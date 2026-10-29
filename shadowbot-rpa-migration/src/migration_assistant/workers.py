"""Qt 后台工作线程: 账号验证与迁移. 不导入任何 UI 控件."""
from PySide6.QtCore import QObject, Signal

from migration_assistant.errors import MigrationError
from migration_assistant.models import (MigrationTask, TaskResult, TaskStage)
from migration_assistant.services.task_runner import RunnerCallbacks


class AccountValidationWorker(QObject):
    succeeded = Signal(object)  # Account
    failed = Signal(str)
    progress = Signal(str)      # 重试/等待进度, 例如「登录失败：… 2 秒后重试」

    def __init__(self, account_service, username, password):
        super().__init__()
        self.account_service = account_service
        self.username = username
        self.password = password

    def run(self):
        try:
            account = self.account_service.validate_and_save(
                self.username, self.password, on_log=self.progress.emit)
            self.succeeded.emit(account)
        except MigrationError as e:
            self.failed.emit(e.user_message)
        except Exception:
            self.failed.emit("发生未知错误")


class MigrationWorker(QObject):
    task_started = Signal(object, int, int)  # task, attempt, total
    stage_changed = Signal(object, object)   # task, TaskStage
    task_finished = Signal(object)           # TaskResult
    log_line = Signal(str)
    completed = Signal(object)               # list[TaskResult]

    def __init__(self, runner, tasks, app_by_id, account_by_id):
        super().__init__()
        self.runner = runner
        self.tasks = tasks
        self.app_by_id = app_by_id
        self.account_by_id = account_by_id

    def request_stop(self):
        self.runner.request_stop()

    def run(self):
        results = []
        try:
            callbacks = RunnerCallbacks(
                on_task_started=self.task_started.emit,
                on_stage=self.stage_changed.emit,
                on_task_finished=self.task_finished.emit,
                on_log=self.log_line.emit,
            )
            self.runner.callbacks = callbacks
            results = self.runner.run(self.tasks, self.app_by_id, self.account_by_id)
        except Exception as exc:
            # 绝不能静默吃掉: 窗口模式下异常只会打到 stderr(看不见), 界面就会永久停在
            # "正在迁移/正在停止"且开始按钮一直禁用。这里一定要把原因告诉界面, 并保证
            # completed 一定发出去。
            self.log_line.emit(f"迁移异常终止: {type(exc).__name__}: {exc}")
        finally:
            self.completed.emit(results)
