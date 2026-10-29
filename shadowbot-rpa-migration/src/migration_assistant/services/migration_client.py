"""账号作用域的迁移客户端: 登录、获取上传地址、上传、创建. 与抓包还原的请求语义完全一致.

网络容错策略:
- 传输层异常按类型判定是否重试 (DNS 抖动 / TLS 被打断 / 连接重置 / 超时 / 5xx / 429 都重试)
- 4xx 认证或请求错误不重试, 重试没有意义且拖慢反馈
- 每次失败都写日志 (异常链 + DNS 复核 + 代理配置), 界面通过 on_log 看到重试进度
- create 一律不重试: 重复创建会在目标账号里留下同名应用
"""
import base64
import logging
import time
from typing import Callable

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from migration_assistant import config
from migration_assistant.errors import MigrationError
from migration_assistant.models import PackagePayload, TaskStage
from migration_assistant.services import netutil

logger = logging.getLogger("migration_assistant.client")


class MigrationClient:
    def __init__(self, session: requests.Session | None = None,
                 on_log: Callable[[str], None] | None = None):
        self.session = session or requests.Session()
        if isinstance(self.session, requests.Session):
            self.session.headers.update({"User-Agent": config.USER_AGENT})
        self.access_token: str | None = None
        # 重试进度回调 (界面显示「第 2/3 次尝试」); 不传则静默
        self.on_log = on_log

    # ---------- 通用请求 ----------
    def _notify(self, text: str) -> None:
        if self.on_log is None:
            return
        try:
            self.on_log(text)
        except Exception:  # noqa: BLE001 - 界面回调异常不能影响迁移
            pass

    def _execute(self, *, stage: str, method: str, url: str,
                 send: Callable[[], requests.Response],
                 error_builder: Callable[[netutil.NetIssue, BaseException | None, int],
                                         MigrationError],
                 attempts: int = 1,
                 delays: tuple[float, ...] = config.RETRY_DELAYS) -> requests.Response:
        """发请求; 可恢复的失败按策略重试, 最终失败交给 error_builder 转成 MigrationError."""
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                resp = send()
            except requests.RequestException as exc:
                elapsed = time.monotonic() - started
                issue = netutil.classify(exc)
                netutil.log_failure(stage=stage, method=method, url=url, exc=exc,
                                    issue=issue, elapsed=elapsed,
                                    attempt=attempt, attempts=attempts)
                if issue.retryable and attempt < attempts:
                    self._sleep_before_retry(stage, issue, attempt, attempts, delays)
                    continue
                raise error_builder(issue, exc, attempt)

            elapsed = time.monotonic() - started
            if resp.status_code >= 400:
                issue = netutil.http_issue(resp.status_code)
                netutil.log_failure(stage=stage, method=method, url=url, exc=None,
                                    issue=issue, elapsed=elapsed,
                                    attempt=attempt, attempts=attempts)
                if issue.retryable and attempt < attempts:
                    self._sleep_before_retry(stage, issue, attempt, attempts, delays)
                    continue
                raise error_builder(issue, None, attempt)
            return resp

        raise AssertionError("unreachable")  # pragma: no cover - 循环必然 return 或 raise

    def _sleep_before_retry(self, stage: str, issue: netutil.NetIssue,
                            attempt: int, attempts: int,
                            delays: tuple[float, ...]) -> None:
        delay = delays[min(attempt - 1, len(delays) - 1)]
        self._notify(f"{stage}失败：{issue.reason}，{delay:g} 秒后重试"
                     f"（第 {attempt + 1}/{attempts} 次）")
        time.sleep(delay)

    @staticmethod
    def _retried(attempt: int) -> str:
        return f"，已重试 {attempt - 1} 次" if attempt > 1 else ""

    # ---------- 错误映射 ----------
    def _login_error(self, issue: netutil.NetIssue,
                     exc: BaseException | None, attempt: int) -> MigrationError:
        tried = self._retried(attempt)
        if issue.code in ("net_connect_timeout", "net_read_timeout"):
            return MigrationError(
                "login_timeout", f"登录超时（{issue.reason}{tried}），请检查网络后重试")
        if issue.code in ("http_auth", "http_bad_request"):
            return MigrationError("login_failed", "账号或密码错误")
        if issue.code.startswith("http_"):
            return MigrationError("login_network", f"登录失败：{issue.reason}{tried}")
        return MigrationError("login_network", f"无法登录：{issue.reason}{tried}")

    def _assign_error(self, issue: netutil.NetIssue,
                      exc: BaseException | None, attempt: int) -> MigrationError:
        tried = self._retried(attempt)
        if issue.code in ("net_connect_timeout", "net_read_timeout"):
            return MigrationError("assign_timeout", f"获取上传地址超时（{issue.reason}{tried}）")
        return MigrationError("assign_failed", f"获取上传地址失败：{issue.reason}{tried}")

    def _upload_error(self, issue: netutil.NetIssue,
                      exc: BaseException | None, attempt: int) -> MigrationError:
        tried = self._retried(attempt)
        if issue.code in ("net_connect_timeout", "net_read_timeout"):
            return MigrationError("upload_timeout", f"上传超时（{issue.reason}{tried}）")
        return MigrationError("upload_network", f"上传失败：{issue.reason}{tried}")

    def _create_error(self, issue: netutil.NetIssue,
                      exc: BaseException | None, attempt: int) -> MigrationError:
        return MigrationError(
            "create_result_unknown",
            f"服务器结果未知（{issue.reason}），请先在目标账号检查是否已创建，再决定是否重试",
            result_unknown=True)

    # ---------- 登录 ----------
    def _encrypt_password(self, password: str) -> str:
        pub = serialization.load_pem_public_key(config.RSA_PUBLIC_KEY.encode())
        encrypted = pub.encrypt(password.encode(), padding.PKCS1v15())
        return base64.b64encode(encrypted).decode()

    def login(self, username: str, password: str) -> None:
        basic = base64.b64encode(config.CLIENT_ID.encode()).decode()
        try:
            resp = self._execute(
                stage="登录", method="POST", url=config.OAUTH_URL,
                send=lambda: self.session.post(
                    config.OAUTH_URL,
                    headers={"Authorization": f"basic {basic}",
                             "Content-Type": "application/x-www-form-urlencoded; Charset=UTF-8"},
                    data={"username": username,
                          "password": self._encrypt_password(password),
                          "grant_type": "password",
                          "scope": "all",
                          "crypt": "metal"},
                    timeout=(config.CONNECT_TIMEOUT, config.LOGIN_TIMEOUT)),
                error_builder=self._login_error,
                attempts=config.LOGIN_ATTEMPTS)
            body = resp.json()
        except MigrationError:
            raise
        except ValueError:
            raise MigrationError("login_response", "登录响应异常")
        except Exception as exc:
            # 兜底: 任何未预期的异常都要转成 MigrationError, 否则会冲出任务循环、
            # 把工作线程打死, 界面上就永久停在"正在登录"。
            raise MigrationError("login_error",
                                 f"登录失败: {type(exc).__name__}: {exc}") from exc
        if not body.get("access_token"):
            netutil.log_business_failure(
                stage="登录", url=config.OAUTH_URL,
                detail=f"响应中没有 access_token, 摘要: {str(body)[:300]}")
            raise MigrationError("login_failed", "账号或密码错误")
        self.access_token = body["access_token"]

    # ---------- 上传 ----------
    def _headers(self):
        return {"Authorization": f"bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8",
                "Xybot-Client-RequestId": config.REQUEST_ID}

    def _assign_url(self, app_id: str, is_bot: str) -> dict:
        resp = self._execute(
            stage="获取上传地址", method="POST", url=config.ASSIGN_UPLOAD_URL,
            send=lambda: self.session.post(
                config.ASSIGN_UPLOAD_URL, headers=self._headers(),
                json={"appId": app_id, "appType": "app", "version": "", "isBot": is_bot},
                timeout=(config.CONNECT_TIMEOUT, config.API_TIMEOUT)),
            error_builder=self._assign_error,
            attempts=config.API_ATTEMPTS)
        try:
            body = resp.json()
        except ValueError:
            raise MigrationError("assign_failed", "获取上传地址失败：响应不是合法 JSON")
        if not body.get("success") or not body.get("data"):
            netutil.log_business_failure(
                stage="获取上传地址", url=config.ASSIGN_UPLOAD_URL,
                detail=f"isBot={is_bot} msg={body.get('msg')!r} "
                       f"响应摘要: {str(body)[:300]}")
            raise MigrationError("assign_business", body.get("msg") or "获取上传地址被拒绝")
        return body["data"]

    def _put(self, upload_url: str, data: bytes, label: str = "上传") -> None:
        # 预签名 URL 在对象存储域, 弱网下单次 PUT 可能长时间无收包,
        # 传输间隔单独放宽到 UPLOAD_TIMEOUT, 连接仍用统一的 CONNECT_TIMEOUT.
        resp = self._execute(
            stage=label, method="PUT", url=upload_url,
            send=lambda: self.session.put(
                upload_url, data=data,
                timeout=(config.CONNECT_TIMEOUT, config.UPLOAD_TIMEOUT)),
            error_builder=self._upload_error,
            attempts=config.UPLOAD_ATTEMPTS)
        if resp.status_code not in (200, 204):
            raise MigrationError("upload_failed", f"上传失败 (HTTP {resp.status_code})")

    def upload(self, new_uuid: str, payload: PackagePayload,
               new_name: str, package: dict,
               on_stage: Callable[[TaskStage], None],
               should_abort: Callable[[], bool] | None = None) -> None:
        """new_uuid: 新生成的 uuid (与 package.json 写入的一致, 原软件行为).

        抓包还原: assignUploadUrl 与 create 的 appId 都用新 uuid,
        而不是本地应用 uuid.

        should_abort: 每个阶段开始前调用, 返回 True 表示用户在等待期间点了「停止迁移」,
        抛 MigrationError("cancelled") 让调用方把该任务标记为已取消。
        """
        if not self.access_token:
            raise MigrationError("not_logged_in", "尚未登录目标账号")

        def _gate() -> None:
            if should_abort is not None and should_abort():
                raise MigrationError("cancelled", "已停止（取消）")

        _gate()
        on_stage(TaskStage.ASSIGNING_BOT_URL)
        bot_up = self._assign_url(new_uuid, "true")

        _gate()
        on_stage(TaskStage.UPLOADING_BOT)
        self._put(bot_up["uploadUrl"], payload.bot_bytes, "上传 package.bot")

        _gate()
        on_stage(TaskStage.ASSIGNING_JSON_URL)
        json_up = self._assign_url(new_uuid, "false")

        _gate()
        on_stage(TaskStage.UPLOADING_JSON)
        self._put(json_up["uploadUrl"], payload.json_bytes, "上传 package.json")

        _gate()
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
        # attempts=1: 创建不可重试 (会在目标账号留下重复应用)
        resp = self._execute(
            stage="创建应用", method="POST", url=config.CREATE_URL,
            send=lambda: self.session.post(
                config.CREATE_URL, headers=self._headers(), json=body,
                timeout=(config.CONNECT_TIMEOUT, config.API_TIMEOUT)),
            error_builder=self._create_error,
            attempts=1)
        try:
            result = resp.json()
        except ValueError:
            raise self._create_error(
                netutil.NetIssue("bad_json", "创建响应不是合法 JSON", False), None, 1)
        if not result.get("success"):
            netutil.log_business_failure(
                stage="创建应用", url=config.CREATE_URL,
                detail=f"msg={result.get('msg')!r} 响应摘要: {str(result)[:300]}")
            raise MigrationError("create_business", result.get("msg") or "创建云端应用被拒绝")
