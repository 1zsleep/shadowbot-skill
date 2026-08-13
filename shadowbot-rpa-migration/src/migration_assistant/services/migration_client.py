"""账号作用域的迁移客户端: 登录、获取上传地址、上传、创建. 与抓包还原的请求语义完全一致."""
import base64
from typing import Callable

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from migration_assistant import config
from migration_assistant.errors import MigrationError
from migration_assistant.models import PackagePayload, TaskStage


class MigrationClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        if isinstance(self.session, requests.Session):
            self.session.headers.update({"User-Agent": config.USER_AGENT})
        self.access_token: str | None = None

    # ---------- 登录 ----------
    def _encrypt_password(self, password: str) -> str:
        pub = serialization.load_pem_public_key(config.RSA_PUBLIC_KEY.encode())
        encrypted = pub.encrypt(password.encode(), padding.PKCS1v15())
        return base64.b64encode(encrypted).decode()

    def login(self, username: str, password: str) -> None:
        basic = base64.b64encode(config.CLIENT_ID.encode()).decode()
        try:
            resp = self.session.post(
                config.OAUTH_URL,
                headers={"Authorization": f"basic {basic}",
                         "Content-Type": "application/x-www-form-urlencoded; Charset=UTF-8"},
                data={"username": username,
                      "password": self._encrypt_password(password),
                      "grant_type": "password",
                      "scope": "all",
                      "crypt": "metal"},
                timeout=config.LOGIN_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
        except requests.Timeout:
            raise MigrationError("login_timeout", "登录超时，请检查网络后重试")
        except requests.ConnectionError:
            raise MigrationError("login_network", "无法连接影刀服务器，请检查网络")
        except requests.HTTPError:
            raise MigrationError("login_failed", "账号或密码错误")
        except ValueError:
            raise MigrationError("login_response", "登录响应异常")
        if not body.get("access_token"):
            raise MigrationError("login_failed", "账号或密码错误")
        self.access_token = body["access_token"]

    # ---------- 上传 ----------
    def _headers(self):
        return {"Authorization": f"bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8",
                "Xybot-Client-RequestId": config.REQUEST_ID}

    def _assign_url(self, app_id: str, is_bot: str) -> dict:
        try:
            resp = self.session.post(
                config.ASSIGN_UPLOAD_URL, headers=self._headers(),
                json={"appId": app_id, "appType": "app", "version": "", "isBot": is_bot},
                timeout=config.API_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
        except requests.Timeout:
            raise MigrationError("assign_timeout", "获取上传地址超时")
        except (requests.ConnectionError, requests.HTTPError):
            raise MigrationError("assign_failed", "获取上传地址失败")
        if not body.get("success") or not body.get("data"):
            raise MigrationError("assign_business", body.get("msg") or "获取上传地址被拒绝")
        return body["data"]

    def _put(self, upload_url: str, data: bytes) -> None:
        try:
            resp = self.session.put(upload_url, data=data, timeout=config.UPLOAD_TIMEOUT)
        except requests.Timeout:
            raise MigrationError("upload_timeout", "上传超时")
        except requests.ConnectionError:
            raise MigrationError("upload_network", "上传连接失败")
        if resp.status_code not in (200, 204):
            raise MigrationError("upload_failed", f"上传失败 (HTTP {resp.status_code})")

    def upload(self, new_uuid: str, payload: PackagePayload,
               new_name: str, package: dict,
               on_stage: Callable[[TaskStage], None]) -> None:
        """new_uuid: 新生成的 uuid (与 package.json 写入的一致, 原软件行为).

        抓包还原: assignUploadUrl 与 create 的 appId 都用新 uuid,
        而不是本地应用 uuid.
        """
        if not self.access_token:
            raise MigrationError("not_logged_in", "尚未登录目标账号")

        on_stage(TaskStage.ASSIGNING_BOT_URL)
        bot_up = self._assign_url(new_uuid, "true")

        on_stage(TaskStage.UPLOADING_BOT)
        self._put(bot_up["uploadUrl"], payload.bot_bytes)

        on_stage(TaskStage.ASSIGNING_JSON_URL)
        json_up = self._assign_url(new_uuid, "false")

        on_stage(TaskStage.UPLOADING_JSON)
        self._put(json_up["uploadUrl"], payload.json_bytes)

        on_stage(TaskStage.CREATING)
        self._create(new_uuid, new_name, package, json_up["fileKeyMd5"])

        on_stage(TaskStage.COMPLETE)

    def _create(self, app_id: str, new_name: str, package: dict, package_md5: str) -> None:
        body = {
            "appId": app_id,
            "appPackage": {
                "activities": [],
                "appFlowParamList": [],
                "appIcon": "",
                "appType": "app",
                "customItems": {
                    "gifUrl": "", "imageName": "", "imageUrl": "",
                    "uiaType": package.get("uia_type", "PC"), "videoUrl": ""},
                "description": package.get("description") or "",
                "elementLibraryCodes": [],
                "enableViewSource": "false",
                "externalDependencies": package.get("external_dependencies") or [],
                "instruction": package.get("instruction") or "",
                "internalDependencies": package.get("internaldependencies") or [],
                "internalautodependencies": package.get("internalautodependencies") or [],
                "ipaasDependencies": package.get("ipaasDependencies") or [],
                "name": new_name,
                "packageCode": "",
                "statistics": {"blockCount": 1, "flowCount": 1,
                               "magicBlockCount": 0, "sourceLineCount": 0},
                "uiTags": "",
                "uiaType": package.get("uia_type", "PC"),
                "videoUrl": "",
            },
            "elementLibraryStatus": 0,
            "groupId": "",
            "packageMd5": package_md5,
        }
        try:
            resp = self.session.post(config.CREATE_URL, headers=self._headers(),
                                     json=body, timeout=config.API_TIMEOUT)
        except requests.Timeout:
            raise MigrationError("create_result_unknown",
                                 "服务器结果未知，请先在目标账号检查是否已创建，再决定是否重试",
                                 result_unknown=True)
        except requests.ConnectionError:
            raise MigrationError("create_result_unknown",
                                 "服务器结果未知，请先在目标账号检查是否已创建，再决定是否重试",
                                 result_unknown=True)
        try:
            resp.raise_for_status()
            result = resp.json()
        except (requests.HTTPError, ValueError):
            raise MigrationError("create_result_unknown",
                                 "服务器结果未知，请先在目标账号检查是否已创建，再决定是否重试",
                                 result_unknown=True)
        if not result.get("success"):
            raise MigrationError("create_business", result.get("msg") or "创建云端应用被拒绝")
