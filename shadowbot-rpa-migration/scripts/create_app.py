#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影刀空应用脚手架 — 从技能自带的 templates/app_template 生成一个完整的新应用目录 (无现成应用时用)

用法:
  python create_app.py --name 测试9 --out <目标apps目录>
  python create_app.py --name 测试9 --out "<apps目录>" --uuid 自定义-uuid

生成的目录结构 = 影刀客户端新建的空应用, 可直接被影刀扫描识别, 也可直接用于迁移 (migrate_app.py)。
生成后: 用 shadowbot-block-to-clipboard 技能构建块写入 .dev/main.flow.json + main.py, 再迁移。
"""
import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "app_template"


def main() -> int:
    ap = argparse.ArgumentParser(description="影刀空应用脚手架")
    ap.add_argument("--name", required=True, help="应用名 (如 测试9)")
    ap.add_argument("--out", default=".", help="输出父目录 (影刀 users/<id>/apps 目录), 默认当前目录")
    ap.add_argument("--uuid", help="应用 uuid (默认自动生成 uuid4)")
    a = ap.parse_args()

    if not TEMPLATE.is_dir():
        print(f"错误: 找不到模板目录 {TEMPLATE}", file=sys.stderr)
        return 1

    new_uuid = a.uuid or str(uuid.uuid4())
    dest = Path(a.out) / new_uuid / "xbot_robot"
    if dest.exists():
        print(f"错误: 目标已存在 {dest}", file=sys.stderr)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE, dest)

    # 填 uuid + name
    pkg_path = dest / "package.json"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    pkg["uuid"] = new_uuid
    pkg["name"] = a.name
    pkg_path.write_text(json.dumps(pkg, ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n",
                        encoding="utf-8", newline="")

    # 重新生成 sigstore (云端 create 不校验, 结构对即可)
    sig = {"timestamp": str(int(time.time())), "protocolType": 1, "order": 3, "flows": None,
           "sign": uuid.uuid4().hex[:32], "signv2": uuid.uuid4().hex[:32]}
    (dest / "package.sigstore").write_text(json.dumps(sig, ensure_ascii=False) + "\r\n",
                                           encoding="utf-8", newline="")

    print(f"✅ 新应用已创建: {dest}")
    print(f"   name={a.name}  uuid={new_uuid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
