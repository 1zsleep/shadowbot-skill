"""迁移进度面板."""
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit,
                               QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from migration_assistant.logging_setup import sanitize
from migration_assistant.models import STAGE_LABELS, TaskStage

_MAX_LOG_ROWS = 500


class ProgressPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("progressPanel")

        self.total_label = QLabel("0 / 0")
        self.total_label.setObjectName("cardTitle")
        self.current_label = QLabel("准备中…")
        self.current_label.setObjectName("currentLabel")
        self.stage_label = QLabel("")
        self.stage_label.setObjectName("stageLabel")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.stop_button = QPushButton("停止迁移")
        self.stop_button.setObjectName("stopBtn")

        top = QHBoxLayout()
        top.addWidget(QLabel("迁移进度"))
        top.addStretch()
        top.addWidget(self.total_label)
        top.addWidget(self.stop_button)

        info = QHBoxLayout()
        info.addWidget(self.current_label, 1)
        info.addWidget(self.stage_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(_MAX_LOG_ROWS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.addLayout(top)
        layout.addLayout(info)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_view, 1)

    def set_total(self, total: int):
        self._total = max(total, 1)
        self._done = 0
        self.progress_bar.setRange(0, self._total)
        self.progress_bar.setValue(0)
        self.total_label.setText(f"0 / {self._total}")

    def on_task_started(self, task, index, total):
        self.total_label.setText(f"{index} / {total}")
        self.current_label.setText(f"{task.app_name} → {task.account_username}")
        self.stage_label.setText(STAGE_LABELS.get(task.stage, task.stage))

    def on_stage(self, task, stage: TaskStage):
        self.stage_label.setText(STAGE_LABELS.get(stage, stage))

    def on_task_finished(self, result):
        self._done += 1
        self.total_label.setText(f"{self._done} / {self._total}")
        self.progress_bar.setValue(self._done)

    def append_log(self, text: str):
        self.log_view.appendPlainText(sanitize(text))

    def reset(self):
        self._total = 1
        self._done = 0
        self.total_label.setText("0 / 0")
        self.current_label.setText("准备中…")
        self.stage_label.setText("")
        self.progress_bar.setValue(0)
        self.log_view.clear()
