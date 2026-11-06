"""系统托盘图标（无菜单）。

2026-11-04 按用户拍板：托盘只保留图标本身——挂件可以被拖到屏幕外、
被全屏窗口盖住，托盘是"总能找回它"的兜底入口；但右键菜单整块砍掉，
所有功能入口收敛到悬浮球右键菜单和主窗口顶栏。

左键单击 → 叫出 / 收起挂件。
"""

from __future__ import annotations

import logging
import sys

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..paths import ICON_FILE

logger = logging.getLogger("orb.tray")


def _fallback_icon() -> QIcon:
    """没有 ico 时画一个墨色圆点，绝不因为缺图标而崩。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(QColor("#1A1D26"))
    painter.setPen(QColor("#6B7080"))
    painter.drawEllipse(6, 6, 52, 52)
    painter.end()
    return QIcon(pixmap)


class Tray(QObject):
    """托盘图标 / 菜单栏图标。

    Windows（2026-11-04 拍板）：只保留图标本身，无菜单，左键单击切换挂件。
    macOS：菜单栏图标默认语义是"点开菜单"，没有 QMenu 时点击毫无反应；
    而且图标要用 template image，否则深色菜单栏上的黑图标看不见。
    """

    toggleWidget = pyqtSignal()
    #: 仅 macOS 使用：菜单里的「全部待办」与「退出」
    requestWindow = pyqtSignal()
    requestQuit = pyqtSignal()

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        icon = QIcon(str(ICON_FILE)) if ICON_FILE.exists() else _fallback_icon()
        if icon.isNull():
            icon = _fallback_icon()

        self._tray = QSystemTrayIcon(icon, app)
        self._tray.setToolTip("ORBYTE · 待办")
        self._tray.activated.connect(self._on_activated)

        if sys.platform == "darwin":
            # 这里**不用** setIsMask：mask 会按 alpha 通道把图标渲染成单色剪影，
            # 而 ORBYTE 的图标是彩色狗头（整块圆脸都是不透明的），加了 mask
            # 就变成菜单栏上一个实心圆块，比彩色更难看。彩色图在浅色/深色
            # 菜单栏上都能分辨。
            self._tray.setContextMenu(self._build_menu())
            logger.info("菜单栏图标已就绪（带菜单）")
        else:
            logger.info("托盘图标已就绪（无菜单）")

        self._tray.show()

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.addAction("显示 / 隐藏挂件", self.toggleWidget.emit)
        menu.addAction("全部待办", self.requestWindow.emit)
        menu.addSeparator()
        menu.addAction("退出 ORBYTE", self.requestQuit.emit)
        return menu

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason != QSystemTrayIcon.Trigger:
            return
        if sys.platform == "darwin":
            # mac 上单击图标是"展开菜单"，Qt 同时也会发 Trigger。
            # 这里再 toggle 一次就变成"点一下既开菜单又关挂件"，所以不响应。
            return
        self.toggleWidget.emit()

    def hide(self) -> None:
        self._tray.hide()
