"""账号存储与验证测试."""
import pytest

from migration_assistant.errors import MigrationError
from migration_assistant.services.account_service import AccountService
from migration_assistant.services.migration_client import MigrationClient
from tests.fake_http import FakeResponse, FakeSession


class FakeProtector:
    def protect(self, plaintext: str) -> bytes:
        return f"cipher:{plaintext}".encode()

    def unprotect(self, blob: bytes) -> str:
        return blob.decode().removeprefix("cipher:")


def test_upsert_updates_one_case_insensitive_account(repository):
    first = repository.upsert_verified(
        "User@Example.com", b"cipher-1", "2026-08-05T10:00:00+08:00")
    second = repository.upsert_verified(
        "user@example.com", b"cipher-2", "2026-08-05T11:00:00+08:00")
    assert first.id == second.id
    assert len(repository.list()) == 1
    assert repository.get_secret(first.id) == b"cipher-2"


def test_database_never_contains_plain_password(repository, db_path):
    repository.upsert_verified("user@example.com", b"encrypted-value",
                               "2026-08-05T10:00:00+08:00")
    assert b"plain-secret" not in db_path.read_bytes()


def test_validate_then_encrypt_then_save(repository):
    def client_factory():
        session = FakeSession()
        session.post_responses.append(FakeResponse({"access_token": "tok"}))
        return MigrationClient(session)

    service = AccountService(repository, FakeProtector(), client_factory)
    account = service.validate_and_save("user@example.com", "secret")
    assert account.username == "user@example.com"
    assert repository.get_secret(account.id) == b"cipher:secret"


def test_login_failure_does_not_replace_existing_secret(repository):
    existing = repository.upsert_verified(
        "user@example.com", b"old", "2026-08-05T10:00:00+08:00")

    class FailingSession(FakeSession):
        def post(self, url, **kwargs):
            self.calls.append({"method": "POST", "url": url, **kwargs})
            raise MigrationError("login_failed", "账号或密码错误")

    service = AccountService(repository, FakeProtector(),
                             lambda: MigrationClient(FailingSession()))
    with pytest.raises(MigrationError):
        service.validate_and_save(existing.username, "wrong")
    assert repository.get_secret(existing.id) == b"old"


@pytest.mark.skipif(__import__("os").name != "nt", reason="DPAPI is Windows-only")
def test_dpapi_round_trip():
    from migration_assistant.storage.credential_protector import WindowsDPAPI
    protector = WindowsDPAPI(b"YingdaoMigrationAssistant:v1")
    encrypted = protector.protect("secret")
    assert encrypted != b"secret"
    assert protector.unprotect(encrypted) == "secret"
