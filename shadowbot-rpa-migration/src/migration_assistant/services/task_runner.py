"""串行任务队列: 账号优先, 应用次之; 失败继续; 停止取消未开始; 失败重试."""
import uuid
from dataclasses import dataclass, field
from typing import Callable

from migration_assistant.errors import MigrationError
from migration_assistant.models import (AppInfo, Account, MigrationTask,
                                        TaskResult, TaskStage, TaskStatus)
from migration_assistant.services.package_builder import PackageBuilder
from migration_assistant.services.migration_client import MigrationClient


@dataclass(slots=True)
class RunnerCallbacks:
    on_task_started: Callable[[MigrationTask, int, int], None] = lambda *_: None
    on_stage: Callable[[MigrationTask, TaskStage], None] = lambda *_: None
    on_task_finished: Callable[[TaskResult], None] = lambda *_: None
    on_log: Callable[[str], None] = lambda *_: None


def build_tasks(apps: list[AppInfo], accounts: list[Account]) -> list[MigrationTask]:
    """生成 N x M 任务, 顺序: 账号1所有应用 -> 账号2所有应用."""
    tasks = []
    for account in accounts:
        for app in apps:
            tasks.append(MigrationTask(
                task_id=str(uuid.uuid4()),
                app_id=app.id,
                app_name=app.name,
                account_id=account.id,
                account_username=account.username,
            ))
    return tasks


def failed_tasks(tasks: list[MigrationTask]) -> list[MigrationTask]:
    """为所有失败任务生成新的重试任务 (attempt+1, 新 task_id)."""
    retries = []
    for t in tasks:
        if t.status is TaskStatus.FAILED:
            retries.append(MigrationTask(
                task_id=str(uuid.uuid4()),
                app_id=t.app_id,
                app_name=t.app_name,
                account_id=t.account_id,
                account_username=t.account_username,
                attempt=t.attempt + 1,
            ))
    return retries


class TaskRunner:
    def __init__(self, *, package_builder=None, client_factory=None,
                 account_service=None, callbacks: RunnerCallbacks | None = None):
        self.package_builder = package_builder or PackageBuilder()
        self.client_factory = client_factory or (lambda: MigrationClient())
        self.account_service = account_service
        self.callbacks = callbacks or RunnerCallbacks()
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self, tasks: list[MigrationTask],
            app_by_id: dict[str, AppInfo],
            account_by_id: dict[int, Account]) -> list[TaskResult]:
        self._stop_requested = False
        self._executed = 0
        results: list[TaskResult] = []
        total = len(tasks)

        # 按账号分组, 每组登录一次
        account_groups: dict[int, list[MigrationTask]] = {}
        for t in tasks:
            account_groups.setdefault(t.account_id, []).append(t)

        for account_id, group in account_groups.items():
            if self._stop_requested:
                self._cancel_remaining(tasks, results)
                break

            account = account_by_id.get(account_id)
            if account is None:
                self._fail_group(group, "account_missing", "账号不存在", results)
                continue

            # 登录该账号 (密码解密一次)
            try:
                password = self.account_service.decrypt_password(account_id)
                client = self.client_factory()
                self.callbacks.on_log(f"正在登录目标账号 {account.username}")
                client.login(account.username, password)
                self.callbacks.on_log(f"目标账号 {account.username} 登录成功")
            except MigrationError as e:
                self._fail_group(group, e.code, e.user_message, results)
                continue

            for t in group:
                if self._stop_requested:
                    self._cancel_remaining(tasks, results)
                    break
                self._executed += 1
                results.append(self._run_one(t, app_by_id, client, self._executed, total))
            self._reset_errors(group)

        return results

    def _run_one(self, task: MigrationTask, app_by_id, client,
                 index: int, total: int) -> TaskResult:
        task.status = TaskStatus.RUNNING
        task.stage = TaskStage.WAITING
        self.callbacks.on_task_started(task, index, total)
        try:
            app = app_by_id.get(task.app_id)
            if app is None:
                raise MigrationError("app_missing", "本地应用不存在")

            task.stage = TaskStage.PACKAGING
            self.callbacks.on_stage(task, TaskStage.PACKAGING)
            new_uuid = str(uuid.uuid4())
            from datetime import datetime
            from migration_assistant import config
            ts = datetime.now().strftime(config.TIMESTAMP_FORMAT)
            new_name = config.MIGRATED_NAME_TEMPLATE.format(original=app.name, timestamp=ts)
            payload = self.package_builder.build(app, new_uuid, new_name)

            client.upload(new_uuid, payload, new_name, app.package, self._stage_cb(task))

            task.status = TaskStatus.SUCCEEDED
            task.stage = TaskStage.COMPLETE
        except MigrationError as e:
            task.status = TaskStatus.FAILED
            task.error_code = e.code
            task.error_message = e.user_message
        except Exception as e:  # 未知异常 -> 通用提示
            task.status = TaskStatus.FAILED
            task.error_code = "unexpected"
            task.error_message = f"发生未知错误: {type(e).__name__}"

        result = TaskResult(task_id=task.task_id, status=task.status,
                            stage=task.stage, error_code=task.error_code,
                            error_message=task.error_message)
        self.callbacks.on_task_finished(result)
        return result

    def _stage_cb(self, task):
        def cb(stage: TaskStage):
            task.stage = stage
            self.callbacks.on_stage(task, stage)
        return cb

    def _fail_group(self, group, code, message, results):
        for t in group:
            if t.status is not TaskStatus.PENDING:
                continue
            t.status = TaskStatus.FAILED
            t.error_code = code
            t.error_message = message
            results.append(TaskResult(task_id=t.task_id, status=TaskStatus.FAILED,
                                      stage=t.stage, error_code=code,
                                      error_message=message))
            self.callbacks.on_task_finished(results[-1])

    def _cancel_remaining(self, tasks, results):
        for t in tasks:
            if t.status is TaskStatus.PENDING:
                t.status = TaskStatus.CANCELLED
                results.append(TaskResult(task_id=t.task_id,
                                          status=TaskStatus.CANCELLED,
                                          stage=t.stage))
                self.callbacks.on_task_finished(results[-1])

    @staticmethod
    def _reset_errors(group):
        for t in group:
            if t.status is TaskStatus.SUCCEEDED:
                t.error_code = None
                t.error_message = None
