"""日志脱敏与落盘测试."""

from migration_assistant import logging_setup
from migration_assistant.logging_setup import sanitize


def test_sensitive_filter_redacts_passwords_tokens_and_signed_urls():
    text = ("password=secret Authorization: Bearer tok "
            "https://oss.example/file?X-Amz-Signature=deadbeef&token=xyz")
    result = sanitize(text)
    assert "secret" not in result
    assert "deadbeef" not in result
    assert "xyz" not in result
    assert "password=[REDACTED]" in result
    assert "[REDACTED]" in result


def test_log_actually_reaches_file(tmp_path):
    """日志必须真的落盘 —— 之前只配了 handler、没人写, 文件永远是 0 字节."""
    logger = logging_setup.configure_logging(tmp_path, logger_name="test.file")
    logger.error("hello from test")
    logs = list((tmp_path / "logs").glob("assistant-*.log"))
    assert len(logs) == 1
    assert "hello from test" in logs[0].read_text(encoding="utf-8")


def test_daily_handler_switches_file_without_rename(tmp_path, monkeypatch):
    """跨天只换文件名, 不 rename 旧文件 (Windows 上 rename 会被占用打断)."""
    from datetime import date

    class FakeDate(date):
        _today = date(2026, 10, 22)

        @classmethod
        def today(cls):
            return cls._today

    monkeypatch.setattr(logging_setup, "date", FakeDate)
    logger = logging_setup.configure_logging(tmp_path, logger_name="test.rotate")
    logger.error("day one")
    handler = logger.handlers[0]
    first = handler.baseFilename

    FakeDate._today = date(2026, 10, 23)          # 模拟跨天
    logger.error("day two")

    logs = sorted((tmp_path / "logs").glob("assistant-*.log"))
    assert len(logs) == 2
    assert handler.baseFilename != first
    assert first.endswith("assistant-2026-10-22.log")
    assert "day one" in open(first, encoding="utf-8").read()
    assert "day two" in open(handler.baseFilename, encoding="utf-8").read()

