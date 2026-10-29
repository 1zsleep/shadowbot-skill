"""账号名/显示名 左右滑动切换开关."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class _TrackButton(QPushButton):
    """滑块轨道: QSS 控制背景色, paintEvent 画圆点."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(48, 24)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        super().paintEvent(event)  # QSS 背景
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        knob_w = 18
        if self.isChecked():
            x = rect.width() - knob_w - 3
        else:
            x = 3
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(x, (rect.height() - knob_w) // 2, knob_w, knob_w)
        painter.end()


class NameModeSwitch(QWidget):
    """左右滑动开关: 左=账号名, 右=显示名 (默认显示名)."""
    mode_changed = Signal(str)  # "account" | "display"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "display"  # 默认显示名

        self.label_left = QLabel("账号名")
        self.label_left.setObjectName("switchLabel")
        self.label_right = QLabel("显示名")
        self.label_right.setObjectName("switchLabel")

        self.track = _TrackButton()
        self.track.setObjectName("modeSwitch")
        self.track.setChecked(True)  # checked=右侧(显示名)
        self.track.clicked.connect(self._on_toggled)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.label_left)
        layout.addWidget(self.track)
        layout.addWidget(self.label_right)

        self._update_labels()

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, emit: bool = True):
        self._mode = mode if mode == "account" else "display"
        self.track.blockSignals(True)
        self.track.setChecked(self._mode == "display")
        self.track.blockSignals(False)
        self.track.update()
        self._update_labels()
        if emit:
            self.mode_changed.emit(self._mode)

    def _on_toggled(self, checked: bool):
        # checked=True -> 右侧(显示名)
        self._mode = "display" if checked else "account"
        self._update_labels()
        self.mode_changed.emit(self._mode)

    def _update_labels(self):
        if self._mode == "display":
            self.label_right.setProperty("active", True)
            self.label_left.setProperty("active", False)
        else:
            self.label_left.setProperty("active", True)
            self.label_right.setProperty("active", False)
        for lbl in (self.label_left, self.label_right):
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
