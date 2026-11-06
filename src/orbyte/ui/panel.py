"""主面板（Qt 原生绘制，深蓝玻璃拟态）。

为什么是原生而不是 HTML：原计划用系统 WebView2 渲染 panel.html，但
WebView2 的 COM vtable 下标在纯 ctypes 下无法可靠定位（渲染入口调不通），
而把 Edge 的 app 窗口嵌进来又会带一条 Chromium 自绘的标题栏。
按计划里写好的降级路径，面板改为 Qt 原生绘制：
体积最小（不带任何浏览器内核）、不依赖目标机装了什么、每一帧都可控。

视觉上仍然照效果图来：深蓝渐变 + 圆角 + 玻璃边框 + 红黄绿三态 + 微动效。
`web/panel.html` 保留为设计源文件，将来若 WebView2 打通可直接换。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Callable

from PyQt5.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QRegion,
)
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, config, todo_store
from ..paths import ensure_dirs

# ---- 设计令牌（与效果图一致）----
C_TEXT = "#DFF3FF"
C_TEXT2 = "#8FB4D4"
C_TEXT3 = "#5C7FA3"
C_LINE = "rgba(30,75,122,150)"
C_CYAN = "#22D7FF"
C_BLUE = "#137BFF"
C_GREEN = "#39E8B4"
C_RED = "#FF526B"
C_WARN = "#FFB648"

PANEL_W, PANEL_H = 720, 860
RADIUS = 26

STATE_COLORS = {
    "todo": (C_RED, "#FF93A4", "#8E1B32"),
    "doing": (C_WARN, "#FFD89A", "#8A5A0B"),
    "done": (C_GREEN, "#9BF6DC", "#116A50"),
}
STATE_HINT = {"todo": "未开始", "doing": "进行中", "done": "已完成"}


def _fmt_spent(sec: int) -> str:
    if not sec or sec < 60:
        return ""
    hours, minutes = divmod(sec // 60, 60)
    return f"已用 {hours}h{minutes}m" if hours else f"已用 {minutes}m"


def _fmt_clock(sec: int) -> str:
    """计时用固定宽度 h:mm:ss——每秒跳动时宽度不变，否则整行会抖。"""
    sec = max(0, int(sec))
    hours, rest = divmod(sec, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _elapsed_since(started_at: str | None) -> int:
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


class StateDot(QWidget):
    """红/黄/绿三态圆点，点击循环切换。"""

    clicked = pyqtSignal()

    def __init__(self, state: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._phase = 0.0
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(STATE_HINT.get(state, ""))
        if state == "doing":
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(40)
        else:
            self._timer = None

    def _tick(self) -> None:
        self._phase = (self._phase + 0.08) % (2 * math.pi)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        main, light, dark = STATE_COLORS.get(self._state, STATE_COLORS["todo"])
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        glow = 0.55
        if self._state == "doing":
            glow = 0.45 + 0.35 * (0.5 + 0.5 * math.sin(self._phase))
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(light))
        gradient.setColorAt(0.55, QColor(main))
        gradient.setColorAt(1.0, QColor(dark))
        painter.setBrush(gradient)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(rect)
        halo = QColor(main)
        halo.setAlphaF(glow * 0.35)
        painter.setBrush(halo)
        painter.drawEllipse(rect.adjusted(-1.5, -1.5, 1.5, 1.5))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class TodoRow(QFrame):
    """一行待办：状态点 + 标题 + 项目 + 工时 + 删除。

    正在计时的行自己跑一个 1 秒定时器刷新时长（而不是靠外层整表刷新：
    整表重建会让滚动位置和鼠标位置都跳，用户正在看的那一刻会闪）。
    """

    stateClicked = pyqtSignal(str)
    deleteClicked = pyqtSignal(str)

    def __init__(
        self,
        item: dict,
        active_started: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self._base_sec = int(item.get("spent_sec") or 0)
        self._started_at = active_started
        self.setObjectName("rowActive" if active_started else "row")
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 11, 10, 11)
        layout.setSpacing(12)

        dot = StateDot(item.get("state", "todo"))
        dot.clicked.connect(lambda: self.stateClicked.emit(self.item["uuid"]))
        layout.addWidget(dot, 0, Qt.AlignVCenter)

        text_box = QVBoxLayout()
        text_box.setSpacing(4)
        title = QLabel(item.get("title") or "")
        done = item.get("state") == "done"
        title.setObjectName("doneTitle" if done else "title")
        if done:
            # Qt 样式表不支持 text-decoration，靠 QFont 才画得出删除线
            font = title.font()
            font.setStrikeOut(True)
            title.setFont(font)
        title.setWordWrap(True)
        text_box.addWidget(title)

        self._meta = QLabel()
        self._meta.setObjectName("meta")
        self._meta.setTextFormat(Qt.RichText)
        text_box.addWidget(self._meta)
        self._paint_meta()
        layout.addLayout(text_box, 1)

        delete = QPushButton("×")
        delete.setObjectName("del")
        delete.setFixedSize(26, 26)
        delete.setCursor(Qt.PointingHandCursor)
        delete.setToolTip("删除")
        delete.clicked.connect(lambda: self.deleteClicked.emit(self.item["uuid"]))
        layout.addWidget(delete, 0, Qt.AlignVCenter)

        self._ticker: QTimer | None = None
        if active_started:
            self._ticker = QTimer(self)
            self._ticker.setInterval(1000)
            self._ticker.timeout.connect(self._paint_meta)
            self._ticker.start()

    def _live_sec(self) -> int:
        return self._base_sec + _elapsed_since(self._started_at)

    def _paint_meta(self) -> None:
        bits: list[str] = []
        if self._started_at:
            # 计时中用秒级时钟 + 琥珀色，和静态工时一眼分得开
            bits.append(
                f'<span style="color:{C_WARN};font-weight:600">'
                f"● {_fmt_clock(self._live_sec())}</span>"
            )
        else:
            spent = _fmt_spent(self._base_sec)
            if spent:
                bits.append(f'<span style="color:{C_TEXT3}">{spent}</span>')
        if self.item.get("project"):
            bits.append(f'<span style="color:#9FD4FF">{self.item["project"]}</span>')
        if self.item.get("space") == "work" and self.item.get("sync_state") == "synced":
            bits.append(f'<span style="color:{C_TEXT3}">已同步</span>')
        self._meta.setText("&nbsp;&nbsp;".join(bits))
        self._meta.setVisible(bool(bits))


class ProjectGroupHeader(QWidget):
    """项目组头（工作区两级结构的第一级）。

    右侧「＋ 添加」把项目名回填到底部输入框并聚焦——设计稿要求
    "每组末尾就地添加"，但每条都插一个输入框会让列表随条目膨胀、
    滚动位置乱跳；回填项目名同样能保证「加进来的这条一定属于这组」，
    代价只是视线要往下移一次。
    """

    addRequested = pyqtSignal(str)

    def __init__(self, name: str, count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 12, 4, 2)
        row.setSpacing(8)

        bar = QLabel()
        bar.setFixedSize(3, 13)
        bar.setStyleSheet(
            f"background:{C_CYAN};border-radius:1.5px;opacity:0.85;"
        )
        row.addWidget(bar, 0, Qt.AlignVCenter)

        label = QLabel(name)
        label.setObjectName("groupname")
        row.addWidget(label, 0, Qt.AlignVCenter)

        counter = QLabel(str(count))
        counter.setObjectName("groupcount")
        row.addWidget(counter, 0, Qt.AlignVCenter)
        row.addStretch(1)

        add = QPushButton("＋ 添加")
        add.setObjectName("groupadd")
        add.setCursor(Qt.PointingHandCursor)
        add.setToolTip(f"在「{name}」下添加待办")
        add.clicked.connect(lambda: self.addRequested.emit(name))
        row.addWidget(add, 0, Qt.AlignVCenter)


class Panel(QWidget):
    """主面板窗口（无边框 + 圆角 + 深蓝玻璃）。"""

    closed = pyqtSignal()
    #: 用户点了「连接账号」——由 main 去拿配对码（本模块不碰网络）
    pairRequested = pyqtSignal()

    def __init__(self, on_changed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._on_changed = on_changed
        self._space = todo_store.SPACE_WORK
        self._sync_note = "已同步"
        self._sync_state = "idle"

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(PANEL_W, PANEL_H)
        self._build_ui()
        self._bind_style()

        # 计时条每秒刷新一次。只更新这一个标签，不动列表——
        # 整表重建会打断用户正在进行的点击，也会让滚动位置复位。
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._paint_timer_bar)
        self._clock.start()

        self.refresh()

    # ---- 外观 ----

    def _bind_style(self) -> None:
        self.setStyleSheet(f"""
            QWidget{{color:{C_TEXT};font-family:'Microsoft YaHei UI','Segoe UI';}}
            #title{{font-size:13.5px;}}
            /* 完成态删除线不走样式表：QSS 不支持 text-decoration，
               由 TodoRow 用 QFont.setStrikeOut(True) 实现 */
            #doneTitle{{font-size:13.5px;color:{C_TEXT3};}}
            #meta{{font-size:11px;color:{C_TEXT3};}}
            #row{{background:rgba(16,38,70,120);border:1px solid rgba(30,75,122,140);
                  border-radius:14px;}}
            #row:hover{{background:rgba(22,50,90,190);border:1px solid rgba(50,185,255,150);}}
            /* 正在计时的那一行：底色更实 + 冰蓝描边，扫一眼就知道在给谁计时 */
            #rowActive{{background:rgba(19,123,255,42);border:1px solid rgba(50,185,255,195);
                        border-radius:14px;}}
            #groupname{{font-size:12.5px;font-weight:600;color:#BFE4FF;}}
            #groupcount{{font-size:11px;color:{C_TEXT3};}}
            QPushButton#groupadd{{background:transparent;border:none;color:{C_TEXT3};
                                 font-size:11.5px;padding:2px 7px;border-radius:7px;}}
            QPushButton#groupadd:hover{{color:{C_CYAN};background:rgba(50,185,255,22);}}
            #timerbar{{font-size:12px;color:{C_WARN};padding:7px 12px;border-radius:10px;
                       background:rgba(255,182,72,26);border:1px solid rgba(255,182,72,80);}}
            QPushButton#del{{background:rgba(255,82,107,30);color:#FF9DAE;border:none;
                            border-radius:8px;font-size:15px;}}
            QPushButton#del:hover{{background:rgba(255,82,107,70);color:#fff;}}
            QLineEdit{{background:rgba(6,18,38,235);border:1px solid rgba(30,75,122,205);
                       border-radius:11px;padding:10px 13px;font-size:13px;color:{C_TEXT};}}
            QLineEdit:focus{{border:1px solid rgba(50,185,255,190);}}
            QPushButton#add{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                            stop:0 #1B8BFF, stop:1 #0F55C8);color:#fff;border:none;
                            border-radius:11px;font-size:13px;font-weight:600;
                            padding:0 10px;}}
            QPushButton#add:hover{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                                  stop:0 #3D9EFF, stop:1 #1466E0);}}
            QPushButton#tab{{background:transparent;border:1px solid transparent;
                            border-radius:9px;color:{C_TEXT2};font-size:12.5px;padding:6px 14px;}}
            QPushButton#tab:hover{{background:rgba(50,185,255,20);}}
            QPushButton#tab[on="true"]{{background:rgba(19,123,255,70);color:#EAF9FF;
                            border:1px solid rgba(50,185,255,115);}}
            QPushButton#ghost{{background:rgba(19,123,255,40);border:1px solid rgba(50,185,255,120);
                              border-radius:11px;color:{C_TEXT};padding:10px;font-size:12.5px;}}
            QPushButton#ghost:hover{{background:rgba(19,123,255,80);}}
            QPushButton#primary{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                                stop:0 #1B8BFF, stop:1 #0F55C8);border:none;border-radius:11px;
                                color:#fff;padding:11px;font-size:13px;font-weight:600;}}
            QPushButton#primary:hover{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                                  stop:0 #3D9EFF, stop:1 #1466E0);}}
            QScrollArea{{background:transparent;border:none;}}
            QScrollBar:vertical{{background:transparent;width:6px;margin:0}}
            QScrollBar::handle:vertical{{background:rgba(50,185,255,70);border-radius:3px;
                                        min-height:30px}}
            QScrollBar::add-line, QScrollBar::sub-line{{height:0}}
        """)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)

        # 深蓝三层：左上冷光 → 中间主体 → 右下压深
        base = QLinearGradient(rect.topLeft(), rect.bottomRight())
        base.setColorAt(0.0, QColor("#0E2040"))
        base.setColorAt(0.48, QColor("#091628"))
        base.setColorAt(1.0, QColor("#050F26"))
        painter.setBrush(base)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        painter.save()
        painter.setClipPath(path)
        ambient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        ambient.setColorAt(0.0, QColor(22, 64, 111, 140))
        ambient.setColorAt(0.45, QColor(11, 42, 84, 0))
        painter.setBrush(ambient)
        painter.drawRect(rect)

        glow = QLinearGradient(rect.bottomLeft(), rect.topRight())
        glow.setColorAt(0.0, QColor(19, 123, 255, 0))
        glow.setColorAt(1.0, QColor(19, 123, 255, 26))
        painter.setBrush(glow)
        painter.drawRect(rect)
        painter.restore()

        # 双描边：外发光 + 冰蓝细线
        painter.setBrush(Qt.NoBrush)
        outer = QColor(50, 185, 255, 46)
        painter.setPen(outer)
        painter.drawPath(path)
        inner = QPainterPath()
        inner.addRoundedRect(rect.adjusted(1, 1, -1, -1), RADIUS - 1, RADIUS - 1)
        painter.setPen(QColor(50, 185, 255, 110))
        painter.drawPath(inner)

        # 顶部一道金属高光
        painter.setPen(QColor(200, 240, 255, 150))
        painter.drawLine(
            int(rect.left() + 40), int(rect.top() + 1),
            int(rect.right() - 40), int(rect.top() + 1),
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        """无边框窗口要自己裁圆角，否则四角会露出方形底色。"""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), RADIUS, RADIUS)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        super().resizeEvent(event)

    # ---- 布局 ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # 头部
        header = QHBoxLayout()
        header.setSpacing(12)
        logo = QLabel()
        logo.setFixedSize(34, 34)
        logo.setStyleSheet(
            "border-radius:17px;"
            "background:qradialgradient(cx:0.34,cy:0.30,radius:0.85,"
            "stop:0 #DBF6FF,stop:0.22 #6FCBFF,stop:0.46 #1D7BD8,"
            "stop:0.72 #0B2F63,stop:1 #041127);"
        )
        header.addWidget(logo)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        name = QLabel("ORBYTE")
        name.setStyleSheet("font-size:16px;font-weight:700;letter-spacing:1px;")
        self._sub = QLabel("正在连接…")
        self._sub.setStyleSheet(f"font-size:11.5px;color:{C_TEXT3};")
        title_box.addWidget(name)
        title_box.addWidget(self._sub)
        header.addLayout(title_box)
        header.addStretch(1)

        self._sync_pill = QLabel("已同步")
        self._sync_pill.setStyleSheet(
            f"font-size:11.5px;color:{C_TEXT2};padding:5px 11px;border-radius:11px;"
            f"background:rgba(19,123,255,26);border:1px solid rgba(50,185,255,66);"
        )
        header.addWidget(self._sync_pill, 0, Qt.AlignVCenter)
        root.addLayout(header)

        # 页签
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self._tab_work = QPushButton("工作 0")
        self._tab_life = QPushButton("生活 0")
        for btn in (self._tab_work, self._tab_life):
            btn.setObjectName("tab")
            btn.setCursor(Qt.PointingHandCursor)
            tabs.addWidget(btn)
        tabs.addStretch(1)
        self._tab_work.clicked.connect(lambda: self._switch(todo_store.SPACE_WORK))
        self._tab_life.clicked.connect(lambda: self._switch(todo_store.SPACE_LIFE))
        root.addLayout(tabs)

        # 列表
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        self._list_layout = QVBoxLayout(holder)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(holder)
        root.addWidget(self._scroll, 1)

        # 未配对引导：**纯本地也能用**，这里只是一张"想同步就点这里"的卡片，
        # 不挡任何操作，也不会自动弹浏览器（要不要连由用户决定）
        self._pair_box = QFrame()
        self._pair_box.setObjectName("row")
        pair_layout = QVBoxLayout(self._pair_box)
        pair_layout.setContentsMargins(18, 16, 18, 16)
        pair_layout.setSpacing(8)
        self._pair_tip = QLabel("只在这台电脑上记录")
        self._pair_tip.setStyleSheet("font-size:14px;font-weight:600;")
        pair_layout.addWidget(self._pair_tip)
        self._pair_desc = QLabel(
            "待办现在就存在本机，不联网也能用。想同步到日清应用时再连接账号："
            "工作待办自动上传，生活待办永远只留本机。"
        )
        self._pair_desc.setWordWrap(True)
        self._pair_desc.setStyleSheet(f"font-size:12px;color:{C_TEXT2};")
        pair_layout.addWidget(self._pair_desc)

        self._pair_start_btn = QPushButton("连接账号，开始同步")
        self._pair_start_btn.setObjectName("ghost")
        self._pair_start_btn.setCursor(Qt.PointingHandCursor)
        self._pair_start_btn.clicked.connect(self.pairRequested.emit)
        pair_layout.addWidget(self._pair_start_btn)

        self._code_label = QLabel("------")
        self._code_label.setAlignment(Qt.AlignCenter)
        self._code_label.setStyleSheet(
            "font-size:30px;font-weight:700;letter-spacing:9px;color:#CFF3FF;"
            "padding:14px;border-radius:14px;"
            "background:rgba(19,123,255,45);border:1px solid rgba(50,185,255,100);"
        )
        pair_layout.addWidget(self._code_label)
        self._pair_btn = QPushButton("打开浏览器授权")
        self._pair_btn.setObjectName("primary")
        self._pair_btn.setCursor(Qt.PointingHandCursor)
        pair_layout.addWidget(self._pair_btn)
        # 码和「打开浏览器」按钮只在拿码之后才出现，未点「连接账号」前不吓人
        self._code_label.hide()
        self._pair_btn.hide()
        self._pair_box.hide()
        # 构造顺序上它在列表之后，靠 insertWidget 挪到列表上方
        root.insertWidget(root.indexOf(self._scroll), self._pair_box)

        # 计时条：正在计时时显示（面板收起也靠托盘 tooltip 自证在跑）
        self._timer_bar = QLabel()
        self._timer_bar.setObjectName("timerbar")
        self._timer_bar.setTextFormat(Qt.RichText)
        self._timer_bar.hide()
        root.addWidget(self._timer_bar)

        # 输入区
        composer = QHBoxLayout()
        composer.setSpacing(8)
        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText("记一件要办的事…")
        self._title_input.setMaxLength(200)
        self._title_input.returnPressed.connect(self._add)
        composer.addWidget(self._title_input, 1)

        self._project_input = QLineEdit()
        self._project_input.setPlaceholderText("项目")
        self._project_input.setMaxLength(40)
        self._project_input.setFixedWidth(126)
        self._project_input.returnPressed.connect(self._add)
        composer.addWidget(self._project_input)

        add_btn = QPushButton("添加")
        add_btn.setObjectName("add")
        add_btn.setFixedWidth(78)
        # 与输入框同高：QSS 只给了内边距，不写死高度会被压成一条细线（实测 13px）
        add_btn.setFixedHeight(38)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add)
        composer.addWidget(add_btn)
        root.addLayout(composer)

        self._hint = QLabel("回车快速添加")
        self._hint.setStyleSheet(f"font-size:10.5px;color:{C_TEXT3};")
        root.addWidget(self._hint)

    # ---- 数据 ----

    def _switch(self, space: str) -> None:
        self._space = space
        self.refresh()

    def _add(self) -> None:
        title = self._title_input.text().strip()
        if not title:
            self._title_input.setFocus()
            return
        todo_store.add(title, self._project_input.text().strip(), space=self._space)
        self._title_input.clear()
        self._title_input.setFocus()
        self.refresh()
        self._notify()

    def _cycle(self, item_uuid: str) -> None:
        item = todo_store.get_todo(item_uuid)
        if not item:
            return
        nxt = {"todo": "doing", "doing": "done", "done": "todo"}[item["state"]]
        todo_store.set_state(item_uuid, nxt)
        self.refresh()
        self._notify()

    def _delete(self, item_uuid: str) -> None:
        todo_store.delete(item_uuid)
        self.refresh()
        self._notify()

    def _notify(self) -> None:
        if self._on_changed:
            self._on_changed()

    def set_sync_status(self, payload: dict) -> None:
        self._sync_state = payload.get("state", "idle")
        pending = int(payload.get("pending") or 0)
        text = {
            "idle": f"{pending} 条待上传" if pending else "已同步",
            "syncing": "上传中…",
            "offline": "离线 · 已存本机",
            "unauthorized": "需重新配对",
            "error": "同步异常",
        }.get(self._sync_state, "已同步")
        color = {
            "offline": C_WARN,
            "unauthorized": C_RED,
            "error": C_RED,
        }.get(self._sync_state, C_TEXT2)
        self._sync_pill.setText(f"● {text}")
        self._sync_pill.setStyleSheet(
            f"font-size:11.5px;color:{color};padding:5px 11px;border-radius:11px;"
            f"background:rgba(19,123,255,26);border:1px solid rgba(50,185,255,66);"
        )

    def refresh(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        active = todo_store.active_info()
        items = todo_store.list_todos(self._space)

        if self._space == todo_store.SPACE_WORK:
            # 工作区：项目 > 待办 两级。组序 = 组内首条的 position，
            # 所以拖动排序会顺带改变组序，不需要单独的项目排序表。
            for project, group_items in todo_store.groups(self._space):
                header = ProjectGroupHeader(project, len(group_items))
                header.addRequested.connect(self._prefill_project)
                self._list_layout.insertWidget(self._list_layout.count() - 1, header)
                for row_data in group_items:
                    self._append_row(row_data, active)
        else:
            # 生活区：无项目头平铺（设计稿口径），也不显示项目名与工时
            for row_data in items:
                self._append_row(row_data, None)

        if not items:
            empty = QLabel(
                "还没有工作待办\n下面输入框记一条"
                if self._space == todo_store.SPACE_WORK
                else "生活待办只存本机\n用来放私事，永不上传"
            )
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color:{C_TEXT3};font-size:12.5px;padding:36px 0;")
            self._list_layout.insertWidget(self._list_layout.count() - 1, empty)

        self._tab_work.setText(f"工作 {len(todo_store.list_todos(todo_store.SPACE_WORK))}")
        self._tab_life.setText(f"生活 {len(todo_store.list_todos(todo_store.SPACE_LIFE))}")
        for btn, space in ((self._tab_work, todo_store.SPACE_WORK),
                           (self._tab_life, todo_store.SPACE_LIFE)):
            btn.setProperty("on", "true" if space == self._space else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        paired = bool(config.get_token())
        self._sub.setText(
            f"{config.get_device_name()} · v{__version__}" if paired else "仅本机 · 未连接账号"
        )
        self._pair_box.setVisible(not paired)
        self._hint.setText(
            "回车快速添加" if paired else "未连接账号，记录先存本机，连上后自动补传"
        )
        self._paint_timer_bar()

    def _append_row(self, row_data: dict, active: dict | None) -> None:
        started = None
        if active and row_data["uuid"] == active.get("uuid"):
            started = active.get("started_at")
        row = TodoRow(row_data, active_started=started)
        row.stateClicked.connect(self._cycle)
        row.deleteClicked.connect(self._delete)
        self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    def _paint_timer_bar(self) -> None:
        """底部计时条：秒级时钟 + 标题。没有计时任务时整条隐藏。"""
        active = todo_store.active_info()
        if not active:
            self._timer_bar.hide()
            return
        title = (active.get("title") or "").strip() or "未命名待办"
        if len(title) > 24:
            title = title[:24] + "…"
        elapsed = _elapsed_since(active.get("started_at"))
        spent = int(active.get("spent_sec") or 0)
        total = _fmt_clock(spent + elapsed)
        self._timer_bar.setText(f"● 计时中 {_fmt_clock(elapsed)} · {title} · 累计 {total}")
        self._timer_bar.show()

    def _prefill_project(self, project: str) -> None:
        """组头「＋ 添加」：回填项目名并聚焦标题，加进来的必属这一组。"""
        # 「未归类」是个兜底分组名，不是真项目名，别把它写进输入框
        self._project_input.setText(
            "" if project == todo_store.UNCATEGORIZED else project
        )
        self._title_input.setFocus()

    # ---- 配对卡片 ----

    def show_pair_code(self, code: str, confirm_url: str, on_open: Callable[[str], None]) -> None:
        """拿到码之后：把"连接账号"按钮换成码 + 打开浏览器。"""
        self._pair_tip.setText("在网页里确认这台电脑")
        self._pair_desc.setText(
            "配对码已生成。点下面的按钮打开授权页，登录后点「允许接入」即可。"
        )
        self._pair_start_btn.hide()
        self._code_label.setText(code or "------")
        self._code_label.show()
        try:
            self._pair_btn.clicked.disconnect()
        except TypeError:
            pass
        self._pair_btn.clicked.connect(lambda: on_open(confirm_url))
        self._pair_btn.show()
        self._pair_box.show()

    def show_pair_error(self, message: str) -> None:
        """拿到码失败（服务器不可达等）：留在本地模式，给出可读的原因。"""
        self._pair_tip.setText("暂时连不上服务器")
        self._pair_desc.setText(message)
        self._code_label.hide()
        self._pair_btn.hide()
        self._pair_start_btn.show()
        self._pair_box.show()

    # ---- 动效 ----

    def popup_at(self, anchor: QPoint) -> None:
        """从球的位置展开：起点贴着球，终点向左上收好。

        ⚠️ 不用 windowOpacity 做淡入：无边框 + setMask 的窗口在部分
        Windows 环境下窗口透明度不会恢复，面板会一直"隐身"。
        改成从下方 16px 滑入 + 高度微整形，效果接近且没有这种坑。
        """
        screen = self.screen().availableGeometry()
        x = anchor.x() - PANEL_W + 40
        y = anchor.y() - PANEL_H + 60
        x = max(screen.left() + 8, min(x, screen.right() - PANEL_W - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - PANEL_H - 8))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(240)
        self._slide.setStartValue(QPoint(x, y + 16))
        self._slide.setEndValue(QPoint(x, y))
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._slide.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
