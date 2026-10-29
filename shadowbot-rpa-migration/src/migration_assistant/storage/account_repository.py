"""账号仓库 CRUD."""
from datetime import datetime, timezone

from migration_assistant.models import Account


class AccountRepository:
    def __init__(self, conn):
        self.conn = conn

    def list(self) -> list[Account]:
        rows = self.conn.execute(
            "SELECT id, username, validation_state, last_verified_at "
            "FROM accounts ORDER BY username").fetchall()
        return [Account(r["id"], r["username"], r["validation_state"],
                        r["last_verified_at"]) for r in rows]

    def get(self, account_id: int) -> Account | None:
        row = self.conn.execute(
            "SELECT id, username, validation_state, last_verified_at "
            "FROM accounts WHERE id=?", (account_id,)).fetchone()
        if row is None:
            return None
        return Account(row["id"], row["username"], row["validation_state"],
                       row["last_verified_at"])

    def get_secret(self, account_id: int) -> bytes | None:
        row = self.conn.execute(
            "SELECT password_blob FROM accounts WHERE id=?", (account_id,)).fetchone()
        return bytes(row["password_blob"]) if row else None

    def upsert_verified(self, username: str, encrypted_password: bytes,
                        verified_at: str) -> Account:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO accounts (username, password_blob, validation_state,
                                         last_verified_at, created_at, updated_at)
                   VALUES (?, ?, 'verified', ?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET
                       password_blob=excluded.password_blob,
                       validation_state='verified',
                       last_verified_at=excluded.last_verified_at,
                       updated_at=excluded.updated_at""",
                (username, encrypted_password, verified_at, now, now))
            row = self.conn.execute(
                "SELECT id, username, validation_state, last_verified_at "
                "FROM accounts WHERE id=?", (cur.lastrowid,)).fetchone()
            # ON CONFLICT UPDATE 时 lastrowid 可能不准确, 用 username 再查一次
            row = self.conn.execute(
                "SELECT id, username, validation_state, last_verified_at "
                "FROM accounts WHERE username=?", (username,)).fetchone()
        return Account(row["id"], row["username"], row["validation_state"],
                       row["last_verified_at"])

    def delete(self, account_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
