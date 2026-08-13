"""迁移结果汇总弹窗: 成功/失败/取消 + 复制脱敏信息 + 重试失败."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from migration_assistant.logging_setup import sanitize
from migration_assistant.models import STAGE_LABELS, TaskResult, TaskStatus


class ResultDialog(QDialog):
    retry_requested = Signal(object)

    def __init__(self, results: list[TaskResult], tasks, parent=None):
        super().__init__(parent)
        self.setWindowTitle("迁移结果")
        self.setMinimumSize(640, 420)
        self._tasks = list(tasks)

        succeeded = sum(1 for r in results if r.status is TaskStatus.SUCCEEDED)
        failed = sum(1 for r in results if r.status is TaskStatus.FAILED)
        cancelled = sum(1 for r in results if r.status is TaskStatus.CANCELLED)

        summary = QLabel(
            f"<b style='color:#2aa874'>成功 {succeeded}</b>　"
            f"<b style='color:#e53935'>失败 {failed}</b>　"
            f"<b style='color:#f5a623'>取消 {cancelled}</b>")
        summary.setStyleSheet("font-size:15px;")

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["应用", "目标账号", "阶段", "结果"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 140)

        task_by_id = {t.task_id: t for t in tasks}
        for r in results:
            task = task_by_id.get(r.task_id)
            app_name = task.app_name if task else "?"
            account = task.account_username if task else "?"
            if r.status is TaskStatus.SUCCEEDED:
                status_text = "✅ 成功"
            elif r.status is TaskStatus.FAILED:
                status_text = f"❌ {r.error_message or '失败'}"
            else:
                status_text = "⏸ 已取消"
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(app_name))
            self.table.setItem(row, 1, QTableWidgetItem(account))
            self.table.setItem(row, 2, QTableWidgetItem(
                STAGE_LABELS.get(r.stage, r.stage)))
            self.table.setItem(row, 3, QTableWidgetItem(status_text))

        self.copy_button = QPushButton("复制失败信息")
        self.retry_button = QPushButton("重试失败任务")
        self.retry_button.setObjectName("startBtn")
        self.close_button = QPushButton("关闭")

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.copy_button)
        if failed > 0:
            btn_row.addWidget(self.retry_button)
        btn_row.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.table, 1)
        layout.addLayout(btn_row)

        self.copy_button.clicked.connect(lambda: self._copy_failures(results, task_by_id))
        self.retry_button.clicked.connect(self._retry)
        self.close_button.clicked.connect(self.accept)

    def _copy_failures(self, results, task_by_id):
        lines = []
        for r in results:
            if r.status is not TaskStatus.FAILED:
                continue
            task = task_by_id.get(r.task_id)
            lines.append(f"{task.app_name if task else '?'} → "
                         f"{task.account_username if task else '?'}: "
                         f"{r.error_message or '失败'}")
        text = sanitize("\n".join(lines))
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    def _retry(self):
        from migration_assistant.services.task_runner import failed_tasks
        retries = failed_tasks([t for t in self._all_tasks])
        self.retry_requested.emit(retries)
        self.accept()

    @property
    def _all_tasks(self):
        return getattr(self, "_tasks", [])
