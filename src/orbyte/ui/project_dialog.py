"""新建 / 重命名项目的输入框。

不用 QInputDialog：系统默认的灰白方框跟深蓝玻璃界面放一起像两个产品。
项目名是低频操作，但它是"界面是否统一"最容易被一眼看穿的地方。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from . import theme
from .theme import apply_round_mask, paint_glass

RADIUS = 18


class ProjectDialog(QDialog):
    """返回 accept() 时用 `.value` 取输入的项目名。"""

    def __init__(
        self,
        title: str,
        value: str = "",
        hint: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.value = value.strip()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setStyleSheet(theme.GLOBAL_QSS)
        self.setFixedWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        heading = QLabel(title)
        heading.setStyleSheet("font-size:17px;font-weight:600;")
        root.addWidget(heading)

        self._input = QLineEdit(value)
        self._input.setMaxLength(40)
        self._input.setPlaceholderText("例如：权限重构")
        self._input.returnPressed.connect(self._confirm)
        root.addWidget(self._input)

        tip = QLabel(hint or "项目只是待办的分组名，同步时跟着待办一起走。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"font-size:13px;color:{theme.C_TEXT3};")
        root.addWidget(tip)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("ghost")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("确定")
        ok.setObjectName("primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setFixedWidth(96)
        ok.setMinimumHeight(40)
        ok.setDefault(True)
        ok.clicked.connect(self._confirm)
        buttons.addWidget(ok)
        root.addLayout(buttons)

        self._input.selectAll()
        self._input.setFocus()

    def _confirm(self) -> None:
        text = self._input.text().strip()
        if not text:
            self._input.setStyleSheet(f"border:1px solid {theme.C_DANGER};")
            self._input.setFocus()
            return
        self.value = text
        self.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        paint_glass(self, RADIUS)

    def resizeEvent(self, event) -> None:  # noqa: N802
        apply_round_mask(self, RADIUS)
        super().resizeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 贴到父窗口中间（无边框对话框不会自己居中）
        parent = self.parent()
        if parent is not None:
            geo = parent.geometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )
