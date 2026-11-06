"""统一样式的轻量提示框。

QMessageBox 在 Windows 上是系统灰白样式，跟界面风格放一起很出戏。
这里只做两件事：说清楚一件事、问一个二选一的问题。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from . import theme
from .theme import apply_round_mask, paint_glass

RADIUS = 18


class MessageDialog(QDialog):
    def __init__(
        self,
        title: str,
        body: str = "",
        primary: str = "知道了",
        secondary: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setStyleSheet(theme.GLOBAL_QSS)
        self.setFixedWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        heading = QLabel(title)
        heading.setStyleSheet("font-size:17px;font-weight:600;")
        root.addWidget(heading)

        if body:
            text = QLabel(body)
            text.setTextFormat(Qt.RichText)
            text.setWordWrap(True)
            text.setStyleSheet(f"font-size:14px;color:{theme.C_TEXT2};line-height:160%;")
            root.addWidget(text)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        if secondary:
            cancel = QPushButton(secondary)
            cancel.setObjectName("ghost")
            cancel.setCursor(Qt.PointingHandCursor)
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)
        ok = QPushButton(primary)
        ok.setObjectName("primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setMinimumWidth(104)
        ok.setMinimumHeight(40)
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        root.addLayout(buttons)

    def paintEvent(self, event) -> None:  # noqa: N802
        paint_glass(self, RADIUS)

    def resizeEvent(self, event) -> None:  # noqa: N802
        apply_round_mask(self, RADIUS)
        super().resizeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        parent = self.parent()
        if parent is not None:
            geo = parent.geometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )


def info(parent, title: str, body: str = "", primary: str = "知道了") -> None:
    MessageDialog(title, body, primary, None, parent).exec_()


def ask(parent, title: str, body: str, primary: str, secondary: str = "取消") -> bool:
    return bool(MessageDialog(title, body, primary, secondary, parent).exec_())


def hint_text(text: str) -> str:
    return f'<span style="color:{theme.C_TEXT3}">{text}</span>'
