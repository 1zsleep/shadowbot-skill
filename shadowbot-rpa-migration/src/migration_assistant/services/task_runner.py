"""串行任务队列: 账号优先, 应用次之; 失败继续; 停止取消未开始; 失败重试."""
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from migration_assistant.errors import MigrationError
from migration_assistant.models import (STAGE_LABELS, AppInfo, Account,
                                        MigrationTask, TaskResult, TaskStage,
                                        TaskStatus)
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
        # 注意: 这里**不能**重置 _stop_requested. 工作线程真正进入 run() 之前用户就可能点了
        # 「停止迁移」, 重置会把那个请求悄悄吞掉, 界面就卡在"停止迁移"上了。
        if self._stop_requested:
            self.callbacks.on_log("已收到停止请求，未开始的任务将被取消")
        self._executed = 0
        results: list[TaskResult] = []
        total = len(tasks)

        # 按账号分组, 每组登录一次
        account_groups: dict[int, list[MigrationTask]] = {}
        for t in tasks:
            account_groups.setdefault(t.account_id, []).append(t)

        started = time.perf_counter()
        try:
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
                    # 把重试进度接到日志面板, 弱网重试时才看得到"还在动"
                    if hasattr(client, "on_log"):
                        client.on_log = self.callbacks.on_log
                    self.callbacks.on_log(f"正在登录目标账号 {account.username}")
                    login_at = time.perf_counter()
                    client.login(account.username, password)
                    self.callbacks.on_log(
                        f"目标账号 {account.username} 登录成功 "
                        f"({time.perf_counter() - login_at:.1f}s)")
                except MigrationError as e:
                    self._fail_group(group, e.code, e.user_message, results)
                    continue
                except Exception as e:
                    # 未预期异常不能冲出任务循环 (会打死工作线程 -> 界面永久卡住)
                    self.callbacks.on_log(f"登录异常: {type(e).__name__}: {e}")
                    self._fail_group(group, "unexpected",
                                     f"登录时发生未知错误: {type(e).__name__}", results)
                    continue

                for t in group:
                    if self._stop_requested:
                        self._cancel_remaining(tasks, results)
                        break
                    self._executed += 1
                    results.append(self._run_one(t, app_by_id, client,
                                                 self._executed, total))
                self._reset_errors(group)
        except Exception as e:      # 兜底: 保证 run() 一定返回, 界面才不会卡死
            self.callbacks.on_log(f"迁移中断: {type(e).__name__}: {e}")
            self._cancel_remaining(tasks, results)
        finally:
            elapsed = time.perf_counter() - started
            self.callbacks.on_log(
                f"迁移结束, 总用时 {elapsed:.1f}s "
                f"({elapsed // 60:.0f} 分 {elapsed % 60:.0f} 秒)")

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
            payload = self.package_builder.build(app, new_uuid, new_name,
                                                 on_inline=self._log_inline)

            # create 请求要用打包时改写过的 package (内联自定义指令集会改依赖声明),
            # 不能用扫描结果里那份原始 package, 否则服务端仍按旧依赖判定并拒绝。
            # should_abort: 每个阶段开始前检查停止请求, 这样点了停止不必等整条链路跑完。
            client.upload(new_uuid, payload, new_name,
                          payload.package or app.package, self._stage_cb(task),
                          should_abort=lambda: self._stop_requested)

            task.status = TaskStatus.SUCCEEDED
            task.stage = TaskStage.COMPLETE
        except MigrationError as e:
            if e.code == "cancelled":
                task.status = TaskStatus.CANCELLED
            else:
                task.status = TaskStatus.FAILED
                task.error_code = e.code
                task.error_message = e.user_message
                if e.code.startswith("create_"):
                    deps = (payload.package or {}).get("internaldependencies") or []
                    self.callbacks.on_log(
                        "  该应用仍声明的指令集依赖: " + (", ".join(deps) or "(无)")
                        + "；若报「高级指令集」，就是其中之一仍被服务端判为受限")
        except Exception as e:  # 未知异常 -> 通用提示
            task.status = TaskStatus.FAILED
            task.error_code = "unexpected"
            task.error_message = f"发生未知错误: {type(e).__name__}"

        result = TaskResult(task_id=task.task_id, status=task.status,
                            stage=task.stage, error_code=task.error_code,
                            error_message=task.error_message)
        self.callbacks.on_task_finished(result)
        return result

    def _log_inline(self, report) -> None:
        """汇报自定义指令集内联结果 (社区版/基础账号不允许使用第三方指令集)."""
        for item in report.converted:
            self.callbacks.on_log(f"  自定义指令内联为应用流程: {item}")
        if report.dropped_deps:
            self.callbacks.on_log("  已移除自定义指令集依赖: " + ", ".join(report.dropped_deps))
        for name in report.skipped:
            self.callbacks.on_log(f"  自定义指令集 {name} 无法自动转换, 保持原样")
        for name in report.kept:
            self.callbacks.on_log(f"  第三方指令集 {name} 版本较低(pv<=7), 社区版可用, 无需处理")
        for warning in report.warnings:
            self.callbacks.on_log(f"  [提示] {warning}")

    def _stage_cb(self, task):
        """阶段回调: 顺带记录每个阶段耗时, 方便定位"卡在哪一步"."""
        started = time.perf_counter()
        last = {"stage": None, "at": started}

        def cb(stage: TaskStage):
            now = time.perf_counter()
            if last["stage"] is not None and stage != last["stage"]:
                self.callbacks.on_log(
                    f"    {STAGE_LABELS.get(last['stage'], last['stage'])} "
                    f"用时 {now - last['at']:.1f}s")
            last["stage"] = stage
            last["at"] = now
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
        cancelled = 0
        for t in tasks:
            if t.status is TaskStatus.PENDING:
                t.status = TaskStatus.CANCELLED
                results.append(TaskResult(task_id=t.task_id,
                                          status=TaskStatus.CANCELLED,
                                          stage=t.stage))
                cancelled += 1
                self.callbacks.on_task_finished(results[-1])
        if cancelled:
            self.callbacks.on_log(f"已停止：{cancelled} 个未开始的任务已取消")

    @staticmethod
    def _reset_errors(group):
        for t in group:
            if t.status is TaskStatus.SUCCEEDED:
                t.error_code = None
                t.error_message = None
