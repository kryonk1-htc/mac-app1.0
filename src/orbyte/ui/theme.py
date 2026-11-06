"""视觉基座：双主题（浅色 / 深色）+ 统一 QSS + 公共小控件。

以前颜色是模块级常量、QSS 是 import 时算好的 f-string，结果是：
主题只能在启动时定死，运行时切换会有一半控件不变色（值已经被
`from .theme import C_TEXT2` 静态绑定了）。

现在只有两条规则：

1. 任何模块引用颜色都写 `theme.C_TEXT2`，不许 `from .theme import C_TEXT2`
   —— 前者是属性查找（每次取当前值），后者是启动那一刻值的拷贝。
2. 切主题只做一件事：`theme.apply(mode)` —— 重设令牌、重建 QSS、
   下发 QApplication、通知订阅者重画。窗口自己不记主题。

色值取自《桌面待办组件设计说明》的视觉参数建议表，深浅一一对应。
"""

from __future__ import annotations

import math
import string
import sys
from datetime import date, datetime, timezone
from typing import Callable

from PyQt5.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QRegion,
)
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

# ---- 主题定义 ----

MODE_LIGHT = "light"
MODE_DARK = "dark"
MODE_SYSTEM = "system"
MODES = (MODE_LIGHT, MODE_DARK, MODE_SYSTEM)

MODE_LABEL = {MODE_LIGHT: "浅色", MODE_DARK: "深色", MODE_SYSTEM: "跟随系统"}

#: 平台判断集中在这里：UI 层不再各自写 sys.platform
_IS_MAC = sys.platform == "darwin"

PALETTES: dict[str, dict] = {
    # 全部色值按设计稿截图逐像素实测（PIL 采样，非目测）：
    # 底色众数、文字取区域内最暗 2% 均值、徽章取非白众数、勾取最饱和绿。
    MODE_LIGHT: {
        "bg": "#FFFFFF",
        "card": "#FFFFFF",
        "surface": "#F3F4F7",       # 悬停态，实测按钮底 #EBEEF3 提亮一档
        "surface2": "#E9EBF1",      # 状态按钮底（实测 #EBEEF3）
        "line": "#E6E9F1",          # 分隔线（实测，极淡）
        "line_soft": "#EFF1F5",
        # 文字是近黑的冷灰，不是纯黑：#000 在 Windows ClearType 下会发糊
        "text": "#14171F",
        "text2": "#4A4F63",         # 所属项目后缀（实测 #494E69）
        "text3": "#6B7080",         # 图标 / 折叠箭头（实测 #6A6A71、#565973）
        "green": "#0A8F26",         # 完成态 / 勾（实测 #008F1E）
        "green_d": "#066B1C",
        "green_l": "#EBF9F1",       # 下拉选中行底（实测 #EBF9F1）
        "on_primary": "#FFFFFF",
        # 设计稿没有品牌色，唯一的「主色」是墨黑——主按钮/选中页签走深色实心。
        # 绿只留给"完成"，红橙绿只留给优先级，避免语义串色。
        "primary": "#1A1D26",
        "primary_d": "#2A2F3B",
        "primary_l": "#F3F4F7",
        "primary_line": "#DFE3EA",
        "warn": "#6B4400",          # P2 文字（实测 #633E00）
        "danger": "#A62019",        # P1 文字（实测 #A30605）
        "scroll": "#D8DCE4",
        "scroll_hover": "#C3C9D4",
        "shadow": (17, 22, 34),
        "shadow_alpha": (10, 16, 22),
        "top_light": 150,
        "todo": "#6B7080",
        "doing": "#1A1D26",
        "done": "#0A8F26",
        "p0_bg": "#A62019", "p0_fg": "#FFFFFF",
        "p1_bg": "#FDE8E8", "p1_fg": "#A62019",
        "p2_bg": "#FCF2DA", "p2_fg": "#6B4400",
        "p3_bg": "#D0F1DA", "p3_fg": "#0A6116",
        "tooltip_bg": "#1A1D26", "tooltip_fg": "#FFFFFF",
    },
    # 深色版：设计稿只有浅色，这里按同一套冷灰色相推导，
    # 保持「底色越深一档、文字越亮一档」的相对关系，不引入新色相。
    MODE_DARK: {
        "bg": "#16181F",
        "card": "#1B1E26",
        "surface": "#23262F",
        "surface2": "#2C303A",
        "line": "#2E323C",
        "line_soft": "#24272F",
        "text": "#F2F3F7",
        "text2": "#A7ADBE",
        "text3": "#767D8F",
        "green": "#4FD07A",
        "green_d": "#86E0A5",
        "green_l": "#14301F",
        "on_primary": "#14171F",
        # 深底上主色反过来：近白实心 + 深色字
        "primary": "#F0F2F7",
        "primary_d": "#FFFFFF",
        "primary_l": "#2A2E38",
        "primary_line": "#3A3F4B",
        "warn": "#E8C489",
        "danger": "#F1999A",
        "scroll": "#3A404C",
        "scroll_hover": "#4A5160",
        "shadow": (0, 0, 0),
        "shadow_alpha": (40, 50, 66),
        "top_light": 14,
        "todo": "#767D8F",
        "doing": "#F0F2F7",
        "done": "#4FD07A",
        "p0_bg": "#7A1C1C", "p0_fg": "#FDE8E8",
        "p1_bg": "#3B1F20", "p1_fg": "#F1999A",
        "p2_bg": "#3A2E18", "p2_fg": "#E8C489",
        "p3_bg": "#16301F", "p3_fg": "#7BD79A",
        "tooltip_bg": "#F2F3F7", "tooltip_fg": "#16181F",
    },
}

_mode = MODE_SYSTEM
_listeners: list[Callable[[str], None]] = []

# ---- 令牌（由 apply() 重新赋值；外部一律以 theme.C_XXX 形式访问）----

C_BG = "#FFFFFF"
C_CARD = "#FFFFFF"
C_SURFACE = "#F3F4F7"
C_SURFACE2 = "#E9EBF1"
C_LINE = "#E6E9F1"
C_LINE_SOFT = "#EFF1F5"
C_TEXT = "#14171F"
C_TEXT2 = "#4A4F63"
C_TEXT3 = "#6B7080"
C_MUTED = "#4A4F63"
C_PRIMARY = "#1A1D26"
C_PRIMARY_D = "#2A2F3B"
C_PRIMARY_L = "#F3F4F7"
C_PRIMARY_LINE = "#DFE3EA"
C_ON_PRIMARY = "#FFFFFF"
C_SUCCESS = "#0A8F26"
C_WARN = "#6B4400"
C_DANGER = "#A62019"

# 兼容旧名字（主窗口/详情/对话框里还在用）
C_CYAN = C_PRIMARY
C_BLUE = C_PRIMARY
C_GREEN = C_SUCCESS
C_RED = C_DANGER
C_WARN_OLD = C_WARN

STATE_HINT = {"todo": "未开始", "doing": "进行中", "done": "已完成"}
STATE_NEXT = {"todo": "doing", "doing": "done", "done": "todo"}
STATE_COLORS = {
    "todo": ("#6B7080", "#6B7080", "#4A4F63"),
    "doing": ("#1A1D26", "#DFE3EA", "#2A2F3B"),
    "done": ("#0A8F26", "#0A8F26", "#066B1C"),
}
PRIORITY_COLORS = {"P0": "#A62019", "P1": "#A62019", "P2": "#6B4400"}


# ---- 主题切换 ----


def _system_is_light() -> bool:
    """读 Windows 应用主题（浅色=1）。读不到就当浅色，不把异常丢给界面。"""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return bool(value)
    except (OSError, ImportError):
        return True


def resolve(mode_value: str | None = None) -> str:
    """把 "system" 解析成实际生效的 light / dark。"""
    value = (mode_value or _mode or MODE_SYSTEM).strip().lower()
    if value not in MODES:
        value = MODE_SYSTEM
    if value == MODE_SYSTEM:
        return MODE_LIGHT if _system_is_light() else MODE_DARK
    return value


def mode() -> str:
    """用户选择的模式（可能是 system）。"""
    return _mode


def palette_mode() -> str:
    """当前真正生效的模式（light / dark）。"""
    return resolve(_mode)


def subscribe(callback: Callable[[str], None]) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def _notify() -> None:
    for callback in list(_listeners):
        try:
            callback(_mode)
        except RuntimeError:
            # 订阅者（窗口）已被销毁，忽略即可
            pass


def _apply_tokens(palette: dict, resolved: str) -> None:
    g = globals()
    g["C_BG"] = palette["bg"]
    g["C_CARD"] = palette["card"]
    g["C_SURFACE"] = palette["surface"]
    g["C_SURFACE2"] = palette["surface2"]
    g["C_LINE"] = palette["line"]
    g["C_LINE_SOFT"] = palette["line_soft"]
    g["C_TEXT"] = palette["text"]
    g["C_TEXT2"] = palette["text2"]
    g["C_TEXT3"] = palette["text3"]
    g["C_MUTED"] = palette["text2"]
    g["C_PRIMARY"] = palette["primary"]
    g["C_PRIMARY_D"] = palette["primary_d"]
    g["C_PRIMARY_L"] = palette["primary_l"]
    g["C_PRIMARY_LINE"] = palette["primary_line"]
    g["C_ON_PRIMARY"] = palette["on_primary"]
    g["C_SUCCESS"] = palette["done"]
    g["C_WARN"] = palette["warn"]
    g["C_DANGER"] = palette["danger"]
    g["C_CYAN"] = palette["primary"]
    g["C_BLUE"] = palette["primary"]
    g["C_GREEN"] = palette["done"]
    g["C_RED"] = palette["danger"]
    g["C_WARN_OLD"] = palette["warn"]
    g["STATE_COLORS"] = {
        "todo": (palette["todo"], palette["text3"], palette["text2"]),
        "doing": (palette["doing"], palette["primary_line"], palette["primary_d"]),
        "done": (palette["done"], palette["green"], palette["green_d"]),
    }
    g["PRIORITY_COLORS"] = {
        "P0": palette["p0_bg"],
        "P1": palette["p1_fg"],
        "P2": palette["p2_fg"],
    }
    g["_ARROW_QSS"] = _arrow_qss(palette["text3"], resolved)
    g["GLOBAL_QSS"] = _build_qss(palette)
    g["LOGO_STYLE"] = (
        "border-radius:{half}px;"
        "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        f"stop:0 {palette['primary_d']},stop:1 {palette['primary']});"
    )


def apply(mode_value: str | None = None) -> str:
    """切换/应用主题：重设令牌、重建 QSS、全局下发、通知订阅者。"""
    global _mode
    value = (mode_value or _mode or MODE_SYSTEM).strip().lower()
    if value not in MODES:
        value = MODE_SYSTEM
    _mode = value
    resolved = resolve(value)
    _apply_tokens(PALETTES[resolved], resolved)

    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(GLOBAL_QSS)
    _notify()
    return value


# ---- 格式化 ----


def fmt_clock(sec: int) -> str:
    """计时用固定宽度 h:mm:ss——每秒跳动时宽度不变，否则整行会抖。"""
    sec = max(0, int(sec))
    hours, rest = divmod(sec, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def fmt_hours(sec: int) -> str:
    """工时口径：<1 分钟不显示，整点不带 0 分（2h 比 2h00m 好读）。"""
    sec = max(0, int(sec))
    if sec < 60:
        return ""
    hours, minutes = divmod(sec // 60, 60)
    if not hours:
        return f"{minutes}m"
    return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"


def elapsed_since(started_at: str | None) -> int:
    """从 ISO 时刻算到现在的秒数；解析失败按 0 处理（不因脏数据崩界面）。"""
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))


def due_label(due: str | None) -> tuple[str, str]:
    """截止日 → (显示文案, 颜色)。今天/逾期用警示色，别让人自己算日期。"""
    text = (due or "").strip()
    if not text:
        return "", C_TEXT3
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return "", C_TEXT3
    today = date.today()
    delta = (parsed - today).days
    if delta < 0:
        return f"{text[5:]} 逾期", C_DANGER
    if delta == 0:
        return "今天到期", C_WARN
    if delta == 1:
        return "明天到期", C_WARN
    if delta <= 7:
        return text[5:], C_TEXT2
    return text[5:], C_TEXT3


def short(text: str, limit: int) -> str:
    clean = (text or "").strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def item_title(item: dict) -> str:
    """挂件行标题：只显示任务名。

    以前拼成 `任务名称 - 上级项目`，360 宽的面板里项目名白占一半宽度，
    标题被压到七八个字就得省略号；项目信息留在 tooltip 和主窗口里。
    """
    return (item.get("title") or "").strip() or "未命名待办"


# ---- 圆角卡片窗口 ----


def paint_glass(widget: QWidget, radius: int) -> None:
    """卡片背景：柔阴影 + 单层描边 + 顶部一层极淡高光。

    深浅共用这套画法，只是令牌不同——深色下阴影更重、高光几乎为零，
    否则深色卡面上会浮着一层灰雾。
    """
    palette = PALETTES[palette_mode()]
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.Antialiasing, True)
    rect = QRectF(widget.rect()).adjusted(1, 1, -1, -1)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)

    painter.setBrush(Qt.NoBrush)
    shadow = QColor(*palette["shadow"])
    for i, alpha in enumerate(palette["shadow_alpha"], start=1):
        shadow.setAlpha(alpha)
        painter.setPen(shadow)
        outer = QRectF(rect).adjusted(-i, -i + 1, i, i + 1)
        outer_path = QPainterPath()
        outer_path.addRoundedRect(outer, radius + i, radius + i)
        painter.drawPath(outer_path)

    painter.setBrush(QColor(palette["card"]))
    painter.setPen(Qt.NoPen)
    painter.drawPath(path)

    painter.save()
    painter.setClipPath(path)
    top_light = QLinearGradient(rect.topLeft(), QRectF(rect).bottomLeft())
    top_light.setColorAt(0.0, QColor(255, 255, 255, palette["top_light"]))
    top_light.setColorAt(0.16, QColor(255, 255, 255, 0))
    painter.setBrush(top_light)
    painter.drawRect(rect)
    painter.restore()

    painter.setBrush(Qt.NoBrush)
    painter.setPen(QColor(palette["line"]))
    painter.drawPath(path)


def apply_round_mask(widget: QWidget, radius: int) -> None:
    if _IS_MAC:
        # macOS 不做 mask：窗口本身已经 WA_TranslucentBackground + 自绘圆角，
        # 再 setMask 会丢掉系统阴影，并在 Retina 屏上把圆弧切出锯齿。
        return
    path = QPainterPath()
    path.addRoundedRect(QRectF(widget.rect()), radius, radius)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


class GlassWindow(QWidget):
    """无边框 + 圆角 + 卡片背景。

    Windows 上无边框窗口必须自己裁圆角（setMask），否则四角会露出方形底色；
    也不能用 windowOpacity 做淡入——配合 setMask 时部分环境透明度回不来。
    """

    def __init__(
        self,
        radius: int = 22,
        on_moved: Callable[[int, int], None] | None = None,
        on_top: bool = False,
    ) -> None:
        super().__init__()
        self._radius = radius
        self._on_moved = on_moved
        self._drag_from: QPoint | None = None
        # 2026-11-04 用户拍板：插件所有窗口永远贴桌面最底层，不遮盖其它应用。
        # on_top 参数仅保留兼容旧签名，已不起作用。
        #
        # macOS 差异（两点，缺一个窗口就"切个应用就没了"）：
        # 1. 没有 WindowStaysOnBottomHint 的实现，带上也不生效；
        # 2. Qt.Tool 窗口在 app 失活时会被系统隐藏，必须用
        #    WA_MacAlwaysShowToolWindow 才保持可见。
        if _IS_MAC:
            flags = Qt.FramelessWindowHint | Qt.Tool
        else:
            flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnBottomHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if _IS_MAC:
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(GLOBAL_QSS)

    # 拖动：只有顶栏空白处 / 标题区域能拖，按钮和输入框自己消费掉事件
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and event.pos().y() < 64:
            self._drag_from = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_from)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is not None:
            self._drag_from = None
            if self._on_moved:
                self._on_moved(self.x(), self.y())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        paint_glass(self, self._radius)

    def resizeEvent(self, event) -> None:  # noqa: N802
        apply_round_mask(self, self._radius)
        super().resizeEvent(event)


# ---- 小控件 ----


class StateDot(QWidget):
    """三态圆点（主窗口/详情仍在使用），点击切到下一态。"""

    clicked = pyqtSignal()

    def __init__(self, state: str, size: int = 20, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._phase = 0.0
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(STATE_HINT.get(state, "") + " · 点击切换")
        self._timer: QTimer | None = None
        if state == "doing":
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(40)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.08) % (2 * math.pi)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        main, _light, dark = STATE_COLORS.get(self._state, STATE_COLORS["todo"])
        rect = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)

        if self._state == "doing":
            glow = 0.45 + 0.35 * (0.5 + 0.5 * math.sin(self._phase))
            halo = QColor(main)
            halo.setAlphaF(glow * 0.20)
            painter.setPen(Qt.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(rect.adjusted(-2.5, -2.5, 2.5, 2.5))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(main))
        painter.drawEllipse(rect)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QColor(dark))
        painter.drawEllipse(rect)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()


class BreathingDot(QWidget):
    """详情页"计时中"的呼吸绿点。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._phase = 0.0
        self.setFixedSize(10, 10)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.1) % (2 * math.pi)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        alpha = 0.45 + 0.45 * (0.5 + 0.5 * math.sin(self._phase))
        halo = QColor(C_GREEN)
        halo.setAlphaF(alpha * 0.35)
        painter.setBrush(halo)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(0, 0, 10, 10))
        core = QColor(C_GREEN)
        core.setAlphaF(min(1.0, alpha + 0.3))
        painter.setBrush(core)
        painter.drawEllipse(QRectF(3, 3, 4, 4))


def priority_chip(priority: str) -> QLabel | None:
    """P0..P2 小徽标；未设置优先级时不占位（空白比灰块更干净）。"""
    value = (priority or "").strip().upper()
    if value not in PRIORITY_COLORS:
        return None
    label = QLabel(value)
    label.setObjectName("pri")
    label.setProperty("pri", value.lower())
    label.setAlignment(Qt.AlignCenter)
    label.setFixedHeight(22)
    label.setMinimumWidth(32)
    return label


def _apply_strike(label: QLabel) -> None:
    font = label.font()
    if font.strikeOut():
        return
    font.setStrikeOut(True)
    label.setFont(font)


class _StrikeKeeper(QObject):
    """把删除线"焊"在 label 上：polish / 显示 / 样式变化时重贴一次。

    起因：全局样式表里只要写了 font-size，Qt 就会在 polish 阶段整份替换
    widget 的 QFont —— strikeOut 标志一起被丢。polish 发生在 show 前后
    （比构造晚），所以只在构造时设一次会出现"同屏有的行有删除线、有的没有"。
    """

    def __init__(self, label: QLabel) -> None:
        super().__init__(label)
        self._label = label
        self._pending = False

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Polish, QEvent.Show, QEvent.StyleChange):
            self._schedule()
        return False

    def _schedule(self) -> None:
        if self._pending:
            return
        self._pending = True
        QTimer.singleShot(0, self._reapply)

    def _reapply(self) -> None:
        self._pending = False
        try:
            _apply_strike(self._label)
        except RuntimeError:
            pass


def strike(label: QLabel) -> None:
    """完成态删除线（QSS 不支持 text-decoration，只能走 QFont）。"""
    _apply_strike(label)
    keeper = _StrikeKeeper(label)
    label.installEventFilter(keeper)
    label._strike_keeper = keeper  # type: ignore[attr-defined]


# ---- 下拉箭头（QSS 不支持 border 拼三角，只能给图片）----


def _arrow_qss(color: str, mode_name: str) -> str:
    path = _arrow_icon_path(color, mode_name)
    if not path:
        return ""
    return (
        "QComboBox::down-arrow{image:url(%s);width:14px;height:9px;margin-right:8px;}"
        "QDateEdit::down-arrow{image:url(%s);width:14px;height:9px;margin-right:7px;}"
    ) % (path, path)


def _arrow_icon_path(color: str, mode_name: str) -> str:
    """运行时画一张箭头小图存数据目录，避免往安装包里塞资源文件。"""
    from PyQt5.QtCore import QPointF
    from PyQt5.QtGui import QImage, QPolygonF

    from ..paths import DATA_DIR

    path = DATA_DIR / f"arrow_{mode_name}.png"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        image = QImage(14, 9, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon(
            QPolygonF([QPointF(2.0, 2.4), QPointF(12.0, 2.4), QPointF(7.0, 7.6)])
        )
        painter.end()
        image.save(str(path))
    except OSError:
        return ""
    return path.as_posix()


# ---- 全局样式表 ----
# 用 string.Template（$name）而不是 f-string：QSS 满屏大括号，
# f-string 要把每一对写成 {{ }}，漏一个就从那里开始整段失效——
# 上一版"按钮全变纯文字"就是多写了一个右括号。
# Template 里没有花括号语义，改样式时不会踩这个坑。

_QSS_TEMPLATE = string.Template(
    """
/* 桌面端字号：正文统一 17px（1.6.0 起按设计稿 1:1 校准，挂件实测
   480×593 @1x，正文墨高 15px ≈ 17px 字号）。
   之前是 15 / 12.5 / 12 三级，100% 缩放下勉强能用，125% 或 150% 缩放时
   Qt 按比例缩放再取整，12px 会糊成一团——这才是"字太小看不清"的根因。
   规矩：正文不低于 15px；徽标/角标例外，最低 13px。 */
QWidget{color:$text;font-family:$fontstack;
    font-size:17px;background:transparent;}
QLabel{background:transparent;}

QLabel#widgetTitle{font-size:24px;font-weight:600;color:$text;}
QLabel#widTitle{font-size:19px;color:$text;}
QLabel#widProj{font-size:17px;color:$text3;}
QLabel#sectionHead{font-size:20px;font-weight:600;color:$text;}
QLabel#sectionCount{font-size:17px;color:$text3;}
QLabel#footText{font-size:14px;color:$text3;}
QLabel#footDoing{font-size:14px;color:$primary;font-weight:600;}
QLabel#meta{font-size:15px;color:$text3;}
QLabel#metaDoing{font-size:15px;color:$primary;font-weight:600;}
QLabel#title{font-size:22px;color:$text;}
QLabel#titleDone{font-size:22px;color:$text2;}
QLabel#pageTitle{font-size:20px;font-weight:600;color:$text;}
QLabel#groupname{font-size:22px;font-weight:600;color:$text;}
QLabel#groupcount{font-size:17px;color:$text3;}
QLabel#groupbar{font-size:17px;color:$text3;}
QLabel#section{font-size:17px;color:$text3;}
QLabel#hint{font-size:22px;color:$text3;}
QLabel#pill{font-size:17px;color:$text2;padding:5px 11px;border-radius:11px;
    background:$surface;border:1px solid $line;}
QLabel#workbox{font-size:17px;color:$text2;background:$surface;
    border:1px solid $line_soft;border-radius:10px;padding:10px 12px;}

/* 挂件行（1.6.0 设计稿）：两行式——首行「标题 + 项目后缀 + 状态按钮」，
   次行「优先级徽章」。行与行之间用 1px 通栏分隔线，不再做内缩圆角块，
   否则 480 宽下每行都像独立卡片，视觉噪点太多。 */
QFrame#wrow{background:transparent;border:none;
    border-bottom:1px solid $line_soft;border-radius:0;}
QFrame#wrow:hover{background:$surface;}
/* 设计稿里「进行中」不铺整行底色，状态只在右侧按钮上体现——
   整行橙色会和 P1 徽章抢注意力，反而看不清哪条在跑 */
QFrame#wrow[running="true"]{background:transparent;}

QLabel#rowTitle{font-size:22px;color:$text;}
QLabel#rowSub{font-size:19px;color:$text3;}
QLabel#sectionSub{font-size:19px;color:$text3;}
QLabel#wfoot{font-size:18px;color:$text3;}

/* 挂件顶栏页签：工作 / 生活 / 暂存（2026-11-04） */
QPushButton#wtab{background:transparent;border:none;border-bottom:2px solid transparent;
    color:$text3;font-size:21px;padding:2px 2px 6px;}
QPushButton#wtab:hover{color:$text2;}
QPushButton#wtab[on="true"]{color:$text;font-weight:600;border-bottom:2px solid $primary;}

/* 分组头的 ＋ 按钮：32×32 圆角 8，描边而非填充 */
QPushButton#wadd{background:$card;border:1px solid $line;border-radius:10px;
    color:$text2;font-size:22px;font-weight:500;padding:0;}
QPushButton#wadd:hover{background:$surface;border:1px solid $text3;color:$text;}

/* 状态下拉浮层里的三行 */
QLabel#menuItem{font-size:21px;color:$text;padding:9px 12px;border-radius:8px;}
QLabel#menuItem:hover{background:$surface;}
QLabel#menuItem[on="true"]{background:$green_l;color:$green_d;font-weight:600;}

/* 挂件骨架：标题栏 / 底栏 / 分区头 / 竖线 / 浮层 */
QFrame#wheader{background:transparent;border:none;
    border-bottom:1px solid $line_soft;}
QFrame#wfootbar{background:transparent;border:none;
    border-top:1px solid $line_soft;}
QFrame#hdline{background:$line;border:none;}
QFrame#wsection{background:transparent;border:none;}
QFrame#wsection[last="true"]{border-bottom:1px solid $line_soft;}
QWidget#statusPop{background:$card;border:1px solid $line;border-radius:14px;}
QPushButton#viewall{background:transparent;border:none;
    border-bottom:1px solid $line_soft;
    color:$text2;font-size:19px;padding:0;}
QPushButton#viewall:hover{background:$surface;color:$text;}
/* 主窗口列表行：和挂件同口径——通栏 + 1px 分隔线，不再是一条条圆角卡片。
   卡片式在 860 宽的窗口里显得零碎，而且每行都自带阴影会把优先级徽章压掉 */
QFrame#row{background:transparent;border:none;
    border-bottom:1px solid $line_soft;border-radius:0;}
QFrame#row:hover{background:$surface;border-bottom:1px solid $line_soft;}
/* 进行中不做整行底色：$primary_l 会把 P1 徽章的浅红底吃掉，两行看起来
   一个样。行首那个橙点已经够标识了 */
QFrame#rowActive{background:transparent;border:none;
    border-bottom:1px solid $line_soft;border-radius:0;}
QFrame#rowFlat{background:transparent;border:none;border-radius:8px;}
QFrame#rowFlat:hover{background:$surface;}
QFrame#card{background:$surface;border:1px solid $line_soft;border-radius:14px;}
QFrame#detailCard{background:transparent;border:none;border-radius:0;}
QFrame#detailCard[wide="false"]{background:transparent;border:none;border-radius:0;}
/* 详情页控件整体加高一档：860 宽的窗口里 30px 的输入框显得小。
   用 padding 撑高而不是 min-height——可编辑 QComboBox 的内部
   QLineEdit 不会跟着 min-height 变高，文字会被裁掉半截。 */
QFrame#detailCard QLineEdit{padding:11px 12px;font-size:19px;}
QFrame#detailCard QComboBox,QFrame#detailCard QDateEdit,
QFrame#detailCard QSpinBox{padding:10px 12px;font-size:19px;}
QLineEdit#detailTitle{font-size:22px;padding:13px 14px;}
QPushButton#seg{min-height:44px;font-size:19px;}
QFrame#sep{background:$line_soft;border:none;}
QLabel#fieldLab{font-size:18px;color:$text3;}
/* 详细内容块：应用「详细说明」只读展示，底色区分可编辑字段 */
QPlainTextEdit#detailBody{background:$surface;border:1px solid $line;
    border-radius:10px;font-size:19px;color:$text2;padding:10px 12px;}

/* 状态入口：已改为自绘圆点，这三个选择器只为兼容旧控件保留 */
QWidget#statusBtn{background:$surface;border:1px solid transparent;border-radius:6px;}
QWidget#statusBtn:hover{background:$surface2;}
QLabel#statusText{font-size:20px;font-weight:500;}

QLabel#pri{font-size:16px;font-weight:600;border-radius:8px;padding:0 11px;
    min-height:32px;}
QLabel#pri[pri="p0"]{color:$p0_fg;background:$p0_bg;}
QLabel#pri[pri="p1"]{color:$p1_fg;background:$p1_bg;}
QLabel#pri[pri="p2"]{color:$p2_fg;background:$p2_bg;}
QLabel#pri[pri="p3"]{color:$p3_fg;background:$p3_bg;}

QPushButton#linkGhost{background:transparent;border:none;color:$text2;font-size:19px;
    padding:4px 8px;border-radius:6px;}
QPushButton#linkGhost:hover{color:$primary;background:$primary_l;}
QPushButton#link{background:transparent;border:none;color:$text2;font-size:19px;
    padding:4px 8px;border-radius:6px;}
QPushButton#link:hover{color:$text;background:$surface;}
QPushButton#icon{background:transparent;border:none;color:$text2;font-size:20px;
    border-radius:8px;}
QPushButton#icon:hover{background:$surface;color:$text;}
QPushButton#del{background:$danger;color:#FFFFFF;border:none;
    border-radius:9px;font-size:17px;font-weight:600;}
QPushButton#del:hover{background:$danger;}

QLineEdit,QComboBox,QDateEdit,QSpinBox{background:$card;
    border:1px solid $line;border-radius:8px;padding:8px 11px;
    font-size:19px;color:$text;selection-background-color:$primary_l;
    selection-color:$text;}
QLineEdit:focus,QComboBox:focus,QDateEdit:focus,QSpinBox:focus{border:1px solid $primary;}
QLineEdit::placeholder{color:$text3;}
QComboBox::drop-down{border:none;width:24px;background:transparent;}
QComboBox QAbstractItemView{background:$card;border:1px solid $line;
    selection-background-color:$primary_l;selection-color:$primary_d;
    color:$text;outline:none;padding:4px;font-size:19px;}
QDateEdit::drop-down{border:none;width:22px;background:transparent;}
QSpinBox::up-button,QSpinBox::down-button{width:0;border:none;}

QPushButton#add{background:$primary;color:$on_primary;border:none;
    border-radius:8px;font-size:19px;font-weight:600;padding:0 14px;}
QPushButton#add:hover{background:$primary_d;}
QPushButton#add:disabled{background:$line;color:$text3;}
QPushButton#ghost{background:transparent;border:1px solid $line;
    border-radius:9px;color:$text2;padding:7px 14px;font-size:19px;}
QPushButton#ghost:hover{background:$surface;border:1px solid $text3;color:$text;}
QPushButton#primary{background:$primary;border:none;border-radius:10px;
    color:$on_primary;padding:11px;font-size:19px;font-weight:600;}
QPushButton#primary:hover{background:$primary_d;}
QPushButton#tab{background:transparent;border:1px solid transparent;border-radius:9px;
    color:$text2;font-size:19px;padding:8px 16px;}
QPushButton#tab:hover{background:$surface;}
/* 选中页签：从「橙色实心胶囊」改成下划线。实心块在浅底上太重，
   一进主窗口第一眼看到的是那块橙色，而不是待办本身 */
QPushButton#tab[on="true"]{background:transparent;color:$text;font-weight:600;
    border:none;border-bottom:2px solid $primary;border-radius:0;}

QPushButton#seg{background:$card;border:1px solid $line;
    border-radius:8px;color:$text2;font-size:17px;padding:7px 12px;}
QPushButton#seg:hover{border:1px solid $text3;}
QPushButton#seg[on="true"]{background:$primary_l;border:1px solid $primary;color:$primary_d;
    font-weight:600;}
QPushButton#seg[on="true"][kind="todo"]{background:$surface;border:1px solid $text3;color:$text;}
/* 状态段一律「浅底 + 语义色描边 + 深色文字」，和选中页签同一套口径。
   实心橙块在这里太重，跟旁边四个输入框完全不在一个重量级 */
QPushButton#seg[on="true"][kind="doing"]{background:$primary_l;border:1px solid $primary;
    color:$primary_d;}
QPushButton#seg[on="true"][kind="done"]{background:$green_l;border:1px solid $green;
    color:$green_d;}

QLabel#timerbar{font-size:17px;color:$primary;padding:8px 12px;border-radius:10px;
    background:$surface;border:1px solid $line;}
QPushButton#timerbar{font-size:17px;color:$primary;text-align:left;padding:9px 12px;
    border-radius:10px;background:$surface;border:1px solid $line;}
QPushButton#timerbar:hover{background:$surface2;}

QProgressBar#group{background:$line_soft;border:none;border-radius:3px;}
QProgressBar#group::chunk{background:$primary;border-radius:3px;}

QScrollArea{background:transparent;border:none;}
QScrollArea > QWidget > QWidget{background:transparent;}
QScrollBar:vertical{background:transparent;width:8px;margin:0;}
QScrollBar::handle:vertical{background:$scroll;border-radius:4px;min-height:32px;}
QScrollBar::handle:vertical:hover{background:$scroll_hover;}
QScrollBar::add-line,QScrollBar::sub-line{height:0;}
QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}

QMenu{background:$card;color:$text;border:1px solid $line;
    border-radius:10px;padding:5px;}
QMenu::item{padding:7px 20px 7px 6px;border-radius:6px;font-size:17px;color:$text;}
QMenu::icon{width:16px;height:16px;left:4px;}
QMenu::item:selected{background:$primary_l;color:$primary_d;}
QMenu::separator{height:1px;background:$line_soft;margin:5px 8px;}

QToolTip{background:$tooltip_bg;color:$tooltip_fg;border:none;
    border-radius:6px;padding:5px 9px;font-size:15px;}

QSizeGrip{background:transparent;width:14px;height:14px;}
QCheckBox{font-size:17px;color:$text;spacing:8px;}
"""
)


#: 字体栈按平台给。Windows 沿用原来那份（行为零变化）；
#: macOS 上 Microsoft YaHei / Segoe UI 都不存在，回落字体和 17px 的排版
#: 基准对不上，所以显式指定系统中文字体。
_FONT_STACK_WIN = "'Microsoft YaHei UI','Segoe UI',sans-serif"
_FONT_STACK_MAC = "'PingFang SC','Helvetica Neue',sans-serif"


def _build_qss(palette: dict) -> str:
    tokens = {key: value for key, value in palette.items() if isinstance(value, str)}
    tokens["fontstack"] = _FONT_STACK_MAC if _IS_MAC else _FONT_STACK_WIN
    return _QSS_TEMPLATE.substitute(**tokens) + _ARROW_QSS


# 导入时先按浅色铺一遍，保证任何窗口在 apply() 之前构造也有完整样式
_apply_tokens(PALETTES[MODE_LIGHT], MODE_LIGHT)
