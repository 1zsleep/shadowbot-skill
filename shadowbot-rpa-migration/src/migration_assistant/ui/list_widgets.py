"""可复用的应用/账号列表组件."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QVBoxLayout, QWidget)

from migration_assistant.ui.name_mode_switch import NameModeSwitch


class AppListWidget(QWidget):
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._apps = []  # list[AppInfo]
        self._selected: set[str] = set()
        self._mode = "display"  # 默认显示名

        title_row = QHBoxLayout()
        self.title_label = QLabel("可迁移应用")
        self.title_label.setObjectName("cardTitle")
        self.count_label = QLabel("已选 0 / 0")
        self.count_label.setObjectName("cardCount")
        title_row.addWidget(self.title_label)
        title_row.addStretch()
        title_row.addWidget(self.count_label)

        # 账号名/显示名 切换开关
        self.mode_switch = NameModeSwitch()
        self.mode_switch.mode_changed.connect(self._on_mode_changed)

        self.select_all = QCheckBox("全选")
        self.search = QLineEdit()
        self.search.setObjectName("searchBox")
        self.search.setPlaceholderText("搜索可迁移应用…")
        self.search.textChanged.connect(self._apply_filter)

        self.list = QListWidget()
        self.list.setObjectName("appList")
        self.list.itemChanged.connect(self._on_item_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.addLayout(title_row)
        row = QHBoxLayout()
        row.addWidget(self.mode_switch)
        row.addWidget(self.select_all)
        row.addStretch()
        row.addWidget(self.search, 1)
        layout.addLayout(row)
        layout.addWidget(self.list, 1)

        self.select_all.stateChanged.connect(self._toggle_all)

    # ---------- 数据 ----------
    def set_apps(self, apps):
        self._apps = list(apps)
        self._selected = {a.id for a in self._apps if a.id in self._selected}
        self._rebuild()

    def selected_apps(self):
        return [a for a in self._apps if a.id in self._selected]

    def set_selected_ids(self, ids):
        self._selected = set(ids)
        self._rebuild()

    def _rebuild(self):
        self.list.blockSignals(True)
        self.list.clear()
        for app in self._apps:
            item = QListWidgetItem(self._row_text(app))
            item.setData(Qt.UserRole, app.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if app.id in self._selected else Qt.Unchecked)
            item.setToolTip(f"{app.app_dir}\n账号: {app.owner_name}")
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._apply_filter()
        self._update_counts()

    def _row_text(self, app) -> str:
        """按当前模式显示: 应用名 (账号名/显示名)."""
        label = app.display_name if self._mode == "display" else app.owner_name
        if label:
            return f"{app.name}  ({label})"
        return app.name

    def _on_mode_changed(self, mode: str):
        """账号名/显示名切换时刷新列表文本."""
        self._mode = mode
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            app = self._find_app(item.data(Qt.UserRole))
            if app is not None:
                item.setText(self._row_text(app))
        self.list.blockSignals(False)
        self._apply_filter()

    # ---------- 交互 ----------
    def _on_item_changed(self, item):
        app_id = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self._selected.add(app_id)
        else:
            self._selected.discard(app_id)
        self._update_counts()
        self._update_select_all()
        self.selection_changed.emit()

    def _toggle_all(self, state):
        # stateChanged 传 int (0=Unchecked, 1=Partially, 2=Checked)
        checked = int(state) == 2
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.list.blockSignals(False)
        self._selected = {a.id for a in self._apps} if checked else set()
        self._update_counts()
        self.selection_changed.emit()

    def _apply_filter(self):
        text = self.search.text().strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            app = self._find_app(item.data(Qt.UserRole))
            if not app:
                item.setHidden(True)
                continue
            haystack = f"{app.name} {app.owner_name} {app.display_name}".lower()
            item.setHidden(not (not text or text in haystack))

    def _update_counts(self):
        self.count_label.setText(f"已选 {len(self._selected)} / {len(self._apps)}")

    def _update_select_all(self):
        self.select_all.blockSignals(True)
        if self._selected:
            self.select_all.setCheckState(
                Qt.Checked if len(self._selected) == len(self._apps)
                else Qt.PartiallyChecked)
        else:
            self.select_all.setCheckState(Qt.Unchecked)
        self.select_all.blockSignals(False)

    def _find_app(self, app_id):
        for a in self._apps:
            if a.id == app_id:
                return a
        return None


class AccountListWidget(QWidget):
    selection_changed = Signal()
    add_requested = Signal()
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    revalidate_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._accounts = []
        self._selected: set[int] = set()

        title_row = QHBoxLayout()
        self.title_label = QLabel("目标账号")
        self.title_label.setObjectName("cardTitle")
        self.count_label = QLabel("已选 0 / 0")
        self.count_label.setObjectName("cardCount")
        title_row.addWidget(self.title_label)
        title_row.addStretch()
        title_row.addWidget(self.count_label)

        self.select_all = QCheckBox("全选")
        self.add_btn = QPushButton("＋ 添加账号")
        self.add_btn.setObjectName("addAccountBtn")
        self.add_btn.clicked.connect(self.add_requested.emit)

        self.list = QListWidget()
        self.list.setObjectName("accountList")
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.addLayout(title_row)
        row = QHBoxLayout()
        row.addWidget(self.select_all)
        row.addStretch()
        row.addWidget(self.add_btn)
        layout.addLayout(row)
        layout.addWidget(self.list, 1)

        self.select_all.stateChanged.connect(self._toggle_all)

    # ---------- 数据 ----------
    def set_accounts(self, accounts):
        self._accounts = list(accounts)
        self._selected = {a.id for a in self._accounts if a.id in self._selected}
        self._rebuild()

    def selected_accounts(self):
        return [a for a in self._accounts
                if a.id in self._selected and a.validation_state == "verified"]

    def set_selected_ids(self, ids):
        # 只保留可选中账户的 id
        selectable = {a.id for a in self._accounts
                      if a.validation_state == "verified"}
        self._selected = set(ids) & selectable
        self._rebuild()

    def _rebuild(self):
        self.list.blockSignals(True)
        self.list.clear()
        for acc in self._accounts:
            selectable = acc.validation_state == "verified"
            status = {"verified": "已验证", "verifying": "验证中",
                      "failed": "验证失败", "unavailable": "凭据不可用"}.get(
                acc.validation_state, acc.validation_state)
            text = f"{acc.username}    ({status})"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, acc.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if acc.id in self._selected and selectable
                               else Qt.Unchecked)
            if not selectable:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_counts()
        self._update_select_all()

    # ---------- 交互 ----------
    def _on_item_changed(self, item):
        acc_id = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self._selected.add(acc_id)
        else:
            self._selected.discard(acc_id)
        self._update_counts()
        self._update_select_all()
        self.selection_changed.emit()

    def _toggle_all(self, state):
        # stateChanged 传 int (0=Unchecked, 1=Partially, 2=Checked)
        checked = int(state) == 2
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            acc = self._find_account(item.data(Qt.UserRole))
            if acc and acc.validation_state == "verified":
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.list.blockSignals(False)
        self._selected = {a.id for a in self._accounts
                          if a.validation_state == "verified"} if checked else set()
        self._update_counts()
        self.selection_changed.emit()

    def _show_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        acc_id = item.data(Qt.UserRole)
        menu = QMenu(self)
        act_revalidate = menu.addAction("重新验证")
        act_edit = menu.addAction("编辑")
        act_delete = menu.addAction("删除")
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen == act_revalidate:
            self.revalidate_requested.emit(acc_id)
        elif chosen == act_edit:
            self.edit_requested.emit(acc_id)
        elif chosen == act_delete:
            self.delete_requested.emit(acc_id)

    def _update_counts(self):
        self.count_label.setText(f"已选 {len(self._selected)} / {len(self._accounts)}")

    def _update_select_all(self):
        self.select_all.blockSignals(True)
        if self._selected:
            self.select_all.setCheckState(
                Qt.Checked if len(self._selected) == len(self._accounts)
                else Qt.PartiallyChecked)
        else:
            self.select_all.setCheckState(Qt.Unchecked)
        self.select_all.blockSignals(False)

    def _find_account(self, acc_id):
        for a in self._accounts:
            if a.id == acc_id:
                return a
        return None

    def set_locked(self, locked: bool):
        self.add_btn.setEnabled(not locked)
        self.select_all.setEnabled(not locked)
