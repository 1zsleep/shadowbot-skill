"""主窗口: 平衡双栏 (左侧应用, 右侧账号) + 底部任务栏 + 进度面板."""
from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from migration_assistant.models import TaskStatus
from migration_assistant.services.task_runner import (TaskRunner, build_tasks,
                                                      failed_tasks)
from migration_assistant.ui.account_dialog import AccountDialog
from migration_assistant.ui.list_widgets import AccountListWidget, AppListWidget
from migration_assistant.ui.progress_panel import ProgressPanel
from migration_assistant.ui.result_dialog import ResultDialog
from migration_assistant.workers import MigrationWorker


class MainWindow(QMainWindow):
    def __init__(self, *, scanner, account_service, repository,
                 account_repository, data_dir):
        super().__init__()
        self.scanner = scanner
        self.account_service = account_service
        self.repository = repository
        self.account_repository = account_repository
        self.data_dir = data_dir

        self._apps = []
        self._accounts = []
        self._migration_thread = None
        self._migration_worker = None
        self._scan_thread = None
        self._scanning = False
        self._validating = False
        self._migrating = False

        self.setWindowTitle("影刀迁移助手")
        self.resize(1120, 720)
        self.setMinimumSize(900, 600)

        self._build_ui()
        self._connect_signals()

    # ---------- UI ----------
    def _build_ui(self):
        # 顶部栏
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        self.refresh_button = QPushButton("刷新应用")
        self.refresh_button.setObjectName("refreshBtn")
        self.refresh_button.clicked.connect(self.refresh_apps)

        title_label = QLabel("影刀迁移助手")
        title_label.setObjectName("titleLabel")
        top_bar = QWidget()
        top_bar.setObjectName("topBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(6, 2, 6, 2)
        top_layout.addWidget(title_label)
        top_layout.addStretch()
        top_layout.addWidget(self.status_label)
        top_layout.addWidget(self.refresh_button)

        # 左: 应用; 右: 账号
        self.app_list = AppListWidget()
        self.account_list = AccountListWidget()

        app_card = QFrame()
        app_card.setObjectName("card")
        app_layout = QVBoxLayout(app_card)
        app_layout.setContentsMargins(0, 0, 0, 0)
        app_layout.addWidget(self.app_list)
        account_card = QFrame()
        account_card.setObjectName("card")
        account_layout = QVBoxLayout(account_card)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.addWidget(self.account_list)

        cols = QHBoxLayout()
        cols.setSpacing(14)
        cols.addWidget(app_card, 1)
        cols.addWidget(account_card, 1)

        # 底部操作栏
        self.task_summary = QLabel("已选 0 个应用 × 0 个账号 = 0 个迁移任务")
        self.task_summary.setObjectName("taskSummary")
        self.start_button = QPushButton("开始迁移 →")
        self.start_button.setObjectName("startBtn")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._confirm_and_start)

        footer = QFrame()
        footer.setObjectName("footerBar")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 10, 16, 10)
        footer_layout.addWidget(self.task_summary)
        footer_layout.addStretch()
        footer_layout.addWidget(self.start_button)

        # 进度面板 (默认隐藏)
        self.progress_panel = ProgressPanel()
        self.progress_panel.setVisible(False)
        self.progress_panel.stop_button.clicked.connect(self._request_stop)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(top_bar)
        body = QVBoxLayout()
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(14)
        body.addLayout(cols, 1)
        body.addWidget(footer)
        body.addWidget(self.progress_panel)
        root.addLayout(body)
        self.setCentralWidget(central)

    def _connect_signals(self):
        self.app_list.selection_changed.connect(self._update_task_summary)
        self.account_list.selection_changed.connect(self._update_task_summary)
        self.account_list.add_requested.connect(self._add_account)
        self.account_list.edit_requested.connect(self._edit_account)
        self.account_list.delete_requested.connect(self._delete_account)
        self.account_list.revalidate_requested.connect(self._revalidate_account)

    # ---------- 应用扫描 ----------
    def refresh_apps(self):
        if self._scanning or self._migrating:
            return
        self._scanning = True
        self._set_status("正在扫描…")
        self.refresh_button.setEnabled(False)

        from PySide6.QtCore import QThread
        from migration_assistant import config

        class ScanWorker(QThread):
            def __init__(self, scanner, users_dir):
                super().__init__()
                self.scanner = scanner
                self.users_dir = users_dir
                self.result = None
                self.error = None

            def run(self):
                try:
                    self.result = self.scanner.scan(self.users_dir)
                except Exception as e:
                    self.error = e

        self._scan_thread = ScanWorker(self.scanner, config.SHADOWBOT_USERS)
        self._scan_thread.finished.connect(self._on_scan_finished)
        self._scan_thread.start()

    def _on_scan_finished(self):
        thread = self._scan_thread
        self._scan_thread = None
        self._scanning = False
        self.refresh_button.setEnabled(True)
        if thread.error is not None:
            self._set_status("扫描失败")
            self.app_list.set_apps([])
            QMessageBox.warning(self, "扫描失败", str(thread.error))
        else:
            self._apps = list(thread.result.apps)
            self.app_list.set_apps(self._apps)
            skipped = thread.result.skipped_count
            self._set_status(f"就绪 · 共 {len(self._apps)} 个应用"
                             + (f" (跳过 {skipped} 个无效)" if skipped else ""))
        thread.deleteLater()
        self._update_task_summary()

    def _set_status(self, text: str):
        self.status_label.setText(text)

    # ---------- 账号 CRUD ----------
    def _reload_accounts(self, select_id=None):
        self._accounts = self.account_repository.list()
        self.account_list.set_accounts(self._accounts)
        if select_id is not None:
            self.account_list.set_selected_ids({select_id})
        self._update_task_summary()

    def _add_account(self):
        dialog = AccountDialog(self.account_service, self)
        dialog.account_saved.connect(
            lambda acc: self._reload_accounts(acc.id))
        dialog.exec()

    def _edit_account(self, account_id):
        acc = self.account_repository.get(account_id)
        if acc is None:
            return
        dialog = AccountDialog(self.account_service, self, username=acc.username)
        dialog.account_saved.connect(
            lambda new_acc: self._reload_accounts(new_acc.id))
        dialog.exec()

    def _delete_account(self, account_id):
        acc = self.account_repository.get(account_id)
        if acc is None:
            return
        answer = QMessageBox.question(
            self, "删除账号",
            f"确定删除本地保存的账号 {acc.username} 吗？\n"
            "仅删除本地记录，不会影响影刀云端账号。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.account_repository.delete(account_id)
            self._reload_accounts()

    def _revalidate_account(self, account_id):
        acc = self.account_repository.get(account_id)
        if acc is None:
            return
        dialog = AccountDialog(self.account_service, self, username=acc.username)
        dialog.account_saved.connect(
            lambda new_acc: self._reload_accounts(new_acc.id))
        dialog.exec()

    # ---------- 任务统计 ----------
    def _update_task_summary(self):
        n_apps = len(self.app_list.selected_apps())
        n_accounts = len(self.account_list.selected_accounts())
        total = n_apps * n_accounts
        self.task_summary.setText(
            f"已选 <b>{n_apps}</b> 个应用 × <b>{n_accounts}</b> 个账号 "
            f"= <b>{total}</b> 个迁移任务")
        enabled = (n_apps > 0 and n_accounts > 0
                   and not self._scanning and not self._migrating)
        self.start_button.setEnabled(enabled)

    # ---------- 迁移 ----------
    def _confirm_and_start(self):
        apps = self.app_list.selected_apps()
        accounts = self.account_list.selected_accounts()
        if not apps or not accounts:
            return
        total = len(apps) * len(accounts)
        names = "、".join(a.username for a in accounts)
        answer = QMessageBox.question(
            self, "确认迁移",
            f"将迁移 <b>{len(apps)}</b> 个应用到 <b>{len(accounts)}</b> 个账号 "
            f"({names})，共 <b>{total}</b> 个任务。\n继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._start_migration(apps, accounts)

    def _start_migration(self, apps, accounts):
        self._migrating = True
        self._set_status("正在迁移…")
        self.start_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.app_list.setEnabled(False)
        self.account_list.set_locked(True)
        self.progress_panel.setVisible(True)
        self.progress_panel.reset()
        self.progress_panel.stop_button.setEnabled(True)

        tasks = build_tasks(apps, accounts)
        self._current_tasks = tasks
        app_by_id = {a.id: a for a in apps}
        account_by_id = {a.id: a for a in accounts}
        runner = TaskRunner(account_service=self.account_service)
        self.progress_panel.set_total(len(tasks))

        self._migration_thread = QThread(self)
        self._migration_worker = MigrationWorker(runner, tasks, app_by_id, account_by_id)
        self._migration_worker.moveToThread(self._migration_thread)
        self._migration_thread.started.connect(self._migration_worker.run)
        self._migration_worker.task_started.connect(self.progress_panel.on_task_started)
        self._migration_worker.stage_changed.connect(self.progress_panel.on_stage)
        self._migration_worker.task_finished.connect(self.progress_panel.on_task_finished)
        self._migration_worker.log_line.connect(self.progress_panel.append_log)
        self._migration_worker.completed.connect(self._on_migration_completed)
        self._migration_thread.finished.connect(self._cleanup_migration_thread)
        self._migration_thread.start()

    def _request_stop(self):
        if self._migration_worker is not None:
            answer = QMessageBox.question(
                self, "停止迁移",
                "确定停止迁移吗？当前任务完成后将不再开始新任务。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.Yes:
                self._migration_worker.request_stop()
                self.progress_panel.stop_button.setEnabled(False)

    def _on_migration_completed(self, results):
        self._migrating = False
        self._set_status("已完成")
        # 迁移结束: 禁用停止按钮 (任务已全部完成或取消, 停止无意义)
        self.progress_panel.stop_button.setEnabled(False)
        self.app_list.setEnabled(True)
        self.account_list.set_locked(False)
        self.refresh_button.setEnabled(True)
        self._update_task_summary()

        dialog = ResultDialog(results, self._last_tasks())
        dialog.retry_requested.connect(self._on_retry)
        dialog.exec()

    def _last_tasks(self):
        return getattr(self, "_current_tasks", [])

    def _on_retry(self, retry_tasks):
        apps = self.app_list.selected_apps()
        accounts = self.account_list.selected_accounts()
        if not apps or not accounts:
            return
        # 只迁移重试任务对应的应用/账号
        retry_app_ids = {t.app_id for t in retry_tasks}
        retry_account_ids = {t.account_id for t in retry_tasks}
        self._current_tasks = retry_tasks
        self._start_migration(
            [a for a in apps if a.id in retry_app_ids],
            [a for a in accounts if a.id in retry_account_ids])

    def _cleanup_migration_thread(self):
        if self._migration_thread:
            self._migration_thread.deleteLater()
            self._migration_worker.deleteLater()
            self._migration_thread = None
            self._migration_worker = None

    def closeEvent(self, event):
        if self._migrating:
            answer = QMessageBox.question(
                self, "退出",
                "迁移正在进行，确定退出吗？未完成的任务将不会执行。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            if self._migration_worker is not None:
                self._migration_worker.request_stop()
        # 等待所有后台线程结束, 避免 Qt 退出时线程残留导致进程挂起
        if self._migration_thread is not None:
            self._migration_thread.quit()
            self._migration_thread.wait(8000)
        if self._scan_thread is not None:
            self._scan_thread.wait(5000)
        # 显式退出事件循环
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
        event.accept()
