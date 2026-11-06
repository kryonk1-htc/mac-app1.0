"""6 位配对码窗口。

不用系统消息框：配对码是用户要看清、可能还要抄下来的一串数字，
字号和对比度必须自己控。整个流程只有三种状态——等码、出码、失败，
所以不需要多页面。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from . import theme
from .theme import apply_round_mask, paint_glass

RADIUS = 20


class PairDialog(QDialog):
    openRequested = pyqtSignal(str)
    canceled = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._url = ""
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setStyleSheet(theme.GLOBAL_QSS)
        self.setFixedWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        logo = QLabel()
        logo.setFixedSize(30, 30)
        logo.setStyleSheet(theme.LOGO_STYLE.format(half=15))
        head.addWidget(logo)
        heading = QLabel("连接账号")
        heading.setStyleSheet("font-size:17px;font-weight:600;")
        head.addWidget(heading)
        head.addStretch(1)
        root.addLayout(head)

        self._desc = QLabel(
            "在浏览器里登录后点「允许接入」，这台电脑就能同步<b>工作待办</b>。"
            "生活待办永远只留本机。"
        )
        self._desc.setTextFormat(Qt.RichText)
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet(f"font-size:13.5px;color:{theme.C_TEXT2};")
        root.addWidget(self._desc)

        self._code = QLabel("准备中…")
        self._code.setAlignment(Qt.AlignCenter)
        self._code.setStyleSheet(
            (
                "font-size:14px;color:{};padding:18px;border-radius:12px;"
                "background:{};border:1px solid {};"
            ).format(theme.C_TEXT3, theme.C_SURFACE, theme.C_LINE)
        )
        root.addWidget(self._code)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        self._cancel = QPushButton("取消")
        self._cancel.setObjectName("ghost")
        self._cancel.setCursor(Qt.PointingHandCursor)
        self._cancel.clicked.connect(self._on_cancel)
        buttons.addWidget(self._cancel)
        self._open = QPushButton("打开浏览器授权")
        self._open.setObjectName("primary")
        self._open.setCursor(Qt.PointingHandCursor)
        self._open.setMinimumHeight(40)
        self._open.hide()
        self._open.clicked.connect(lambda: self.openRequested.emit(self._url))
        buttons.addWidget(self._open)
        root.addLayout(buttons)

    def set_code(self, code: str, url: str) -> None:
        self._url = url
        # 配对码要占满视觉：34px + 主色，扫一眼就能念出来
        self._code.setText(
            f'<span style="font-size:34px;font-weight:700;letter-spacing:10px;'
            f'color:{theme.C_PRIMARY}">{code}</span>'
        )
        self._open.show()
        self._cancel.setText("稍后再说")

    def set_error(self, message: str) -> None:
        self._code.setText(f'<span style="color:{theme.C_TEXT2}">{message}</span>')
        self._open.hide()
        self._cancel.setText("关闭")

    def _on_cancel(self) -> None:
        self.canceled.emit()
        self.reject()

    def paintEvent(self, event) -> None:  # noqa: N802
        paint_glass(self, RADIUS)

    def resizeEvent(self, event) -> None:  # noqa: N802
        apply_round_mask(self, RADIUS)
        super().resizeEvent(event)
