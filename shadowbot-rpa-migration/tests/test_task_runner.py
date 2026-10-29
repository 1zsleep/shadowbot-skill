"""任务队列测试: 笛卡尔生成、失败继续、停止、重试."""
from pathlib import Path

from migration_assistant.errors import MigrationError
from migration_assistant.models import (AppInfo, Account, MigrationTask,
                                        PackagePayload, TaskStage, TaskStatus)
from migration_assistant.services.custom_activity_inliner import InlineReport
from migration_assistant.services.task_runner import (TaskRunner, build_tasks,
                                                      failed_tasks)

APPS = [AppInfo("日报", "u1", Path("/a1"), Path("/a1/package.json"), {}),
        AppInfo("销售", "u2", Path("/a2"), Path("/a2/package.json"), {})]
ACCS = [Account(1, "zhang", "verified", "t"), Account(2, "li", "verified", "t")]


class FakeClient:
    def __init__(self, fail_login=False, fail_app_names=()):
        self.fail_login = fail_login
        self.fail_app_names = set(fail_app_names)
        self.usernames = []
        self.seen_packages = []

    def login(self, username, password):
        self.usernames.append(username)
        if self.fail_login:
            raise MigrationError("login_failed", "账号或密码错误")
        self.access_token = "tok"

    def upload(self, new_uuid, payload, new_name, package, on_stage,
               should_abort=None):
        self.seen_packages.append(package)
        # new_name 形如 "原名_云迁_接收于...", 据此判断应用
        if any(name in new_name for name in self.fail_app_names):
            raise MigrationError("upload_failed", "上传失败")
        on_stage(TaskStage.COMPLETE)


class FakePackageBuilder:
    """模拟打包时改写 package.json (内联自定义指令集会去依赖)."""

    def build(self, app, new_uuid, new_name, on_inline=None):
        if on_inline is not None:
            on_inline(InlineReport())
        inlined = dict(app.package or {})
        inlined["internaldependencies"] = []
        return PackagePayload(bot_bytes=b"B", json_bytes=b'{}', package=inlined)


class FakeAccountService:
    def decrypt_password(self, account_id):
        return "p"


def _runner(fail_login=False, fail_app_names=()):
    return TaskRunner(package_builder=FakePackageBuilder(),
                      client_factory=lambda: FakeClient(fail_login, fail_app_names),
                      account_service=FakeAccountService())


def test_build_tasks_is_account_major():
    tasks = build_tasks(APPS, ACCS)
    assert [(t.account_id, t.app_id) for t in tasks] == [
        (1, APPS[0].id), (1, APPS[1].id), (2, APPS[0].id), (2, APPS[1].id)]


def test_single_failure_continues_queue():
    tasks = build_tasks(APPS, ACCS)
    results = _runner(fail_app_names={APPS[0].name}).run(
        tasks, {a.id: a for a in APPS}, {a.id: a for a in ACCS})
    assert [r.status for r in results] == [
        TaskStatus.FAILED, TaskStatus.SUCCEEDED,
        TaskStatus.FAILED, TaskStatus.SUCCEEDED]


def test_login_failure_skips_account_group():
    tasks = build_tasks(APPS, ACCS)
    results = _runner(fail_login=True).run(
        tasks, {a.id: a for a in APPS}, {a.id: a for a in ACCS})
    assert all(r.status is TaskStatus.FAILED for r in results)
    assert len(results) == 4


def test_stop_cancels_unstarted_tasks():
    class StopClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.count = 0

        def upload(self, app_id, payload, new_name, package, on_stage,
                   should_abort=None):
            self.count += 1
            if self.count > 1:
                runner.request_stop()
            on_stage(TaskStage.COMPLETE)

    runner = TaskRunner(package_builder=FakePackageBuilder(),
                        client_factory=StopClient,
                        account_service=FakeAccountService())
    tasks = build_tasks(APPS, ACCS)
    results = runner.run(tasks, {a.id: a for a in APPS}, {a.id: a for a in ACCS})
    assert [r.status for r in results] == [
        TaskStatus.SUCCEEDED, TaskStatus.SUCCEEDED,
        TaskStatus.CANCELLED, TaskStatus.CANCELLED]


def test_stop_requested_before_run_is_not_lost():
    """点「停止迁移」可能发生在 worker 线程真正进入 run() 之前, 不能被重置吞掉."""
    runner = TaskRunner(package_builder=FakePackageBuilder(),
                        client_factory=FakeClient,
                        account_service=FakeAccountService())
    runner.request_stop()          # 模拟 run() 之前就点了停止
    tasks = build_tasks(APPS, ACCS)
    results = runner.run(tasks, {a.id: a for a in APPS}, {a.id: a for a in ACCS})
    assert [r.status for r in results] == [TaskStatus.CANCELLED] * 4


def test_upload_uses_inlined_package_not_original():
    """create 请求必须用打包时改写过的 package, 否则服务端仍按旧依赖声明判定."""
    client = FakeClient()
    runner = TaskRunner(package_builder=FakePackageBuilder(),
                        client_factory=lambda: client,
                        account_service=FakeAccountService())
    apps = [AppInfo("日报", "u1", Path("/a1"), Path("/a1/package.json"),
                    {"internaldependencies": ["activity_x==1.0"]})]
    tasks = build_tasks(apps, ACCS[:1])
    runner.run(tasks, {a.id: a for a in apps}, {a.id: a for a in ACCS[:1]})
    assert client.seen_packages[0]["internaldependencies"] == []


def test_failed_tasks_build_retry_with_incremented_attempt():
    f1 = MigrationTask("t1", APPS[0].id, "日报", 1, "zhang",
                       status=TaskStatus.FAILED, attempt=1)
    s1 = MigrationTask("t2", APPS[1].id, "销售", 1, "zhang",
                       status=TaskStatus.SUCCEEDED, attempt=1)
    f2 = MigrationTask("t3", APPS[0].id, "日报", 2, "li",
                       status=TaskStatus.FAILED, attempt=2)
    retries = failed_tasks([f1, s1, f2])
    assert [(t.app_id, t.account_id, t.attempt) for t in retries] == [
        (APPS[0].id, 1, 2), (APPS[0].id, 2, 3)]
    assert retries[0].task_id != f1.task_id
