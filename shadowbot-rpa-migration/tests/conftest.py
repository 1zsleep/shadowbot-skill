"""共享 fixtures: 临时应用树、SQLite."""
import json
from pathlib import Path

import pytest

from migration_assistant.models import AppInfo
from migration_assistant.storage.database import Database
from migration_assistant.storage.account_repository import AccountRepository


@pytest.fixture
def sample_app(tmp_path: Path) -> AppInfo:
    app_dir = tmp_path / "u1" / "apps" / "a1" / "xbot_robot"
    app_dir.mkdir(parents=True)
    pkg = {"uuid": "old-uuid", "name": "日报生成", "version": "1",
           "external_dependencies": ["pandas==1.3.5"],
           "internaldependencies": ["shadowbot_text_new==26.1.0"],
           "internalautodependencies": ["shadowbot_text_new"],
           "ipaasDependencies": [], "uia_type": "PC"}
    pkg_path = app_dir / "package.json"
    pkg_path.write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
    (app_dir / "main.py").write_text("# main\n", encoding="utf-8")
    ignored = app_dir / "__pycache__"
    ignored.mkdir()
    (ignored / "main.pyc").write_bytes(b"\x00")
    return AppInfo("日报生成", "u1", app_dir, pkg_path, pkg)


@pytest.fixture
def apps_tree(tmp_path: Path) -> Path:
    users = tmp_path / "users"
    for uid, name in [("u1", "日报生成"), ("u2", "销售同步")]:
        d = users / uid / "apps" / f"app-{uid}" / "xbot_robot"
        d.mkdir(parents=True)
        (d / "package.json").write_text(
            json.dumps({"name": name, "uuid": f"uuid-{uid}"}, ensure_ascii=False),
            encoding="utf-8")
        (d / "main.py").write_text("# x", encoding="utf-8")
    bad = users / "u3" / "apps" / "bad" / "xbot_robot"
    bad.mkdir(parents=True)
    (bad / "package.json").write_text("{broken", encoding="utf-8")
    return users


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "assistant.db"


@pytest.fixture
def repository(db_path: Path) -> AccountRepository:
    db = Database(db_path)
    conn = db.connect()
    return AccountRepository(conn)
