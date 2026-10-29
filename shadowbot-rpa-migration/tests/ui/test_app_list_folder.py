"""应用列表「打开所在文件夹」测试 (offscreen)."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from migration_assistant.models import AppInfo  # noqa: E402
from migration_assistant.ui.list_widgets import AppListWidget  # noqa: E402


def _app(tmp_path, name="日报"):
    app_dir = tmp_path / "apps" / "uuid-1" / "xbot_robot"
    app_dir.mkdir(parents=True, exist_ok=True)
    return AppInfo(name, "u1", app_dir, app_dir / "package.json", {})


def test_double_click_opens_app_folder(qapp, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("migration_assistant.ui.list_widgets.QDesktopServices.openUrl",
                        lambda url: opened.append(url.toLocalFile()))

    widget = AppListWidget()
    app = _app(tmp_path)
    widget.set_apps([app])

    item = widget.list.item(0)
    widget.list.itemDoubleClicked.emit(item)      # 模拟双击
    # QUrl.toLocalFile 用正斜杠, 这里统一成 Path 比较
    assert [Path(p) for p in opened] == [app.app_dir.parent]


def test_open_folder_reports_missing_dir(qapp, tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr("migration_assistant.ui.list_widgets.QMessageBox.warning",
                        lambda *a, **k: warned.append(a))
    monkeypatch.setattr("migration_assistant.ui.list_widgets.QDesktopServices.openUrl",
                        lambda url: None)

    widget = AppListWidget()
    app = _app(tmp_path)
    widget.set_apps([app])
    import shutil
    shutil.rmtree(app.app_dir.parent)

    assert widget.open_app_folder(app.id) is False
    assert warned


def test_menu_offers_open_folder(qapp, tmp_path, monkeypatch):
    """右键菜单里必须有「打开所在文件夹」."""
    widget = AppListWidget()
    app = _app(tmp_path)
    widget.set_apps([app])

    captured = {}

    class FakeMenu:
        def addAction(self, text):
            captured.setdefault("actions", []).append(text)
            return text

        def exec(self, *a):
            return None

    monkeypatch.setattr("migration_assistant.ui.list_widgets.QMenu",
                        lambda *a, **k: FakeMenu())
    widget._show_menu(widget.list.visualItemRect(widget.list.item(0)).center())
    assert "打开所在文件夹" in captured.get("actions", [])
