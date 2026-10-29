"""领域模型测试."""
from pathlib import Path

from migration_assistant.models import (AppInfo, MigrationTask, TaskStage,
                                        TaskStatus)


def test_app_info_identity_uses_resolved_package_path(tmp_path: Path):
    package_path = tmp_path / "app" / "package.json"
    app = AppInfo("日报", "local-user", package_path.parent, package_path, {"name": "日报"})
    assert app.id == str(package_path.resolve())


def test_new_task_starts_pending():
    task = MigrationTask("t-1", "app-1", "日报", 7, "user@example.com")
    assert task.status is TaskStatus.PENDING
    assert task.stage is TaskStage.WAITING
    assert task.attempt == 1
