"""日志: 按日期分文件 + 敏感信息过滤 (密码/token/Authorization/预签名URL).

刻意不用 TimedRotatingFileHandler: 它跨天靠 rename 旧文件实现轮转, 而 Windows
上只要文件正被本进程或另一个实例打开, rename 就会抛 WinError 32, 结果是
「日志一条都没落盘」。改成每天一个独立文件名, 切天只是换文件句柄, 不 rename。
"""
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

_QUERY_STRIP = re.compile(r"(\?)[^\s\"']*")
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


class DailyFileHandler(logging.FileHandler):
    """按日期命名 (assistant-YYYY-MM-DD.log), 跨天只切换句柄, 不重命名文件."""

    def __init__(self, log_dir: Path, prefix: str = "assistant",
                 keep_days: int = 14, encoding: str = "utf-8"):
        self._log_dir = log_dir
        self._prefix = prefix
        self._keep_days = keep_days
        self._day: date | None = None
        super().__init__(self._path_for(date.today()), mode="a",
                         encoding=encoding, delay=True)
        self._purge_old()

    def _path_for(self, day: date) -> str:
        return str(self._log_dir / f"{self._prefix}-{day.isoformat()}.log")

    def _purge_old(self) -> None:
        cutoff = date.today() - timedelta(days=self._keep_days)
        for path in self._log_dir.glob(f"{self._prefix}-*.log"):
            try:
                day = date.fromisoformat(path.stem[len(self._prefix) + 1:])
            except ValueError:
                continue
            if day < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def emit(self, record: logging.LogRecord) -> None:
        today = date.today()
        if today != self._day:
            self.close()                     # 先释放旧句柄, 再换文件
            self.baseFilename = os.path.abspath(self._path_for(today))
            self._day = today
        super().emit(record)


def configure_logging(data_dir: Path, logger_name: str = "migration_assistant") -> logging.Logger:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = DailyFileHandler(log_dir)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(SensitiveDataFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
