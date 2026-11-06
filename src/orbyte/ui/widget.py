"""桌面挂件（悬浮球展开的那块面板）。

2026-11-04 第五版（用户拍板）：

- 顶栏 = 工作 / 生活 / 暂存 三个页签，右侧 主页 / 刷新 / 关闭
  （设置齿轮已删——主题切到球右键菜单，其余入口在主页）
- 「刷新」= 手动同步：点一下立刻推+拉一轮，不等空闲轮询
- 待办页 = 全部行直接平铺（一级项目分组头与组头＋号已删），超出出滚轮
- 行 = 单行：优先级徽章 + 待办内容 + 项目后缀 + 状态按钮
- 宽度 926 = 712×1.3（高度不变），字号一律没动
"""

from __future__ import annotations

import math
from functools import partial
from typing import Callable

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import todo_store
from . import theme
from .scratch import ScratchPage

#: 宽度 926 = 712×1.3（2026-11-04 用户拍板：高度不变加宽 30%）；
#: 高度 915 不变，内容超出走滚轮
DEFAULT_W = 926
DEFAULT_H = 915
#: 高度上限：自适应收缩的上界，也是尺寸记忆传回旧值时的钳制值
MAX_H = 915
#: 高度下限 350：内容少时窗口仍能收缩贴合内容
MIN_W, MIN_H = 806, 350

#: 行高 72 = 上 17 + 状态按钮 38 + 下 17（单行版：徽章挪进首行）
ROW_H = 72
#: 行之间用 1px 分隔线切分，不再留间隙
ROW_GAP = 0
#: 行左右内边距
ROW_PAD = 26
#: 标题栏高度
HEADER_H = 72
#: 状态按钮 / 优先级徽章
STATUS_H = 38
CHIP_H = 28
#: 完成后停留多久再移出列表（有下拉可回退，不必抢那 0.7 秒）
DONE_HOLD_MS = 900

_STATE_LABEL = {"todo": "未开始", "doing": "进行中", "done": "已完成"}
_STATE_ORDER = ("todo", "doing", "done")

#: 排序用的优先级档位：P0 最先，未设置垫底；同档保持原有顺序（稳定排序）
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _priority_rank(item: dict) -> int:
    return _PRIORITY_RANK.get((item.get("priority") or "").strip().upper(), 3)


# ---- 基础绘制件 ----


class _IconButton(QAbstractButton):
    """矢量图标按钮：home / gear / close / plus / chevron。

    不引图标文件、也不依赖字体里的特殊字符——字形在不同系统上会缺字，
    还会跟着字体一起变灰。这里全部用 QPainter 画，颜色直接读当前主题，
    切主题不需要重新生成任何资源。
    """

    def __init__(
        self,
        kind: str,
        size: int = 26,
        tip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self._active = False
        self._expanded = True
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        if tip:
            self.setToolTip(tip)
            self.setAccessibleName(tip)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.update()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def _color(self) -> QColor:
        if self._active:
            return QColor(theme.C_PRIMARY)
        return QColor(theme.C_TEXT if self._hover else theme.C_TEXT2)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # 图标坐标按 26px 按钮手绘；按钮变大时整体等比缩放，
        # 线宽、圆角、笔画一起放大，不会出现"按钮大了图标还是小的"
        s = self.width() / 26.0
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.scale(s, s)
        painter.translate(-13.0, -13.0)
        if self._hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.C_SURFACE))
            painter.drawRoundedRect(QRectF(1, 1, 24, 24), 7, 7)

        color = self._color()
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        cx, cy = 13.0, 13.0
        kind = self.kind

        if kind == "close":
            painter.drawLine(QPointF(cx - 4.6, cy - 4.6), QPointF(cx + 4.6, cy + 4.6))
            painter.drawLine(QPointF(cx + 4.6, cy - 4.6), QPointF(cx - 4.6, cy + 4.6))
        elif kind == "plus":
            painter.drawLine(QPointF(cx - 5.0, cy), QPointF(cx + 5.0, cy))
            painter.drawLine(QPointF(cx, cy - 5.0), QPointF(cx, cy + 5.0))
        elif kind == "chevron":
            if self._expanded:
                # 展开：向下（内容在这行下面）
                painter.drawPolyline(
                    QPolygonF(
                        [
                            QPointF(cx - 3.6, cy - 1.7),
                            QPointF(cx, cy + 1.7),
                            QPointF(cx + 3.6, cy - 1.7),
                        ]
                    )
                )
            else:
                # 折叠：向右（▸ 是"还能展开"的通用语汇，向上容易被读成"收起"）
                painter.drawPolyline(
                    QPolygonF(
                        [
                            QPointF(cx - 1.7, cy - 3.6),
                            QPointF(cx + 1.7, cy),
                            QPointF(cx - 1.7, cy + 3.6),
                        ]
                    )
                )
        elif kind == "pin":
            # 图钉：圆头 + 针身 + 底座横线，缩到 16px 也还能认出是"钉住"
            painter.setBrush(color if self._active else Qt.NoBrush)
            painter.drawEllipse(QPointF(cx, cy - 3.4), 3.0, 3.0)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(cx, cy - 0.4), QPointF(cx, cy + 4.4))
            painter.drawLine(QPointF(cx - 2.6, cy + 4.6), QPointF(cx + 2.6, cy + 4.6))
        elif kind == "home":
            # 主页：屋顶 + 墙体 + 门。挂件标题栏左侧第一个按钮，
            # 点击展开主窗口（原「置顶」图钉已按用户要求替换）
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(cx - 8.0, cy + 0.4),
                        QPointF(cx, cy - 7.2),
                        QPointF(cx + 8.0, cy + 0.4),
                    ]
                )
            )
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(cx - 5.6, cy - 1.4),
                        QPointF(cx - 5.6, cy + 7.4),
                        QPointF(cx + 5.6, cy + 7.4),
                        QPointF(cx + 5.6, cy - 1.4),
                    ]
                )
            )
            painter.drawLine(QPointF(cx, cy + 7.4), QPointF(cx, cy + 2.6))
        elif kind == "gear":
            painter.drawEllipse(QRectF(cx - 3.4, cy - 3.4, 6.8, 6.8))
            for index in range(8):
                angle = index * math.pi / 4.0
                painter.drawLine(
                    QPointF(cx + 4.8 * math.cos(angle), cy + 4.8 * math.sin(angle)),
                    QPointF(cx + 6.5 * math.cos(angle), cy + 6.5 * math.sin(angle)),
                )
        elif kind == "refresh":
            # 手动同步：粗一点的主弧 + 实心箭头贴在弧线末端、指向顺时针
            # 切向（缺口在右侧，箭头从下方扫上来），是各系统「刷新」的
            # 通用语汇；旧版细折线箭头方向感差，已弃
            r = 6.2
            painter.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), -40 * 16, -280 * 16)
            # 弧线终点在 40°（右上）：P + 顺时针切向箭头（实心三角）
            ar = math.radians(40.0)
            px = cx + r * math.cos(ar)
            py = cy - r * math.sin(ar)
            dx, dy = math.sin(ar), math.cos(ar)          # 切向（右下）
            nx, ny = math.cos(ar), -math.sin(ar)         # 法向
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(px + nx * 2.6, py + ny * 2.6),
                        QPointF(px + dx * 4.6, py + dy * 4.6),
                        QPointF(px - nx * 2.6, py - ny * 2.6),
                    ]
                )
            )
        painter.end()


class _ElidedLabel(QLabel):
    """单行标题，宽度不够时省略并保留完整提示。

    QLabel 自己不会省略，长标题会把状态圈挤出面板；这里在 resize
    时按当前字体宽度重算一次。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setObjectName("widTitle")
        self.setMinimumWidth(40)
        # 不要在这里预切：此刻还没布局，width() 是默认值，切完 sizeHint
        # 就只剩"灰…"的宽度，布局从此只肯给这么宽，再也长不回来
        QLabel.setText(self, text)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._full) + 2, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, super().minimumSizeHint().height())

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._relayout()

    def _relayout(self) -> None:
        metrics = self.fontMetrics()
        available = max(20, self.width() - 2)
        QLabel.setText(self, metrics.elidedText(self._full, Qt.ElideRight, available))

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._relayout()
        super().resizeEvent(event)



class _StatusButton(QAbstractButton):
    """描边式状态按钮：文案 + chevron（1.6.0 设计稿）。

    1.5 及以前是「点圈循环三态」，没有回退路径——点成完成就得等它自己
    消失。改成显式下拉后可以直接跳到任意状态，代价是多一次点击。
    """

    def __init__(self, state: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(STATUS_H)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip("切换状态")

    def _label(self) -> str:
        return _STATE_LABEL.get(self._state, "未开始")

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        self._state = state
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        f = QFont(self.font())
        f.setPixelSize(20)
        fm = QFontMetrics(f)
        return QSize(fm.horizontalAdvance(self._label()) + 62, STATUS_H)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(theme.C_LINE), 1))
        p.setBrush(QColor(theme.C_SURFACE if self._hover else theme.C_CARD))
        p.drawRoundedRect(r, 10, 10)

        f = QFont(self.font())
        f.setPixelSize(20)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(QColor(theme.C_TEXT2))
        p.drawText(
            self.rect().adjusted(16, 0, -34, 0),
            int(Qt.AlignVCenter | Qt.AlignLeft),
            self._label(),
        )

        pen = QPen(QColor(theme.C_TEXT3), 2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        cx = float(self.width() - 22)
        cy = self.height() / 2.0
        p.drawPolyline(
            QPolygonF(
                [
                    QPointF(cx - 4.8, cy - 2.4),
                    QPointF(cx, cy + 2.4),
                    QPointF(cx + 4.8, cy - 2.4),
                ]
            )
        )
        p.end()


class _StatusMenu(QWidget):
    """状态下拉浮层。

    QMenu 做不出设计稿那种「选中项绿底、无图标槽」的菜单——图标槽固定
    占 45px 压不住，activeAction 的高亮也不稳定。这里用 Qt.Popup 自绘，
    点击别处会自动关闭，不用自己管焦点。
    """

    picked = pyqtSignal(str)

    def __init__(self, current: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("statusPop")

        box = QVBoxLayout(self)
        box.setContentsMargins(6, 6, 6, 6)
        box.setSpacing(1)
        for key in _STATE_ORDER:
            item = QLabel(_STATE_LABEL[key])
            item.setObjectName("menuItem")
            item.setProperty("on", "true" if key == current else "false")
            item.setCursor(Qt.PointingHandCursor)
            item.setMinimumWidth(210)
            item.mouseReleaseEvent = partial(self._pick, key)
            box.addWidget(item)

    def _pick(self, key: str, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.picked.emit(key)
            self.close()

    def popup_at(self, anchor: QWidget) -> None:
        self.adjustSize()
        gp = anchor.mapToGlobal(anchor.rect().bottomRight())
        x = gp.x() - self.width()
        y = gp.y() + 6
        screen = self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
            if y + self.height() > area.bottom():
                y = anchor.mapToGlobal(anchor.rect().topLeft()).y() - self.height() - 6
        self.move(x, y)
        self.show()


class _Row(QFrame):
    """挂件里的一行（2026-11-04 单行版）：

    优先级徽章 + 待办内容 + 项目后缀 …… 状态按钮，一行排完。
    徽章决定先做哪条，放在内容前面第一眼就能扫到；标题超宽只省略标题。
    """

    openRequested = pyqtSignal(str)
    stateRequested = pyqtSignal(str, str)

    def __init__(self, item: dict, running: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self._state = item.get("state") or "todo"
        self.setObjectName("wrow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("running", "true" if running else "false")
        self.setFixedHeight(ROW_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self._tooltip())

        top = QHBoxLayout(self)
        top.setContentsMargins(ROW_PAD, 17, ROW_PAD, 17)
        top.setSpacing(9)

        # 优先级徽章：内容前面（用户拍板：一行放下，徽章在前）；
        # 已完成的条目不再显示徽章（2026-11-04 用户拍板）
        chip = (
            theme.priority_chip(item.get("priority") or "")
            if self._state != "done"
            else None
        )
        self._chip = chip
        if chip is not None:
            chip.setFixedHeight(CHIP_H)
            top.addWidget(chip, 0, Qt.AlignVCenter)

        self._title = _ElidedLabel(theme.item_title(item))
        self._title.setObjectName("rowTitle")
        top.addWidget(self._title, 0, Qt.AlignVCenter)

        project = (item.get("project") or "").strip()
        self._project = QLabel(f"\u00a0 - {project}" if project else "")
        self._project.setObjectName("rowSub")
        # 固定不压缩：位置不够时只让标题省略，项目名不能被吃掉
        self._project.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        top.addWidget(self._project, 0, Qt.AlignVCenter)
        top.addStretch(1)

        self._status = _StatusButton(self._state)
        self._status.clicked.connect(self._open_menu)
        top.addWidget(self._status, 0, Qt.AlignVCenter)

    def _tooltip(self) -> str:
        title = (self.item.get("title") or "").strip() or "未命名待办"
        project = (self.item.get("project") or "").strip()
        path = f"{project} / {title}" if project else title
        return f"{path}\n点标题看详情 · 点右侧按钮切状态"

    def _open_menu(self) -> None:
        menu = _StatusMenu(self._state, self.window())
        menu.picked.connect(lambda key: self.stateRequested.emit(self.item["uuid"], key))
        menu.popup_at(self._status)

    def apply_state(self, state: str) -> None:
        """把状态落到视觉上（持久化由 Widget 负责）。"""
        self._state = state
        self._status.set_state(state)
        self.setProperty("running", "true" if state == "doing" else "false")
        _repolish(self)
        # 已完成自动隐藏优先级徽章（2026-11-04 用户拍板）
        if getattr(self, "_chip", None) is not None:
            self._chip.setVisible(state != "done")
        if state == "done":
            self._title.setObjectName("titleDone")
            _repolish(self._title)
            theme.strike(self._title)
        else:
            self._title.setObjectName("rowTitle")
            _repolish(self._title)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.openRequested.emit(self.item["uuid"])
            event.accept()
            return
        super().mouseReleaseEvent(event)


class Widget(theme.GlassWindow):
    """常驻挂件（悬浮球点开的面板）。"""

    expandRequested = pyqtSignal()
    openDetailRequested = pyqtSignal(str)
    changed = pyqtSignal()
    closeRequested = pyqtSignal()
    connectRequested = pyqtSignal()
    #: 手动同步：点标题栏刷新按钮立刻推+拉一轮
    refreshSyncRequested = pyqtSignal()

    def __init__(
        self,
        on_moved: Callable[[int, int], None] | None = None,
        on_resized: Callable[[int, int], None] | None = None,
        size: tuple[int, int] | None = None,
        pinned: bool = False,
    ) -> None:
        super().__init__(radius=20, on_moved=on_moved)
        # pinned 参数仅保留兼容旧调用（主窗口仍传 config 里的旧开关），
        # 置顶功能已删：所有窗口永远贴桌面底层
        self._on_resized = on_resized
        self._sync_state = "idle"
        # 顶栏页签：工作 / 生活 / 暂存（暂存 2026-11-04 从主窗口挪进弹窗）
        self._space = todo_store.SPACE_WORK
        self._page = "todos"

        # 尺寸记忆：resize 会在构造过程中触发 resizeEvent，
        # 所以定时器必须先建好，不能等到 _build() 之后
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._persist_size)

        width, height = size or (DEFAULT_W, DEFAULT_H)
        self.setMinimumSize(MIN_W, MIN_H)
        # 宽度按新版口径至少 DEFAULT_W：尺寸记忆里存的还是旧的 712，
        # 不钳的话加宽 30% 重启后一点没变。高度照旧钳到 MAX_H
        self.resize(max(DEFAULT_W, width), min(max(MIN_H, height), MAX_H))

        self._build()
        self.refresh()

    # ---- 布局 ----

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QFrame()
        head.setObjectName("wheader")
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setFixedHeight(HEADER_H)
        header = QHBoxLayout(head)
        header.setContentsMargins(ROW_PAD, 0, ROW_PAD, 0)
        header.setSpacing(10)

        # 顶栏页签：工作 / 生活 / 暂存（原「待办事项」标题按口径让位）
        self._tab_work = QPushButton("工作")
        self._tab_life = QPushButton("生活")
        self._tab_scratch = QPushButton("暂存")
        for btn in (self._tab_work, self._tab_life, self._tab_scratch):
            btn.setObjectName("wtab")
            btn.setCursor(Qt.PointingHandCursor)
            header.addWidget(btn, 0, Qt.AlignVCenter)
        self._tab_work.clicked.connect(lambda: self._switch_space(todo_store.SPACE_WORK))
        self._tab_life.clicked.connect(lambda: self._switch_space(todo_store.SPACE_LIFE))
        self._tab_scratch.clicked.connect(self._switch_scratch)
        header.addStretch(1)

        # 手动同步：不等空闲轮询，点一下立刻推+拉一轮
        self._refresh_btn = _IconButton("refresh", 40, "手动同步")
        self._refresh_btn.clicked.connect(self.refreshSyncRequested.emit)
        header.addWidget(self._refresh_btn, 0, Qt.AlignVCenter)

        self._home_btn = _IconButton("home", 40, "打开主页")
        self._home_btn.clicked.connect(self.expandRequested.emit)
        header.addWidget(self._home_btn, 0, Qt.AlignVCenter)

        # 竖线：把「主页」和「关闭」分开——关窗是不可撤销的动作，
        # 跟另外两个不同性质
        sep = QFrame()
        sep.setObjectName("hdline")
        sep.setFixedSize(1, 22)
        header.addWidget(sep, 0, Qt.AlignVCenter)

        self._close_btn = _IconButton("close", 40, "关闭面板")
        self._close_btn.clicked.connect(self.closeRequested.emit)
        header.addWidget(self._close_btn, 0, Qt.AlignVCenter)

        root.addWidget(head)

        # 页面栈：0=待办（工作/生活共用），1=暂存
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_todo_page())
        self._scratch = ScratchPage()
        self._stack.addWidget(self._scratch)
        root.addWidget(self._stack, 1)

        # 缩放抓手浮在右下角，不占布局高度
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(14, 14)
        self._grip.setToolTip("拖动调整面板大小")
        self._grip.raise_()
        self._grip.show()

    def _build_todo_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        # _fit_height 靠它算 chrome（窗口高 - 列表高）
        self._list_page = scroll

        holder = QWidget()
        body = QVBoxLayout(holder)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._rows_box = QVBoxLayout()
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(ROW_GAP)
        body.addLayout(self._rows_box)
        body.addStretch(1)
        scroll.setWidget(holder)
        col.addWidget(scroll, 1)

        # 底部常驻添加栏（原型 addbar）：输入 + 项目 + 添加
        addbar = QFrame()
        addbar.setObjectName("wfootbar")
        addbar.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(addbar)
        row.setContentsMargins(ROW_PAD, 12, ROW_PAD, 12)
        row.setSpacing(8)
        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText("＋ 添加待办，回车确认")
        self._title_input.setMaxLength(200)
        self._title_input.returnPressed.connect(self._add)
        row.addWidget(self._title_input, 1)
        self._project_input = QComboBox()
        self._project_input.setEditable(True)
        self._project_input.setFixedWidth(150)
        self._project_input.lineEdit().setPlaceholderText("项目")
        row.addWidget(self._project_input)
        add = QPushButton("添加")
        add.setObjectName("add")
        add.setFixedHeight(44)
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._add)
        row.addWidget(add)
        col.addWidget(addbar)
        return page

    # ---- 渲染 ----

    def refresh(self) -> None:
        """重画待办页（全部行平铺）与页签态。

        2026-11-04 口径：已完成条目不显示；排序按优先级 P0→P1→P2→未设置
        （稳定排序，同档保持原有顺序）；超出窗口高度出滚轮。
        """
        active = todo_store.active_info()
        active_uuid = active.get("uuid") if active else None

        # 折叠/展开会整页重建列表：重建瞬间滚动条最大值先归零再回升，
        # 原位置会被 Qt 钳到底部（用户看到的「点收紧就跳到页面底部」）。
        # 记住重建前的位置，等高度自适应调完后再恢复。
        scrollbar = self._list_page.verticalScrollBar()
        anchor = scrollbar.value()

        _clear_layout(self._rows_box)
        self._refresh_projects()
        self._paint_tabs()

        if self._space == todo_store.SPACE_WORK:
            # 分组头已删：跨项目收拢成一份平铺清单，再按优先级排
            items = [
                item
                for group in todo_store.projects()
                for item in group["items"]
                if item.get("state") != todo_store.STATE_DONE
            ]
            empty_text = "今天还没有待办\n在下面输入框记一条"
        else:
            items = [
                item
                for item in todo_store.list_todos(todo_store.SPACE_LIFE)
                if item.get("state") != todo_store.STATE_DONE
            ]
            empty_text = "生活待办只存本机，不会上传"

        items.sort(key=_priority_rank)  # 稳定：同优先级保持原顺序
        for item in items:
            running = bool(active_uuid and item["uuid"] == active_uuid)
            row = _Row(item, running=running)
            row.openRequested.connect(self.openDetailRequested.emit)
            row.stateRequested.connect(partial(self._set_state, row))
            self._rows_box.addWidget(row)

        if not items:
            empty = QLabel(empty_text)
            empty.setObjectName("hint")
            empty.setAlignment(Qt.AlignCenter)
            empty.setContentsMargins(0, 24, 0, 24)
            self._rows_box.addWidget(empty)

        # 生活页没有项目概念，添加栏只留输入 + 添加
        self._project_input.setVisible(self._space == todo_store.SPACE_WORK)
        # 高度自适应只跟「工作待办」页走：切生活/暂存保持窗口大小不变
        # （用户拍板：三个页面界面大小要一致，不能切个页签窗口就缩一截）
        if self._page == "todos" and self._space == todo_store.SPACE_WORK:
            self._fit_height_soon()
        # _fit_height 排在前面（FIFO），这里等它把窗口高度调完再回位
        QTimer.singleShot(0, lambda: scrollbar.setValue(min(anchor, scrollbar.maximum())))

    def _refresh_projects(self) -> None:
        current = self._project_input.currentText()
        self._project_input.blockSignals(True)
        self._project_input.clear()
        self._project_input.addItem("")
        for name, items in todo_store.groups(todo_store.SPACE_WORK):
            if items:
                self._project_input.addItem(name)
        self._project_input.setCurrentText(current)
        self._project_input.blockSignals(False)

    def _paint_tabs(self) -> None:
        for btn, on in (
            (self._tab_work, self._page == "todos" and self._space == todo_store.SPACE_WORK),
            (self._tab_life, self._page == "todos" and self._space == todo_store.SPACE_LIFE),
            (self._tab_scratch, self._page == "scratch"),
        ):
            btn.setProperty("on", "true" if on else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ---- 交互 ----

    def _switch_space(self, space: str) -> None:
        self._page = "todos"
        self._space = space
        self._stack.setCurrentIndex(0)
        self._paint_tabs()
        self.refresh()

    def _switch_scratch(self) -> None:
        self._page = "scratch"
        self._stack.setCurrentIndex(1)
        self._paint_tabs()
        self._scratch.refresh()
        self._fit_height_soon()

    # ---- 高度自适应 ----

    def _fit_height_soon(self) -> None:
        """布局完成后再收缩高度（立刻算拿不到最新 sizeHint）。"""
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self) -> None:
        """窗口高度贴合内容：内容少就缩，多了加滚动，不再留大片空白。

        chrome = 窗口高 - 列表高（标题栏 + 底栏 + 边距，是常量），
        内容高 = 列表内 holder 的 sizeHint（尾部 stretch 贡献 0，正好是紧凑高度）。
        """
        if self._page == "scratch":
            return  # 暂存页固定铺满，不参与高度贴合
        holder = self._list_page.widget()
        if holder is None or not self.isVisible():
            return
        chrome = self.height() - self._list_page.height()
        if chrome <= 0:
            chrome = HEADER_H + 100  # 首次布局前的兜底估算（含底部添加栏）
        target = holder.sizeHint().height() + chrome
        target = max(self.minimumHeight(), min(MAX_H, target))
        if abs(target - self.height()) > 1:
            self.resize(self.width(), target)

    def showEvent(self, event) -> None:  # noqa: N802
        # 首次显示前布局未定，chrome 算不准；显示后再收一次
        super().showEvent(event)
        self._fit_height_soon()

    def hideEvent(self, event) -> None:  # noqa: N802
        # 收起面板前把暂存区未落库的输入立刻保存（自动保存是 800ms 防抖）
        page = getattr(self, "_scratch", None)
        if page is not None:
            page._flush_pending_text()
        super().hideEvent(event)

    def _add(self) -> None:
        title = self._title_input.text().strip()
        if not title:
            self._title_input.setFocus()
            return
        todo_store.add(
            title,
            self._project_input.currentText().strip(),
            space=self._space,
        )
        self._title_input.clear()
        self.refresh()
        self.changed.emit()

    def _set_state(self, row: _Row, item_uuid: str, new_state: str) -> None:
        """下拉里选状态：直接落到目标态，不再靠循环推进。

        1.5 的「点圈循环」没有回退路径，点过头只能等它自己消失；现在
        三态都能直达，所以完成态也不用再抢 0.7 秒的展示窗口了。
        """
        item = todo_store.get_todo(item_uuid)
        if not item:
            return
        if (item.get("state") or "todo") == new_state:
            return
        todo_store.set_state(item_uuid, new_state)
        row.apply_state(new_state)
        self.changed.emit()

        if new_state == "done":
            QTimer.singleShot(DONE_HOLD_MS, self.refresh)
        else:
            # 立刻重排，让「进行中」那条顶到最前面
            QTimer.singleShot(0, self.refresh)

    # ---- 尺寸记忆 ----

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        grip = getattr(self, "_grip", None)
        if grip is not None:
            grip.move(self.width() - grip.width() - 4, self.height() - grip.height() - 4)
        if getattr(self, "_save_timer", None) is not None:
            self._save_timer.start()

    def _persist_size(self) -> None:
        if self._on_resized:
            self._on_resized(self.width(), self.height())

    # ---- 同步状态（面板不再展示，保留接口给上层）----

    def set_sync_status(self, payload: dict) -> None:
        self._sync_state = str(payload.get("state") or "idle")


# ---- 工具 ----


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
