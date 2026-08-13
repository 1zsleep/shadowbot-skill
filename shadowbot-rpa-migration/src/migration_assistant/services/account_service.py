"""账号在线验证, 成功后才加密保存."""
from datetime import datetime, timezone

from migration_assistant import config
from migration_assistant.errors import MigrationError
from migration_assistant.models import Account
from migration_assistant.services.migration_client import MigrationClient


class AccountService:
    def __init__(self, repository, protector, client_factory=None):
        self.repository = repository
        self.protector = protector
        self.client_factory = client_factory or (lambda: MigrationClient())

    def validate_and_save(self, username: str, password: str) -> Account:
        username = username.strip()
        if not username or not password:
            raise MigrationError("empty_credentials", "请输入账号和密码")
        client = self.client_factory()
        client.login(username, password)
        encrypted = self.protector.protect(password)
        verified_at = datetime.now(timezone.utc).isoformat()
        return self.repository.upsert_verified(username, encrypted, verified_at)

    def decrypt_password(self, account_id: int) -> str:
        blob = self.repository.get_secret(account_id)
        if blob is None:
            raise MigrationError("account_missing", "账号不存在")
        return self.protector.unprotect(blob)
