"""自绘悬浮球（液态金属质感，全透明窗口）。

为什么不用 WebEngine 渲染这个球：球是两块渐变叠加（径向球体 + 锥向金属带），
Qt 原生画出来比 CSS 更稳——不受 Chromium 合成层影响，60fps 不抖，
而且省掉 107MB 的 Chromium 内核。

窗口是**真透明**的（WA_TranslucentBackground + 无边框），
四角像素与桌面完全一致，不会出现黑色方块。
"""

from __future__ import annotations

import math
import sys
from typing import Callable

from PyQt5.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QConicalGradient,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt5.QtWidgets import QMenu, QWidget

from . import theme

# ---- 配色：与挂件设计稿同一套（冷灰墨黑 + 白）----
# 旧版是赤陶橙球（再往前是深蓝太空球）。设计稿里没有品牌彩，
# 全产品唯一的"重色"就是墨黑，球用墨黑 + 白清单图标，和面板标题一个语气。

C_PRIMARY = QColor("#1A1D26")     # 球体主色（墨黑）
C_PRIMARY_HI = QColor("#343A48")  # 左上受光面
C_PRIMARY_LO = QColor("#10131A")  # 右下背光面
C_ICON = QColor("#FFFFFF")        # 图标与高光
C_SUCCESS = QColor("#4FD07A")
C_WARN = QColor("#E8C489")        # 状态环在深色球上，必须是亮色才可见

#: 球直径（成熟桌面待办的悬浮球都在 56~72 之间，138 会挡住半个屏幕）
ORB_SIZE = 62
#: 四周留白，给柔和阴影
MARGIN = 16
#: 悬浮动画幅度与周期（幅度小一点：球大了晃起来会让人分心）
FLOAT_AMPLITUDE = 3.0
FLOAT_PERIOD_SEC = 5.0


class Orb(QWidget):
    """桌面悬浮球：可拖拽、可点击展开面板。"""

    clicked = pyqtSignal()
    requestPanel = pyqtSignal()
    requestExit = pyqtSignal()
    exportLife = pyqtSignal()
    checkUpdate = pyqtSignal()
    autostartToggled = pyqtSignal(bool)
    #: 主题切换（球右键菜单 → 主题切换子菜单，2026-11-04 从设置弹窗挪来）
    themePicked = pyqtSignal(str)
    #: 拖动结束（x, y）—— 面板要跟着球走，否则球挪到别处、面板还留在原地
    moved = pyqtSignal(int, int)

    def __init__(self, parent: QWidget | None = None, autostart: bool = False) -> None:
        super().__init__(parent)
        self._autostart = bool(autostart)
        if sys.platform == "darwin":
            # macOS：球要盖在其他应用之上（"常驻桌面"的产品前提），
            # 而 Qt.Tool 窗口在 app 失活时会被系统隐藏，两个属性缺一不可：
            # StaysOnTop 决定层级，WA_MacAlwaysShowToolWindow 决定前台换了也还在。
            flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        else:
            flags = Qt.FramelessWindowHint | Qt.WindowStaysOnBottomHint | Qt.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        if sys.platform == "darwin":
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
        self.setFixedSize(ORB_SIZE + MARGIN * 2, ORB_SIZE + MARGIN * 2)
        self.setCursor(Qt.PointingHandCursor)

        self._phase = 0.0
        self._hover = False
        self._pressed_at: QPoint | None = None
        self._drag_offset: QPoint | None = None
        self._dragged = False
        self._sync_state = "idle"

        # 60fps：一个定时器驱动全部动效，避免多个 QPropertyAnimation 互相抢帧
        self._ticker = QTimer(self)
        self._ticker.setInterval(16)
        self._ticker.timeout.connect(self._tick)

        # 悬停缩放（点击后有一个短暂的回弹余韵）
        self._scale = 1.0
        self._scale_anim = QPropertyAnimation(self, b"orbScale", self)
        self._scale_anim.setDuration(280)
        self._scale_anim.setEasingCurve(QEasingCurve.OutCubic)

    # ---- 动效 ----

    def start_animation(self) -> None:
        self._ticker.start()

    def _tick(self) -> None:
        self._phase = (self._phase + 16 / 1000.0) % 1000.0
        self.update()

    @pyqtProperty(float)
    def orbScale(self) -> float:  # noqa: N802 - Qt 属性名
        return self._scale

    @orbScale.setter
    def orbScale(self, value: float) -> None:  # noqa: N802
        self._scale = value
        self.update()

    def _animate_scale(self, target: float) -> None:
        self._scale_anim.stop()
        self._scale_anim.setStartValue(self._scale)
        self._scale_anim.setEndValue(target)
        self._scale_anim.start()

    def set_sync_state(self, state: str) -> None:
        """离线/未配对时球体暗一档，用户一眼能看出「还没同步上去」。"""
        if state != self._sync_state:
            self._sync_state = state
            self.update()

    # ---- 绘制 ----

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 回调名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 悬浮：正弦上下浮动（4.5s 一周期，5px 幅度）
        float_y = math.sin(self._phase / FLOAT_PERIOD_SEC * 2 * math.pi) * FLOAT_AMPLITUDE
        # 呼吸：核心光晕明暗（3s 一周期）
        breath = 0.5 + 0.5 * math.sin(self._phase / 3.0 * 2 * math.pi)

        center = QRectF(
            MARGIN,
            MARGIN + float_y + (1 - self._scale) * ORB_SIZE * 0.5,
            ORB_SIZE * self._scale,
            ORB_SIZE * self._scale,
        )
        dim = 0.55 if self._sync_state in ("offline", "unauthorized") else 1.0

        self._draw_glow(painter, center, breath, dim)
        self._draw_sphere(painter, center, breath, dim)
        self._draw_bands(painter, center, dim)
        self._draw_core(painter, center, breath, dim)

    def _draw_glow(self, painter: QPainter, rect: QRectF, breath: float, dim: float) -> None:
        """落地阴影：球"浮"在桌面上的唯一依据。

        旧版这里是青色外发光。发光是深色球的语言——浅色实心球再发光就会糊成一团，
        换成向下的柔和投影，球才有重量感。
        """
        spread = rect.adjusted(-8, -4, 8, 12)
        shadow = QRadialGradient(spread.center(), spread.width() / 2)
        shadow.setColorAt(0.0, QColor(17, 22, 34, int(52 * dim)))
        shadow.setColorAt(0.55, QColor(17, 22, 34, int(18 * dim)))
        shadow.setColorAt(1.0, QColor(17, 22, 34, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(spread)

    def _draw_sphere(self, painter: QPainter, rect: QRectF, breath: float, dim: float) -> None:
        """球体：赤陶橙实体球，左上受光。

        单一色相的三段渐变就够了。之前那种"深海军蓝 + 六道金属带"是为了做太空质感，
        现在整个产品是纸白极简，球必须是一块干净的实色，靠投影和高光立起来。
        """
        gradient = QRadialGradient(
            rect.center() - QPoint(int(rect.width() * 0.18), int(rect.height() * 0.22)),
            rect.width() * 0.95,
        )
        hi = QColor(C_PRIMARY_HI)
        hi.setAlphaF(dim)
        mid = QColor(C_PRIMARY)
        mid.setAlphaF(dim)
        lo = QColor(C_PRIMARY_LO)
        lo.setAlphaF(dim)
        gradient.setColorAt(0.0, hi)
        gradient.setColorAt(0.55, mid)
        gradient.setColorAt(1.0, lo)
        painter.setBrush(gradient)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(rect)

    def _draw_bands(self, painter: QPainter, rect: QRectF, dim: float) -> None:
        """球心的白色清单图标 + 顶部受光。

        旧版这一层是六道"液态金属带"。浅色球上加金属带会显得脏，
        直接换成产品符号：三条待办横线，一眼认出这是待办工具。
        """
        painter.save()
        clip = QPainterPath()
        clip.addEllipse(rect)
        painter.setClipPath(clip, Qt.IntersectClip)

        w = rect.width()
        center = rect.center()

        # 顶部高光：光源在上方，球面最亮的一块
        spec = QRadialGradient(
            center.x() - w * 0.20, center.y() - w * 0.28, w * 0.48
        )
        spec.setColorAt(0.0, QColor(255, 255, 255, int(64 * dim)))
        spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(spec)
        painter.drawEllipse(
            QPointF(center.x() - w * 0.20, center.y() - w * 0.28), w * 0.48, w * 0.48
        )

        # 底部反光：桌面把光反回球的下缘，没有这道会像贴纸
        bounce = QRadialGradient(center.x(), rect.bottom() - w * 0.10, w * 0.40)
        bounce.setColorAt(0.0, QColor(255, 214, 186, int(52 * dim)))
        bounce.setColorAt(1.0, QColor(255, 214, 186, 0))
        painter.setBrush(bounce)
        painter.drawEllipse(
            QPointF(center.x(), rect.bottom() - w * 0.10), w * 0.40, w * 0.40
        )

        # 上缘 1px 亮边：把球和桌面分开。没有这道边，浅色球在浅色壁纸上会"糊"
        rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
        rim.setColorAt(0.0, QColor(255, 255, 255, int(150 * dim)))
        rim.setColorAt(0.45, QColor(255, 255, 255, int(36 * dim)))
        rim.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(rim, max(1.0, w * 0.018)))
        painter.drawEllipse(rect.adjusted(0.6, 0.6, -0.6, -0.6))
        painter.restore()

        # 清单图标：左侧三个短勾位 + 三条长横
        pen = QPen(QColor(255, 255, 255, int(238 * dim)))
        pen.setWidthF(max(1.7, w * 0.040))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        left = center.x() - w * 0.205
        right = center.x() + w * 0.205
        for dy in (-0.155, 0.0, 0.155):
            y = center.y() + w * dy
            painter.drawLine(QPointF(left, y), QPointF(left + w * 0.052, y))
            painter.drawLine(QPointF(left + w * 0.108, y), QPointF(right, y))

    def _draw_core(self, painter: QPainter, rect: QRectF, breath: float, dim: float) -> None:
        """外圈状态环：一眼看出同步是否正常。

        正常=白色细环；离线=环压暗；需重新配对=橙色环。
        旧版这里是一个"小而极亮的青色光点"，那是太空球的核心光源，
        换成实心球之后中央已经有图标了，再放光点会打架。
        """
        if self._sync_state == "unauthorized":
            ring = QColor(C_WARN)
        elif self._sync_state == "offline":
            ring = QColor(236, 232, 226)
        else:
            ring = QColor(C_ICON)
        ring.setAlphaF((0.66 if self._hover else 0.38) * dim)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(ring, 1.5))
        painter.drawEllipse(rect.adjusted(0.9, 0.9, -0.9, -0.9))

    # ---- 交互 ----

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._animate_scale(1.06)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._animate_scale(1.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._pressed_at = event.globalPos()
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPos() - (self._pressed_at or event.globalPos())
            if abs(delta.x()) > 3 or abs(delta.y()) > 3:
                self._dragged = True
            if self._dragged:
                self.move(event.globalPos() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            was_drag = self._dragged
            self._drag_offset = None
            self._dragged = False
            if was_drag:
                self.moved.emit(self.x(), self.y())
            else:
                self._animate_scale(1.0)
                self.clicked.emit()
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        # 2026-11-04 用户定稿：球右键 = 打开待办 / 导出生活待办 / 检查更新 /
        # 开机自启（勾选）/ 退出。不要分隔线；连接/断开走主窗口顶栏开关。
        menu = QMenu(self)
        # 菜单样式与挂件同一套（白底 + 冷灰），不再单独写一份暖橙
        menu.setStyleSheet(
            "QMenu{background:#FFFFFF;color:#14171F;border:1px solid #E6E9F1;"
            "border-radius:10px;padding:6px;font-size:17px;}"
            "QMenu::item{padding:9px 20px;border-radius:8px;}"
            "QMenu::item:selected{background:#F1F3F7;color:#14171F;}"
        )
        menu.addAction("打开待办", self.requestPanel.emit)
        menu.addAction("导出生活待办…", self.exportLife.emit)
        menu.addAction("检查更新…", self.checkUpdate.emit)

        # 主题切换：子菜单三选一，当前项尾缀 ✓（与「开机自启」同一套
        # 左对齐标记语言，不用 setCheckable）
        sub = QMenu(menu)
        sub.setStyleSheet(menu.styleSheet())
        current = theme.mode()
        for mode in (theme.MODE_LIGHT, theme.MODE_DARK, theme.MODE_SYSTEM):
            mark = " ✓" if mode == current else ""
            sub.addAction(f"{theme.MODE_LABEL[mode]}{mark}",
                          lambda m=mode: self.themePicked.emit(m))
        menu.addMenu(sub).setText("主题切换")

        # 开机自启用「文字尾缀 ✓」标记状态，不用 setCheckable——勾选列会把
        # 这一项的文字顶到右边，和其他项对不齐（用户要求全部左对齐）。
        # macOS 版没有开机自启（system.set_autostart 是空实现），
        # 菜单项留着只会点了没反应，直接不构建。
        if sys.platform != "darwin":
            menu.addAction(
                f"开机自启 {'✓' if self._autostart else ''}".rstrip(),
                lambda: self.autostartToggled.emit(not self._autostart),
            )
        menu.addAction("退出 ORBYTE", self.requestExit.emit)
        menu.exec_(event.globalPos())

    def set_autostart(self, enabled: bool) -> None:
        self._autostart = bool(enabled)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._ticker.stop()
        super().closeEvent(event)
