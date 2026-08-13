"""SQLite 数据库: schema v1, 事务."""
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_blob    BLOB NOT NULL,
    validation_state TEXT NOT NULL DEFAULT 'verified',
    last_verified_at TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: 账号验证/迁移在 Qt 后台线程执行,
        # 需要与主线程共享同一个连接
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        with conn:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return conn
