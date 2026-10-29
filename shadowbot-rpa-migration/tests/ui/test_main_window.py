"""主窗口双栏 UI 测试 (offscreen)."""
import os
import re

import pytest
from PySide6.QtCore import Qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from migration_assistant.models import Account  # noqa: E402
from migration_assistant.storage.database import Database  # noqa: E402
from migration_assistant.storage.account_repository import AccountRepository  # noqa: E402
from migration_assistant.storage.credential_protector import WindowsDPAPI  # noqa: E402
from migration_assistant.services.account_service import AccountService  # noqa: E402
from migration_assistant.services.app_scanner import AppScanner  # noqa: E402
from migration_assistant.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(qtbot, apps_tree, tmp_path):
    db = Database(tmp_path / "assistant.db")
    conn = db.connect()
    repo = AccountRepository(conn)
    service = AccountService(repo, WindowsDPAPI(b"test"))
    win = MainWindow(scanner=AppScanner(), account_service=service,
                     repository=repo, account_repository=repo,
                     data_dir=tmp_path)
    qtbot.addWidget(win)
    # 扫描
    result = win.scanner.scan(apps_tree)
    win._apps = list(result.apps)
    win.app_list.set_apps(win._apps)
    # 注入账号
    win._accounts = [Account(1, "user-a@example.com", "verified", "t"),
                     Account(2, "user-b@example.com", "verified", "t"),
                     Account(3, "user-c@example.com", "unavailable", "t")]
    win.account_list.set_accounts(win._accounts)
    return win


def test_main_window_has_approved_title_and_two_columns(window):
    assert window.windowTitle() == "影刀迁移助手"
    assert window.app_list.title_label.text() == "可迁移应用"
    assert window.account_list.title_label.text() == "目标账号"


def test_task_count_and_start_state_follow_selection(window):
    window.app_list.set_selected_ids({a.id for a in window._apps})
    window.account_list.set_selected_ids({1, 2})
    window._update_task_summary()
    nums = re.findall(r"<b>(\d+)</b>", window.task_summary.text())
    assert nums == ["2", "2", "4"]
    assert window.start_button.isEnabled()


def test_no_selection_disables_start(window):
    window.account_list.set_selected_ids(set())
    window._update_task_summary()
    assert not window.start_button.isEnabled()


def test_unavailable_account_cannot_be_selected(window):
    window.account_list.set_selected_ids({1, 3})
    selected = window.account_list.selected_accounts()
    assert len(selected) == 1 and selected[0].id == 1


def test_select_all_excludes_unavailable(window):
    window.account_list.select_all.setCheckState(Qt.CheckState.Checked)
    assert len(window.account_list.selected_accounts()) == 2
