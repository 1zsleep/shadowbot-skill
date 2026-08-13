"""添加/更新账号弹窗, 后台验证."""
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton,
                               QVBoxLayout)

from migration_assistant.workers import AccountValidationWorker


class AccountDialog(QDialog):
    account_saved = Signal(object)

    def __init__(self, account_service, parent=None, username=None):
        super().__init__(parent)
        self.account_service = account_service
        self.username = username  # 编辑/重验时固定
        self.worker_thread = None
        self.worker = None

        self.setWindowTitle("添加账号" if not username else "编辑账号")
        self.setMinimumWidth(380)

        self.username_edit = QLineEdit(username or "")
        self.username_edit.setPlaceholderText("影刀账号")
        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("密码")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.toggle_btn = QPushButton("显示")
        self.toggle_btn.setCheckable(True)
        # 不参与 Tab/回车焦点链: 避免回车触发"显示"而非"验证并保存"
        self.toggle_btn.setFocusPolicy(Qt.NoFocus)
        self.toggle_btn.toggled.connect(
            lambda on: self.password_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))

        pwd_row = QHBoxLayout()
        pwd_row.addWidget(self.password_edit, 1)
        pwd_row.addWidget(self.toggle_btn)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color:#e53935;")
        self.error_label.setWordWrap(True)

        self.save_button = QPushButton("验证并保存")
        self.save_button.setObjectName("startBtn")
        self.save_button.setMinimumHeight(36)
        self.save_button.setMinimumWidth(140)

        # 无取消按钮: 直接关闭窗口即取消
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.save_button)
        btn_row.addStretch()

        form = QFormLayout()
        form.addRow("账号", self.username_edit)
        form.addRow("密码", pwd_row)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error_label)
        layout.addLayout(btn_row)

        self.save_button.clicked.connect(self._start_validation)
        # 密码框按回车 = 点击"验证并保存"
        self.password_edit.returnPressed.connect(self._start_validation)
        # 回车默认触发保存按钮 (对话框默认按钮)
        self.save_button.setDefault(True)
        if username:
            self.username_edit.setReadOnly(True)

    def _start_validation(self):
        if self.worker_thread is not None:
            return
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            self.error_label.setText("请输入账号和密码")
            return
        self._set_busy(True)
        self.error_label.setText("正在验证…")

        self.worker_thread = QThread(self)
        self.worker = AccountValidationWorker(self.account_service, username, password)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker_thread.finished.connect(self._cleanup_thread)
        self.worker_thread.start()

    def _on_success(self, account):
        self.account_saved.emit(account)
        self.accept()

    def _on_failure(self, message):
        self._set_busy(False)
        self.error_label.setText(message)

    def _set_busy(self, busy):
        self.save_button.setEnabled(not busy)
        self.username_edit.setEnabled(not busy)
        self.password_edit.setEnabled(not busy)

    def _cleanup_thread(self):
        if self.worker_thread:
            self.worker_thread.deleteLater()
            self.worker.deleteLater()
            self.worker_thread = None
            self.worker = None

    def reject(self):
        if self.worker_thread is not None:
            self.worker_thread.requestInterruption()
            self.worker_thread.quit()
            self.worker_thread.wait(3000)
        super().reject()
