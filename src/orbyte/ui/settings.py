"""设置面板：挂件标题栏齿轮弹出的那一小块。

2026-11-04：主题切换从设置弹窗挪到球右键菜单（用户拍板），这里只剩
通往全部待办、连接账号。开机自启、检查更新、导出也在球右键菜单里，
置顶已于 2026-11-04 删除（所有窗口永远贴桌面底层）。

用 Qt.Popup：点外面自动关，不需要自己装事件过滤器找"点击穿透"。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme

POPUP_W = 268


class SettingsPopup(QDialog):
    """两个跳转 + 状态提示。"""

    openAllRequested = pyqtSignal()
    connectRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(POPUP_W)
        self._build()

    # ---- 布局 ----

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        title = QLabel("设置")
        title.setObjectName("sectionHead")
        root.addWidget(title)

        open_all = QPushButton("打开全部待办")
        open_all.setObjectName("seg")
        open_all.setCursor(Qt.PointingHandCursor)
        open_all.clicked.connect(self._emit_open_all)
        root.addWidget(open_all)

        connect_btn = QPushButton("连接账号")
        connect_btn.setObjectName("seg")
        connect_btn.setCursor(Qt.PointingHandCursor)
        connect_btn.setToolTip("把本机待办同步到日清应用")
        connect_btn.clicked.connect(self._emit_connect)
        root.addWidget(connect_btn)

        self._hint = QLabel("")
        self._hint.setObjectName("footText")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

    # ---- 状态同步 ----

    def set_state(self, mode: str, paired: bool) -> None:
        """mode 参数仅为兼容旧调用保留（主题切换已挪去球菜单）。"""
        self._hint.setText("已连接账号，工作待办会同步" if paired else "当前仅本机，未连接账号")

    # ---- 交互 ----

    def _emit_open_all(self) -> None:
        self.close()
        self.openAllRequested.emit()

    def _emit_connect(self) -> None:
        self.close()
        self.connectRequested.emit()

    # ---- 弹出定位 ----

    def popup_at(self, anchor: QWidget) -> None:
        """贴在齿轮按钮的右下方弹出，超出屏幕就往回收。"""
        frame = anchor.mapToGlobal(anchor.rect().bottomLeft())
        self.adjustSize()
        x = frame.x() + anchor.width() - self.width()
        y = frame.y() + 8
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            x = min(max(x, screen.left() + 8), screen.right() - self.width() - 8)
            y = min(y, screen.bottom() - self.height() - 8)
        self.move(x, y)
        self.show()

    # ---- 绘制 ----

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        theme.paint_glass(self, 12)

    def resizeEvent(self, event) -> None:  # noqa: N802
        theme.apply_round_mask(self, 12)
        super().resizeEvent(event)


def _repolish(widget: QWidget) -> None:
    """改 property 之后必须重抛光，否则 QSS 选择器命中不了新值。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
