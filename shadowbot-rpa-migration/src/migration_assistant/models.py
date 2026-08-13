"""领域模型: 应用、账号、任务、结果."""
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStage(StrEnum):
    WAITING = "waiting"
    LOGIN = "login"
    PACKAGING = "packaging"
    ASSIGNING_BOT_URL = "assigning_bot_url"
    UPLOADING_BOT = "uploading_bot"
    ASSIGNING_JSON_URL = "assigning_json_url"
    UPLOADING_JSON = "uploading_json"
    CREATING = "creating"
    COMPLETE = "complete"


# 阶段 -> 界面中文
STAGE_LABELS: dict[TaskStage, str] = {
    TaskStage.WAITING: "等待中",
    TaskStage.LOGIN: "登录目标账号",
    TaskStage.PACKAGING: "打包应用",
    TaskStage.ASSIGNING_BOT_URL: "获取 package.bot 上传地址",
    TaskStage.UPLOADING_BOT: "正在上传 package.bot",
    TaskStage.ASSIGNING_JSON_URL: "获取 package.json 上传地址",
    TaskStage.UPLOADING_JSON: "正在上传 package.json",
    TaskStage.CREATING: "正在创建云端应用",
    TaskStage.COMPLETE: "完成",
}


@dataclass(frozen=True, slots=True)
class AppInfo:
    """本地影刀应用."""
    name: str
    source_user: str
    app_dir: Path
    package_path: Path
    package: dict[str, Any]
    # 来源账号: owner_name=账号名(登录名), display_name=显示名
    owner_name: str = ""
    display_name: str = ""

    @property
    def id(self) -> str:
        return str(self.package_path.resolve())


@dataclass(frozen=True, slots=True)
class Account:
    """目标账号 (不含明文密码)."""
    id: int
    username: str
    validation_state: str
    last_verified_at: str


@dataclass(slots=True)
class MigrationTask:
    task_id: str
    app_id: str
    app_name: str
    account_id: int
    account_username: str
    status: TaskStatus = TaskStatus.PENDING
    stage: TaskStage = TaskStage.WAITING
    attempt: int = 1
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    status: TaskStatus
    stage: TaskStage
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    apps: tuple[AppInfo, ...]
    skipped_count: int


@dataclass(frozen=True, slots=True)
class PackagePayload:
    bot_bytes: bytes
    json_bytes: bytes
