"""应用组装: QApplication + 主题 + 依赖图 + 主窗口 + 单实例."""
import ctypes
import sys
from ctypes import wintypes as wt
from pathlib import Path

from PySide6.QtCore import QSharedMemory
from PySide6.QtWidgets import QApplication, QMessageBox

from migration_assistant import config
from migration_assistant.logging_setup import configure_logging
from migration_assistant.services.account_service import AccountService
from migration_assistant.services.app_scanner import AppScanner
from migration_assistant.storage.account_repository import AccountRepository
from migration_assistant.storage.credential_protector import WindowsDPAPI
from migration_assistant.storage.database import Database
from migration_assistant.ui.main_window import MainWindow

# 单实例共享内存键 (进程退出后自动释放)
_SINGLE_INSTANCE_KEY = "YingdaoMigrationAssistant-SingleInstance"
# 持有引用防止被 GC 回收导致锁失效
_shared_memory: QSharedMemory | None = None

_WINDOW_TITLE = "影刀迁移助手"


def _activate_existing_window() -> None:
    """查找已打开的"影刀迁移助手"窗口并激活到前台."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9
    KEYUP = 0x2
    VK_MENU = 0x12

    target = []

    def enum_cb(hwnd, lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == _WINDOW_TITLE and user32.IsWindowVisible(hwnd):
            target.append(hwnd)
            return False                                  # 停止枚举
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    if not target:
        return

    hwnd = target[0]
    user32.ShowWindow(hwnd, SW_RESTORE)                   # 还原最小化窗口

    # 绕过 Windows 前台锁定: 模拟按下 Alt, 附加线程输入后再置前台
    foreground = user32.GetForegroundWindow()
    current_thread = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(foreground, None) \
        if foreground else 0

    attached = False
    if fg_thread and fg_thread != current_thread:
        attached = bool(user32.AttachThreadInput(current_thread, fg_thread, True))
    user32.keybd_event(VK_MENU, 0, 0, 0)                  # Alt 按下
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYUP, 0)              # Alt 抬起
    user32.BringWindowToTop(hwnd)
    if attached:
        user32.AttachThreadInput(current_thread, fg_thread, False)


def _acquire_single_instance() -> bool:
    """单实例锁: 返回 True=本进程为主实例可继续; False=已有实例, 已激活其窗口应退出."""
    global _shared_memory
    _shared_memory = QSharedMemory(_SINGLE_INSTANCE_KEY)
    if _shared_memory.attach():
        # 已有实例在运行 -> 激活它的窗口, 本进程退出
        _shared_memory.detach()
        _activate_existing_window()
        return False
    if not _shared_memory.create(1):
        # attach 失败但 create 也失败: 视为已有实例, 激活并退出
        _activate_existing_window()
        return False
    return True


def _load_theme() -> str:
    theme_path = Path(__file__).parent / "resources" / "theme.qss"
    return theme_path.read_text(encoding="utf-8")


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setStyleSheet(_load_theme())
    # 关闭最后一个窗口即退出进程
    app.setQuitOnLastWindowClosed(True)

    # 单实例: 已有实例则激活其窗口并退出本进程
    if not _acquire_single_instance():
        return 0

    # 程序图标
    from PySide6.QtGui import QIcon
    icon_path = Path(__file__).parent / "resources" / "app_icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # 日志
    configure_logging(config.DATA_DIR)

    # 存储
    conn = None
    try:
        db = Database(config.DB_PATH)
        conn = db.connect()
        repository = AccountRepository(conn)
    except Exception:
        QMessageBox.critical(None, "启动失败", "无法初始化本地账号数据库")
        return 1

    protector = WindowsDPAPI(config.DPAPI_ENTROPY)
    account_service = AccountService(repository, protector)
    scanner = AppScanner()

    window = MainWindow(
        scanner=scanner,
        account_service=account_service,
        repository=repository,
        account_repository=repository,
        data_dir=config.DATA_DIR,
    )
    window._reload_accounts()
    window.show()
    window.refresh_apps()

    exit_code = app.exec()
    # 释放单实例锁
    try:
        if _shared_memory is not None:
            _shared_memory.detach()
    except Exception:
        pass
    # 事件循环结束后关闭数据库连接, 确保进程干净退出
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass
    return exit_code


def main() -> int:
    return run()
