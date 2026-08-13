"""日志: 每日文件 + 敏感信息过滤 (密码/token/Authorization/预签名URL)."""
import logging
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_SENSITIVE_QUERY = re.compile(
    r"(\?[^\s\"]*)(Expires|Signature|OSSAccessKeyId|token|X-Amz-Signature)=")
_QUERY_STRIP = re.compile(r"(\?)[^\\s\"]*")
_AUTH_HEADER = re.compile(r"(Authorization:\s*)(\S+)")
_PASSWORD_FIELD = re.compile(r"(password=)([^&\s\"]+)")


def sanitize(text: str) -> str:
    text = _PASSWORD_FIELD.sub(r"\1[REDACTED]", text)
    text = _AUTH_HEADER.sub(r"\1[REDACTED]", text)
    text = _QUERY_STRIP.sub(r"\1[REDACTED]", text)
    return text


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize(str(record.msg))
        if record.args:
            record.args = tuple(sanitize(str(a)) if isinstance(a, str) else a
                                for a in record.args)
        return True


def configure_logging(data_dir: Path, logger_name: str = "migration_assistant") -> logging.Logger:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = TimedRotatingFileHandler(
        log_dir / "assistant.log", when="midnight", encoding="utf-8",
        backupCount=14)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(SensitiveDataFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
