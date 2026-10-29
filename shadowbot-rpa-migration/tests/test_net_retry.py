"""网络容错测试: 重试策略 / 异常分类 / 日志可见性 (无真实网络)."""
import logging
import socket

import pytest
import requests

from migration_assistant.errors import MigrationError
from migration_assistant.services import netutil
from migration_assistant.services.migration_client import MigrationClient
from tests.fake_http import FakeResponse, FakeSession


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """重试间隔在测试里不能真睡."""
    import migration_assistant.services.migration_client as client_mod
    monkeypatch.setattr(client_mod.time, "sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    """失败日志里的 DNS 复核用桩, 避免测试依赖真实 DNS."""
    monkeypatch.setattr(netutil, "dns_probe",
                        lambda host, port=443, timeout=5.0: netutil.DnsProbe(
                            host, 0.01, ("1.2.3.4",), None))


def _dns_exc() -> requests.ConnectionError:
    try:
        raise socket.gaierror(11001, "getaddrinfo failed")
    except socket.gaierror as cause:
        err = requests.ConnectionError(
            "HTTPSConnectionPool(host='api.yingdao.com', port=443): "
            "Max retries exceeded (Caused by NewConnectionError)")
        err.__cause__ = cause
        return err


def _reset_exc() -> requests.ConnectionError:
    err = requests.ConnectionError(
        'Connection aborted. RemoteDisconnected: Remote end closed connection')
    err.__cause__ = ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接")
    return err


class FlakySession(FakeSession):
    """前 N 次请求抛指定异常, 之后按预设正常返回."""

    def __init__(self, failures: int, exc_factory):
        super().__init__()
        self.failures = failures
        self.exc_factory = exc_factory
        self.attempts = 0

    def post(self, url, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise self.exc_factory()
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.post_responses.pop(0) if self.post_responses else FakeResponse({})


# ---------- 重试策略 ----------

def test_login_retries_transient_dns_error_and_succeeds():
    session = FlakySession(failures=2, exc_factory=_dns_exc)
    session.post_responses.append(FakeResponse({"access_token": "tok-ok"}))
    client = MigrationClient(session)

    client.login("u", "p")

    assert session.attempts == 3          # 两次失败 + 一次成功
    assert client.access_token == "tok-ok"


def test_login_gives_up_after_configured_attempts():
    session = FlakySession(failures=99, exc_factory=_dns_exc)
    client = MigrationClient(session)
    with pytest.raises(MigrationError) as info:
        client.login("u", "p")
    assert info.value.code == "login_network"
    assert "域名解析" in info.value.user_message
    assert "已重试 2 次" in info.value.user_message
    assert session.attempts == 3


def test_login_does_not_retry_on_bad_credentials():
    session = FakeSession()
    session.post_responses.append(FakeResponse({}, status_code=401))
    client = MigrationClient(session)
    with pytest.raises(MigrationError) as info:
        client.login("u", "p")
    assert info.value.code == "login_failed"
    assert info.value.user_message == "账号或密码错误"
    assert len(session.calls) == 1        # 401 不重试


def test_login_retries_on_server_error_then_succeeds():
    session = FakeSession()
    session.post_responses.append(FakeResponse({}, status_code=503))
    session.post_responses.append(FakeResponse({"access_token": "t"}))
    client = MigrationClient(session)
    client.login("u", "p")
    assert len(session.calls) == 2


def test_assign_url_retries_and_reports_reason():
    session = FlakySession(failures=99, exc_factory=_reset_exc)
    client = MigrationClient(session)
    client.access_token = "tok"
    with pytest.raises(MigrationError) as info:
        client._assign_url("uuid", "true")
    assert info.value.code == "assign_failed"
    assert "对端重置" in info.value.user_message
    assert session.attempts == 3


def test_create_is_never_retried(monkeypatch):
    session = FlakySession(failures=99, exc_factory=_reset_exc)
    client = MigrationClient(session)
    client.access_token = "tok"
    with pytest.raises(MigrationError) as info:
        client._create("uuid", "name", {}, "md5")
    assert info.value.code == "create_result_unknown"
    assert session.attempts == 1          # 创建不可重试


# ---------- 异常分类 ----------

@pytest.mark.parametrize("exc_factory, expected", [
    (_dns_exc, "net_dns"),
    (_reset_exc, "net_reset"),
    (lambda: requests.ConnectTimeout("boom"), "net_connect_timeout"),
    (lambda: requests.ReadTimeout("boom"), "net_read_timeout"),
    (lambda: requests.exceptions.ProxyError("Tunnel connection failed: 502"), "net_proxy"),
    (lambda: requests.exceptions.SSLError("certificate verify failed"), "net_tls"),
])
def test_classify_maps_transport_failures(exc_factory, expected):
    assert netutil.classify(exc_factory()).code == expected


def test_http_issue_marks_5xx_retryable_and_4xx_not():
    assert netutil.http_issue(503).retryable is True
    assert netutil.http_issue(429).retryable is True
    assert netutil.http_issue(401).retryable is False
    assert netutil.http_issue(400).retryable is False


# ---------- 日志可见性 ----------

def test_failure_writes_structured_log(caplog):
    """失败必须落到日志里 —— 之前日志文件一直是 0 字节, 根因无从定位."""
    caplog.set_level(logging.ERROR, logger="migration_assistant.net")
    netutil.log_failure(stage="登录", method="POST", url="https://api.yingdao.com/oauth/token",
                        exc=_dns_exc(), issue=netutil.classify(_dns_exc()),
                        elapsed=12.3, attempt=3, attempts=3)
    text = caplog.text
    assert "[登录]" in text
    assert "12.30s" in text
    assert "net_dns" in text
    assert "gaierror" in text          # 异常链展开后能看到真凶
    assert "DNS 复核" in text
    assert "代理" in text


def test_log_redacts_password_and_signature(caplog):
    caplog.set_level(logging.ERROR, logger="migration_assistant.net")
    exc = requests.ConnectionError(
        "failed url: https://x/up?Expires=1&Signature=SECRET&token=SECRET2 "
        "password=PLAIN")
    netutil.log_failure(stage="上传", method="PUT", url="https://oss/x",
                        exc=exc, issue=netutil.classify(exc),
                        elapsed=1.0, attempt=1, attempts=1)
    assert "SECRET2" not in caplog.text
    assert "PLAIN" not in caplog.text
