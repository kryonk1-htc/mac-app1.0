"""单条详情页（原地翻页，不弹新窗口）。

设计稿的取舍照搬：列表上只靠形状（红黄绿点）认状态，详情里才出现
带字的状态按钮——列表要能快速扫，详情要能确认。工时是整页唯一带色底的
区域，因为它是唯一"正在变"的东西。
"""

from __future__ import annotations

from PyQt5.QtCore import QDate, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import todo_store
from . import theme
from .theme import elapsed_since, fmt_clock, fmt_hours

_PRIORITY_ITEMS = [
    ("", "未设置"),
    ("P0", "P0 · 最高"),
    ("P1", "P1 · 高"),
    ("P2", "P2 · 普通"),
]


class DetailPage(QWidget):
    """一条待办的详情与编辑。改完立刻落库，没有"保存"按钮。"""

    backRequested = pyqtSignal()
    changed = pyqtSignal()
    deleteRequested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item: dict = {}
        self._loading = False
        self._build()
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._paint_work)
        self._clock.start()

    # ---- 布局 ----

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        # 「‹ 返回」已上移到主窗口顶栏（与「已同步 / ✕」平行），页面里不再重复

        # 内容不再包一张"纸"：卡片底和边框都会让四周看起来有留白
        self._card = QFrame()
        self._card.setObjectName("detailCard")
        self._card.setAttribute(Qt.WA_StyledBackground, True)
        self._card.setProperty("wide", "true")
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setContentsMargins(0, 4, 4, 0)
        self._card_layout.setSpacing(14)
        body = self._card_layout

        self._title = QLineEdit()
        self._title.setObjectName("detailTitle")
        self._title.setMaxLength(200)
        self._title.setPlaceholderText("要做的事")
        self._title.editingFinished.connect(self._save_title)
        body.addWidget(self._title)

        seg = QHBoxLayout()
        seg.setSpacing(6)
        self._seg_buttons: dict[str, QPushButton] = {}
        for state, text in (("todo", "未开始"), ("doing", "进行中"), ("done", "已完成")):
            btn = QPushButton(text)
            btn.setObjectName("seg")
            btn.setProperty("kind", state)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, s=state: self._set_state(s))
            self._seg_buttons[state] = btn
            seg.addWidget(btn)
        seg.addStretch(1)
        body.addLayout(seg)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._project = QComboBox()
        self._project.setEditable(True)
        self._project.setInsertPolicy(QComboBox.NoInsert)
        self._project.lineEdit().setPlaceholderText("项目")
        self._project.currentTextChanged.connect(self._save_project)
        grid.addWidget(self._field("项目", self._project), 0, 0)

        self._priority = QComboBox()
        for _value, text in _PRIORITY_ITEMS:
            self._priority.addItem(text)
        self._priority.currentIndexChanged.connect(self._save_priority)
        grid.addWidget(self._field("优先级", self._priority), 0, 1)

        due_box = QWidget()
        due_row = QHBoxLayout(due_box)
        due_row.setContentsMargins(0, 0, 0, 0)
        due_row.setSpacing(6)
        self._due = QDateEdit()
        self._due.setCalendarPopup(True)
        self._due.setDisplayFormat("yyyy-MM-dd")
        self._due.dateChanged.connect(self._save_due)
        due_row.addWidget(self._due, 1)
        self._due_clear = QPushButton("清除")
        self._due_clear.setObjectName("link")
        self._due_clear.setCursor(Qt.PointingHandCursor)
        self._due_clear.clicked.connect(self._clear_due)
        due_row.addWidget(self._due_clear)
        self._due_box = self._field("截止日", due_box)
        grid.addWidget(self._due_box, 1, 0)

        self._planned = QSpinBox()
        self._planned.setRange(0, 6000)
        self._planned.setSingleStep(15)
        self._planned.setSuffix(" 分钟")
        self._planned.setSpecialValueText("未填")
        self._planned.valueChanged.connect(self._save_planned)
        grid.addWidget(self._field("计划工时", self._planned), 1, 1)
        body.addLayout(grid)

        self._work = QLabel()
        self._work.setObjectName("workbox")
        self._work.setTextFormat(Qt.RichText)
        body.addWidget(self._work)

        # 详细内容：应用同步来的「详细说明」（app_content 纯文本）。
        # 分开显示口径（2026-11-04 二次拍板）：标题行=工作内容，这块大区域
        # =详细内容，**常驻显示**——没内容时显示占位文案，不再整块隐藏。
        # 吃掉下方全部弹性空间，内部滚动，不把按钮顶出窗口。
        self._detail_body = QPlainTextEdit()
        self._detail_body.setObjectName("detailBody")
        self._detail_body.setReadOnly(True)
        self._detail_body.setPlaceholderText("暂无详细内容")
        self._detail_body.setMinimumHeight(160)
        body.addWidget(self._detail_body, 1)

        # 「创建于/最后修改」与「同项目」两块已按用户要求整体删除

        # 窄面板（挂件）的高度跟着列表走，详情内容通常比它短。留一段弹性空白，
        # 让「标记完成 / 删除」贴到底部，而不是浮在半空、下面空一大块。
        body.addStretch(1)
        # 详细内容出现时要把这段弹性的系数压成 0，空间全让给内容块
        self._spacer_index = body.count() - 1

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._done_btn = QPushButton("标记完成")
        self._done_btn.setObjectName("primary")
        self._done_btn.setCursor(Qt.PointingHandCursor)
        self._done_btn.setMinimumHeight(52)
        self._done_btn.clicked.connect(self._toggle_done)
        footer.addWidget(self._done_btn, 1)
        self._del_btn = QPushButton("删除")
        self._del_btn.setObjectName("del")
        self._del_btn.setCursor(Qt.PointingHandCursor)
        self._del_btn.setFixedSize(110, 52)
        self._del_btn.clicked.connect(lambda: self.deleteRequested.emit(self.item.get("uuid", "")))
        footer.addWidget(self._del_btn)
        body.addLayout(footer)

        root.addWidget(self._card, 1)

    def _field(self, label_text: str, control: QWidget) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        label = QLabel(label_text)
        # 不用 #hint：那是挂件空态的字号档（22px），字段标签要小一档
        label.setObjectName("fieldLab")
        col.addWidget(label)
        col.addWidget(control)
        return box

    # ---- 载入 ----

    def load(self, item_uuid: str) -> None:
        item = todo_store.get_todo(item_uuid)
        if not item:
            return
        self.item = item
        self._loading = True
        try:
            self._title.setText(item.get("title") or "")
            projects = [name for name, _items in todo_store.groups(todo_store.SPACE_WORK)]
            self._project.clear()
            self._project.addItem("")
            for name in projects:
                self._project.addItem(name)
            if item.get("space") == todo_store.SPACE_LIFE:
                self._project.setEnabled(False)
                self._project.setCurrentText("生活")
                self._project.setToolTip("生活待办不分项目，也不出本机")
            else:
                self._project.setEnabled(True)
                self._project.setCurrentText(item.get("project") or "")

            priority = (item.get("priority") or "").upper()
            index = next(
                (i for i, (value, _t) in enumerate(_PRIORITY_ITEMS) if value == priority), 0
            )
            self._priority.setCurrentIndex(index)

            due = (item.get("due_date") or "").strip()
            if due:
                parsed = QDate.fromString(due, "yyyy-MM-dd")
                self._due.setDate(parsed if parsed.isValid() else QDate.currentDate())
            else:
                self._due.setDate(QDate.currentDate())
            self._due_clear.setVisible(bool(due))

            self._planned.setValue(int(item.get("planned_min") or 0))

            # 详细内容：常驻显示，空时走占位文案；弹性空白永远压 0
            detail_text = (item.get("app_detail") or "").strip()
            self._detail_body.setPlainText(detail_text)
            self._card_layout.setStretch(self._spacer_index, 0)

            self._paint_state()
            self._paint_work()
        finally:
            self._loading = False

    def _paint_state(self) -> None:
        state = self.item.get("state") or "todo"
        for key, btn in self._seg_buttons.items():
            btn.setProperty("on", "true" if key == state else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        done = state == "done"
        self._done_btn.setText("重新打开" if done else "标记完成")

    def _paint_work(self) -> None:
        if not self.item:
            return
        active = todo_store.active_info()
        running = bool(active and active.get("uuid") == self.item.get("uuid"))
        live = elapsed_since(active.get("started_at")) if running else 0
        spent = int(self.item.get("spent_sec") or 0)
        today = todo_store.today_sec(self.item.get("uuid") or "") + live
        total = spent + live

        def shown(sec: int) -> str:
            return fmt_hours(sec) or "0m"

        parts = [
            f'<span style="color:{theme.C_TEXT2}">今日</span> '
            f'<span style="color:{theme.C_TEXT}">{shown(today)}</span>',
            f'<span style="color:{theme.C_TEXT2}">累计</span> '
            f'<span style="color:{theme.C_TEXT}">{shown(total)}</span>',
        ]
        planned = int(self.item.get("planned_min") or 0)
        if planned:
            parts.append(
                f'<span style="color:{theme.C_TEXT2}">计划</span> '
                f'<span style="color:{theme.C_TEXT}">{shown(planned * 60)}</span>'
            )
        if running:
            # 计时中不再用琥珀色：设计稿是无彩色系，活跃状态靠圆点表达
            parts.append(
                f'<span style="color:{theme.C_TEXT}">'
                f"● 计时中 {fmt_clock(live)}</span>"
            )
        self._work.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts))

    def resizeEvent(self, event) -> None:  # noqa: N802
        # 宽窗口（主窗口）里给内容套一层卡片；窄窗口（挂件）保持纯内容，
        # 否则卡片边线离窗口边只剩几像素，看着像套了两层框
        wide = self.width() > 470
        if (self._card.property("wide") == "true") != wide:
            self._card.setProperty("wide", "true" if wide else "false")
            self._card.style().unpolish(self._card)
            self._card.style().polish(self._card)
            margins = (0, 4, 4, 0) if wide else (0, 2, 0, 2)
            self._card_layout.setContentsMargins(*margins)
            # 窄窗口（挂件）里列宽只有 160 出头，写「清除」会把日期框挤到截断，
            # 换成 × 只占 22px。宽窗口有地方，用文字更明确。
            self._due_clear.setText("清除" if wide else "×")
            self._due_clear.setToolTip("清除截止日")
            if not wide:
                self._due_clear.setFixedWidth(24)
            else:
                self._due_clear.setMinimumWidth(0)
                self._due_clear.setMaximumWidth(16777215)
            # 宽窗口里卡片按内容高度居中；窄窗口里把弹性给卡片本身，
            # 让它撑满整页——上面那段弹性空白才会把操作按钮顶到底部。
            # （只改 sizePolicy 不够：上下两个 spacer 也是 Expanding，会把余量分走。）
            layout = self.layout()
            if layout is not None:
                layout.setStretch(1, 1 if wide else 0)
                layout.setStretch(2, 0 if wide else 1)
                layout.setStretch(3, 1 if wide else 0)
        super().resizeEvent(event)

    # ---- 保存 ----

    def _touch(self) -> None:
        if self._loading or not self.item:
            return
        self.item = todo_store.get_todo(self.item.get("uuid", "")) or self.item
        self._paint_state()
        self._paint_work()
        self.changed.emit()

    def _save_title(self) -> None:
        if self._loading or not self.item:
            return
        text = self._title.text().strip()
        if not text or text == (self.item.get("title") or ""):
            return
        todo_store.update(self.item["uuid"], title=text)
        self._touch()

    def _save_project(self, text: str) -> None:
        if self._loading or not self.item:
            return
        if self.item.get("space") != todo_store.SPACE_WORK:
            return
        clean = text.strip()
        if clean == (self.item.get("project") or ""):
            return
        todo_store.update(self.item["uuid"], project=clean)
        self._touch()

    def _save_priority(self, index: int) -> None:
        if self._loading or not self.item:
            return
        value = _PRIORITY_ITEMS[index][0] if 0 <= index < len(_PRIORITY_ITEMS) else ""
        if value == (self.item.get("priority") or ""):
            return
        todo_store.update(self.item["uuid"], priority=value)
        self._touch()

    def _save_due(self, qdate: QDate) -> None:
        if self._loading or not self.item:
            return
        value = qdate.toString("yyyy-MM-dd")
        if value == (self.item.get("due_date") or ""):
            return
        todo_store.update(self.item["uuid"], due_date=value)
        self._due_clear.setVisible(True)
        self._touch()

    def _clear_due(self) -> None:
        if self._loading or not self.item:
            return
        todo_store.update(self.item["uuid"], due_date=None)
        self._due_clear.setVisible(False)
        self._touch()

    def _save_planned(self, value: int) -> None:
        if self._loading or not self.item:
            return
        if value == int(self.item.get("planned_min") or 0):
            return
        todo_store.update(self.item["uuid"], planned_min=value)
        self._touch()

    def _set_state(self, state: str) -> None:
        if not self.item or state == (self.item.get("state") or ""):
            return
        todo_store.set_state(self.item["uuid"], state)
        self._touch()

    def _toggle_done(self) -> None:
        if not self.item:
            return
        nxt = "todo" if self.item.get("state") == "done" else "done"
        self._set_state(nxt)
