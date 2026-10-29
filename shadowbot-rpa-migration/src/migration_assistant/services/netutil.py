"""网络异常分类与环境快照: 把 requests 的底层异常翻译成能定位问题的中文原因.

背景: 原来所有连接层失败都被归成一句「无法连接影刀服务器」, DNS 解析失败 /
TLS 被打断 / 代理 502 / 连接被对端重置 / 真超时 全都长一个样, 日志里也留不下
任何痕迹。这里统一做三件事:

1. classify()  —— 按类型 + 异常链文字特征分成可区分的 code, 并标明是否值得重试
2. dns_probe() —— 失败后复核一次 DNS 解析耗时(带超时上限, 不会反过来拖住主流程)
3. log_failure() —— 把异常链、分类、DNS 复核、代理配置一次性写进日志(已脱敏)
"""
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from migration_assistant.logging_setup import sanitize

logger = logging.getLogger("migration_assistant.net")

# 文字特征: 用来穿过 urllib3 那几层包装, 认出真正的原因
_DNS_HINTS = (
    "getaddrinfo failed", "name or service not known",
    "temporary failure in name resolution", "nodename nor servname",
    "no address associated with hostname", "dns",
)
_RESET_HINTS = (
    "remotedisconnected", "remote end closed connection",
    "connection aborted", "connectionreseterror", "connection reset",
    "forcibly closed", "10054", "reset by peer",
)
_REFUSED_HINTS = ("connectionrefusederror", "actively refused", "10061")
_TLS_HINTS = ("ssl", "certificate verify failed", "wrong version number",
              "eof occurred in violation", "tlsv1", "handshake")


@dataclass(frozen=True)
class NetIssue:
    """一次请求失败的归类结果."""
    code: str
    reason: str          # 给用户看的一句话
    retryable: bool      # 是否值得重试


@dataclass(frozen=True)
class DnsProbe:
    host: str
    elapsed: float
    ips: tuple[str, ...] = ()
    error: str | None = None

    def describe(self) -> str:
        if self.error:
            return f"{self.host} 解析失败 (耗时 {self.elapsed:.2f}s): {self.error}"
        return (f"{self.host} -> {', '.join(self.ips) or '(无记录)'} "
                f"(耗时 {self.elapsed:.2f}s)")


def _chain(exc: BaseException) -> list[BaseException]:
    """展开异常链 (__cause__ / __context__ / args 里的嵌套异常), 去重保序."""
    out: list[BaseException] = []
    seen: set[int] = set()

    def push(e: BaseException) -> None:
        if id(e) in seen or len(out) > 12:
            return
        seen.add(id(e))
        out.append(e)
        for arg in e.args:
            if isinstance(arg, BaseException):
                push(arg)
        push_from = e.__cause__ or e.__context__
        if push_from is not None:
            push(push_from)

    push(exc)
    return out


def _text_of(chain: list[BaseException]) -> str:
    return " ".join(f"{type(e).__name__}: {e}" for e in chain).lower()


def _has(text: str, hints: tuple[str, ...]) -> bool:
    return any(h in text for h in hints)


def short(exc: BaseException, limit: int = 240) -> str:
    """异常的一句话摘要 (脱敏 + 截断), 用于日志和界面提示."""
    text = sanitize(f"{type(exc).__name__}: {exc}")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def classify(exc: BaseException) -> NetIssue:
    """把 requests / socket 异常翻译成 (code, 中文原因, 是否可重试)."""
    chain = _chain(exc)
    text = _text_of(chain)

    # 顺序要紧: ConnectTimeout 同时是 ConnectionError 的子类, 必须先判超时
    if isinstance(exc, requests.exceptions.ProxyError) or "proxyerror" in text:
        return NetIssue("net_proxy", "代理连接失败（请求走了代理）", True)
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return NetIssue("net_connect_timeout", "建立连接超时（TCP/TLS 握手无响应）", True)
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return NetIssue("net_read_timeout", "等待服务器响应超时", True)
    if isinstance(exc, requests.exceptions.SSLError) or _has(text, _TLS_HINTS):
        return NetIssue("net_tls", "TLS 握手失败（连接被中断或证书被拦截）", True)
    if _has(text, _DNS_HINTS):
        return NetIssue("net_dns", "域名解析失败（DNS）", True)
    if _has(text, _RESET_HINTS):
        return NetIssue("net_reset", "连接被中断（对端重置）", True)
    if _has(text, _REFUSED_HINTS):
        return NetIssue("net_refused", "服务器拒绝连接", True)
    if isinstance(exc, requests.exceptions.ConnectionError):
        return NetIssue("net_unreachable", "网络不可达", True)
    return NetIssue("net_unknown", f"{type(exc).__name__}", True)


def http_issue(status_code: int) -> NetIssue:
    """HTTP 状态码归类: 决定要不要重试."""
    if status_code in (401, 403):
        return NetIssue("http_auth", f"服务器拒绝认证（HTTP {status_code}）", False)
    if status_code == 429:
        return NetIssue("http_busy", "服务器限流（HTTP 429）", True)
    if 500 <= status_code < 600:
        return NetIssue("http_server", f"影刀服务器异常（HTTP {status_code}）", True)
    if 400 <= status_code < 500:
        return NetIssue("http_bad_request", f"请求被拒绝（HTTP {status_code}）", False)
    return NetIssue("http_other", f"意外的响应（HTTP {status_code}）", False)


def dns_probe(host: str, port: int = 443, timeout: float = 5.0) -> DnsProbe:
    """复核域名解析: 带硬超时, 避免失败路径上再阻塞十几秒.

    getaddrinfo 不受 requests 的 timeout 约束, 这也是弱网下「十几秒后报无法连接」
    最常见的原因, 所以单独量一下并写进日志。
    """
    box: dict = {}

    def work() -> None:
        started = time.monotonic()
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            box.update(elapsed=time.monotonic() - started, ips=(),
                       error=f"{type(exc).__name__}: {exc}")
            return
        ips = tuple(sorted({info[4][0] for info in infos}))
        box.update(elapsed=time.monotonic() - started, ips=ips, error=None)

    worker = threading.Thread(target=work, name="dns-probe", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return DnsProbe(host, timeout, (), f"解析超过 {timeout:g} 秒仍未返回")
    return DnsProbe(host, **box)


def proxy_snapshot() -> dict:
    """采集可能影响 requests 的代理配置 (环境变量 + WinINET), 仅用于日志定位."""
    env = {key: value for key, value in os.environ.items()
           if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    wininet: dict = {}
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            for name in ("ProxyEnable", "ProxyServer", "AutoConfigURL"):
                try:
                    wininet[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    continue
    except Exception:  # noqa: BLE001 - 拿不到代理信息不影响主流程
        pass
    return {"env": env, "wininet": wininet}


def log_business_failure(*, stage: str, url: str, detail: str) -> None:
    """HTTP 通了但业务被拒 (200 + success=false / 无 token) 也要留痕."""
    host = urlsplit(url).hostname or url
    logger.error(sanitize(f"[{stage}] {host} 业务失败: {detail}"))


def log_failure(*, stage: str, method: str, url: str, exc: BaseException | None,
                issue: NetIssue, elapsed: float, attempt: int, attempts: int) -> None:
    """把一次失败完整写进日志: 阶段 / 耗时 / 异常链 / 分类 / DNS 复核 / 代理."""
    host = urlsplit(url).hostname or url
    lines = [
        f"[{stage}] {method} {host} 失败, 用时 {elapsed:.2f}s "
        f"(第 {attempt}/{attempts} 次尝试)",
        f"  分类: {issue.code} / {issue.reason} / 可重试={issue.retryable}",
    ]
    if exc is not None:
        lines.append("  异常链: " + " -> ".join(type(e).__name__ for e in _chain(exc)))
        lines.append(f"  原始信息: {short(exc)}")
    if issue.code in ("net_dns", "net_unreachable", "net_unknown", "net_reset"):
        lines.append(f"  DNS 复核: {dns_probe(host).describe()}")
    snap = proxy_snapshot()
    lines.append(f"  代理: 环境变量={snap['env'] or '无'} WinINET={snap['wininet'] or '无'}")
    logger.error(sanitize("\n".join(lines)))
