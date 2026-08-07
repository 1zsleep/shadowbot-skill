#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影刀 RPA 应用迁移脚本 — 自包含实现 (仅依赖 requests + cryptography)

把本地影刀应用上传到云端目标账号: 云端新建应用 (新 uuid + 指定名称, 默认加 _云迁_接收于<时间戳> 后缀)。

用法:
  # 密码从环境变量读 (推荐, 避免进 shell 历史)
  SB_PASS=<密码> python migrate_app.py --app "<app的xbot_robot目录>" --user xxx --name <云端新名>
  # 只登录 + 列云端应用 (验证账号/网络, 不上传)
  SB_PASS=<密码> python migrate_app.py --check --user xxx
  # 密码也可用 --password 传 (会出现在进程参数里, 慎用)

依赖: pip install requests cryptography
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ---- 配置 (逆向自 Xbot Deployer.exe + mitmproxy 抓包, 见 references/captured-api-format.md) ----
OAUTH_URL = "https://api.yingdao.com/oauth/token"
API_BASE = "https://api.winrobot360.com/api/client"
ASSIGN_UPLOAD_URL = f"{API_BASE}/app/file/assignUploadUrl"
CREATE_URL = f"{API_BASE}/app/develop/create"
LIST_URL = f"{API_BASE}/app/develop/list"
CLIENT_ID = "sns:T7svFcIL4foPj1j9"          # basic 认证的 client id
REQUEST_ID = "57214437-d52d-4f1f-a23f-87c3e9b84adb"  # 任意 uuid 可用
USER_AGENT = "Mozilla/4.0 (compatible; MSIE 9.0; Windows NT 6.1)"
RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCte0XfPY9GUpQ3ZasH1kVb
DhRwyRAqWSeyxj290OqFHtyiZ+5SQjrEr79mk0hcZqV03fb5oYf385E3gopS
ERIKxVQyGoloNeDgyLu7rHHWMPo8KPDpUBlpRpHlGMgBNzJZ2BI6p7LvGAhC
oA7XRuetyTlAW6EbSXBpSu1sNGBhkQIDAQAB
-----END PUBLIC KEY-----"""
LOGIN_TIMEOUT = 30
API_TIMEOUT = 60
UPLOAD_TIMEOUT = 300
TIMESTAMP_FORMAT = "%Y年%m月%d日%H时%M分%S秒"


def encrypt_password(password: str) -> str:
    pub = serialization.load_pem_public_key(RSA_PUBLIC_KEY.encode())
    return base64.b64encode(pub.encrypt(password.encode(), padding.PKCS1v15())).decode()


def login(session: requests.Session, username: str, password: str) -> str:
    resp = session.post(
        OAUTH_URL,
        headers={"Authorization": f"basic {base64.b64encode(CLIENT_ID.encode()).decode()}",
                 "Content-Type": "application/x-www-form-urlencoded; Charset=UTF-8"},
        data={"username": username, "password": encrypt_password(password),
              "grant_type": "password", "scope": "all", "crypt": "metal"},
        timeout=LOGIN_TIMEOUT)
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("登录失败: 账号或密码错误")
    return token


def headers(token: str) -> dict:
    return {"Authorization": f"bearer {token}", "Content-Type": "application/json; charset=utf-8",
            "Xybot-Client-RequestId": REQUEST_ID}


def list_apps(session: requests.Session, token: str, name: str = "", size: int = 50) -> list:
    resp = session.post(LIST_URL, headers=headers(token),
                        json={"groupId": None, "name": name, "pageType": 1,
                              "pageDTO": {"page": 1, "size": size}, "sortBy": "4"},
                        timeout=API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get("data") or []
    return data if isinstance(data, list) else []


def assign_url(session: requests.Session, token: str, app_id: str, is_bot: str) -> dict:
    resp = session.post(ASSIGN_UPLOAD_URL, headers=headers(token),
                        json={"appId": app_id, "appType": "app", "version": "", "isBot": is_bot},
                        timeout=API_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success") or not body.get("data"):
        raise RuntimeError(f"获取上传地址被拒绝: {body.get('msg')}")
    return body["data"]


def put_bytes(session: requests.Session, upload_url: str, data: bytes) -> None:
    # ⚠️ 不能带 Content-Type 头, 否则 OSS 403 SignatureDoesNotMatch
    resp = session.put(upload_url, data=data, timeout=UPLOAD_TIMEOUT)
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"上传失败 (HTTP {resp.status_code})")


def build_payload(app_dir: Path, new_uuid: str, new_name: str) -> tuple[bytes, bytes]:
    """zip xbot_robot 目录 (排除 venv/__pycache__, package.json 用改后的) + 改后 package.json 字节"""
    pkg_path = app_dir / "package.json"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8-sig"))
    new_pkg = dict(pkg)
    new_pkg["uuid"] = new_uuid
    new_pkg["name"] = new_name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(app_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(app_dir)
            if "venv" in rel.parts or "__pycache__" in rel.parts:
                continue
            if p.name == "package.json":
                zf.writestr("package.json", json.dumps(new_pkg, ensure_ascii=False))
            else:
                zf.write(p, rel)
    json_bytes = json.dumps(new_pkg, ensure_ascii=False).encode("utf-8")
    return buf.getvalue(), json_bytes


def retry(fn, tries: int = 3, delay: int = 5):
    """api.winrobot360.com 常 DNS/连接抖动, 包重试"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  [重试 {i + 1}/{tries}] {e}")
            time.sleep(delay)
    raise last


def migrate(app_dir: Path, username: str, password: str, new_name: str | None) -> str:
    pkg = json.loads((app_dir / "package.json").read_text(encoding="utf-8-sig"))
    local_name = pkg.get("name", "")
    cloud_name = new_name or f"{local_name}_云迁_接收于{time.strftime(TIMESTAMP_FORMAT)}"
    new_uuid = str(uuid.uuid4())  # 云端用新 uuid (本地 uuid 会在 create 阶段报错)

    print(f"[1/6] 登录 {username} ...")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    token = retry(lambda: login(session, username, password))
    print(f"[2/6] 打包 {local_name} -> {cloud_name} (云端新 uuid {new_uuid})")
    bot_bytes, json_bytes = build_payload(app_dir, new_uuid, cloud_name)

    print("[3/6] 获取上传地址 + 上传 package.bot")
    bot_up = retry(lambda: assign_url(session, token, new_uuid, "true"))
    put_bytes(session, bot_up["uploadUrl"], bot_bytes)

    print("[4/6] 获取上传地址 + 上传 package.json")
    json_up = retry(lambda: assign_url(session, token, new_uuid, "false"))
    put_bytes(session, json_up["uploadUrl"], json_bytes)

    print("[5/6] 创建云端应用")
    create_body = {
        "appId": new_uuid,
        "appPackage": {
            "activities": [], "appFlowParamList": [], "appIcon": "",
            "appType": "app",
            "customItems": {"gifUrl": "", "imageName": "", "imageUrl": "",
                            "uiaType": pkg.get("uia_type", "PC"), "videoUrl": ""},
            "description": pkg.get("description") or "",
            "elementLibraryCodes": [], "enableViewSource": "false",
            "externalDependencies": pkg.get("external_dependencies") or [],
            "instruction": pkg.get("instruction") or "",
            "internalDependencies": pkg.get("internaldependencies") or [],
            "internalautodependencies": pkg.get("internalautodependencies") or [],
            "ipaasDependencies": pkg.get("ipaasDependencies") or [],
            "name": cloud_name, "packageCode": "",
            "statistics": {"blockCount": 1, "flowCount": 1, "magicBlockCount": 0, "sourceLineCount": 0},
            "uiTags": "", "uiaType": pkg.get("uia_type", "PC"), "videoUrl": "",
        },
        "elementLibraryStatus": 0, "groupId": "",
        "packageMd5": json_up["fileKeyMd5"],  # ⚠️ 来自 assignUploadUrl(isBot=false) 响应, 不是文件 md5
    }
    resp = retry(lambda: session.post(CREATE_URL, headers=headers(token),
                                      json=create_body, timeout=API_TIMEOUT), tries=2, delay=8)
    result = resp.json()
    if not result.get("success"):
        raise RuntimeError(f"创建云端应用被拒绝: {result.get('msg')}")

    print("[6/6] 验证云端列表")
    time.sleep(3)
    found = [r for r in retry(lambda: list_apps(session, token, name=cloud_name))
             if str(r.get("appName") or "") == cloud_name]
    if found:
        print(f"✅ 迁移成功: {cloud_name} (appId={found[0].get('appId')})")
        return found[0].get("appId", "")
    raise RuntimeError("创建接口成功但云端列表未见, 请人工核对")


def main() -> int:
    ap = argparse.ArgumentParser(description="影刀 RPA 应用迁移到云端目标账号")
    ap.add_argument("--app", help="本地应用的 xbot_robot 目录 (或 app 目录, 自动找 xbot_robot)")
    ap.add_argument("--user", required=True, help="目标账号 (如 xxx)")
    ap.add_argument("--name", help="云端新名称 (默认: 原名称_云迁_接收于<时间戳>)")
    ap.add_argument("--password", help="账号密码 (优先读环境变量 SB_PASS)")
    ap.add_argument("--check", action="store_true", help="只登录+列云端应用, 不上传")
    a = ap.parse_args()

    password = os.environ.get("SB_PASS") or a.password
    if not password:
        print("错误: 请设置环境变量 SB_PASS 或用 --password", file=sys.stderr)
        return 1

    if a.check:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        token = retry(lambda: login(session, a.user, password))
        apps = retry(lambda: list_apps(session, token, size=100))
        print(f"登录成功 ✅ 云端应用 {len(apps)} 个:")
        for r in apps:
            print(f"  {r.get('appId')}  {r.get('appName')}  {r.get('authority')}")
        return 0

    if not a.app:
        print("错误: 迁移模式需要 --app", file=sys.stderr)
        return 1
    app_dir = Path(a.app)
    if not (app_dir / "package.json").exists():
        app_dir = app_dir / "xbot_robot"
    if not (app_dir / "package.json").exists():
        print(f"错误: 找不到 {app_dir / 'package.json'}", file=sys.stderr)
        return 1
    migrate(app_dir, a.user, password, a.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
