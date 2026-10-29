"""工作线程测试: 无论 runner 怎么炸, completed 都必须发出去.

否则界面会永久停在"正在迁移/正在停止", 开始按钮也一直禁用 —— 这是实测踩到的坑。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from migration_assistant.workers import MigrationWorker  # noqa: E402


class BoomRunner:
    def __init__(self, exc):
        self.callbacks = None
        self.exc = exc

    def run(self, tasks, app_by_id, account_by_id):
        raise self.exc

    def request_stop(self):
        pass


def test_completed_is_emitted_even_if_runner_raises(qapp):
    worker = MigrationWorker(BoomRunner(RuntimeError("boom")), [], {}, {})
    done, logs = [], []
    worker.completed.connect(done.append)
    worker.log_line.connect(logs.append)
    worker.run()
    assert done == [[]]                       # 一定收到 completed
    assert any("boom" in line for line in logs)   # 异常原因要能看到


def test_completed_carries_results_on_success(qapp):
    class OkRunner(BoomRunner):
        def run(self, tasks, app_by_id, account_by_id):
            return ["r1", "r2"]

    worker = MigrationWorker(OkRunner(None), [], {}, {})
    done = []
    worker.completed.connect(done.append)
    worker.run()
    assert done == [["r1", "r2"]]
