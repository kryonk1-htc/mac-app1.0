"""主窗口（挂件 ⤢ 之后的放大视图"全部待办"）。

跟挂件的关系只有一条：窗口是挂件的放大，不是另一个产品。
所以标题、颜色、状态点、优先级徽标全部和挂件同一套。

2026-10 第三版：按用户圈选砍掉了所有装饰性信息——logo、页面标题、
项目条数、右上两个按钮、页签数字、整条计时栏、行内到期日与工时、
分组进度与计数、输入框提示语、底部说明。行内只剩
「状态点 + 任务标题 + 优先级徽标」三项。740 → 660 → 720（详情页控件加高后 660 装不下）。
"""

from __future__ import annotations

import sys
from typing import Callable

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config, todo_store
from . import theme
from .detail import DetailPage
from .project_dialog import ProjectDialog
from .theme import GlassWindow, StateDot, priority_chip, strike

WINDOW_W, WINDOW_H = 860, 720
ROW_H = 68  # 任务标题 22px（对齐详情页档位）后 60 装不下


class GroupHeader(QWidget):
    """项目组头：折叠箭头 + 名称 + 添加/更多。

    不再显示「1/4 + 进度条 + 工时」——和行内工时是同一类信息，
    行内已经删了，组头留着就不一致。
    """

    addRequested = pyqtSignal(str)
    renameRequested = pyqtSignal(str)
    deleteRequested = pyqtSignal(str)
    toggleRequested = pyqtSignal(str)

    def __init__(
        self,
        name: str,
        label: str,
        collapsed: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 10, 2, 4)
        row.setSpacing(9)

        chevron = QPushButton("▸" if collapsed else "▾")
        chevron.setObjectName("link")
        chevron.setFixedSize(22, 22)
        chevron.setCursor(Qt.PointingHandCursor)
        chevron.setToolTip("折叠 / 展开")
        chevron.clicked.connect(lambda: self.toggleRequested.emit(name))
        row.addWidget(chevron, 0, Qt.AlignVCenter)

        title = QLabel(label)
        title.setObjectName("groupname")
        row.addWidget(title, 0, Qt.AlignVCenter)

        row.addStretch(1)

        add = QPushButton("＋ 添加")
        add.setObjectName("link")
        add.setCursor(Qt.PointingHandCursor)
        add.setToolTip(f"在「{label}」下添加待办")
        add.clicked.connect(lambda: self.addRequested.emit(name))
        row.addWidget(add, 0, Qt.AlignVCenter)

        more = QPushButton("⋯")
        more.setObjectName("link")
        more.setFixedSize(24, 24)
        more.setCursor(Qt.PointingHandCursor)
        more.setToolTip("项目操作")
        more.clicked.connect(self._open_menu)
        row.addWidget(more, 0, Qt.AlignVCenter)
        self._more = more

    def _open_menu(self) -> None:
        menu = QMenu(self)
        act_rename = menu.addAction("重命名项目")
        act_delete = menu.addAction("删除项目")
        chosen = menu.exec_(self._more.mapToGlobal(self._more.rect().bottomLeft()))
        if chosen is act_rename:
            self.renameRequested.emit(self._name)
        elif chosen is act_delete:
            self.deleteRequested.emit(self._name)


class TodoRow(QFrame):
    """主窗口里的一行：状态点 + 任务标题 + 优先级徽标。

    到期日和「今天 2h · 累计 2h30m」已删除：16px 正文下这两串字会把
    标题挤到十几个字就省略，而它们都能在详情页看到。
    """

    stateClicked = pyqtSignal(str)
    openClicked = pyqtSignal(str)

    def __init__(self, item: dict, running: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.setObjectName("rowActive" if running else "row")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(ROW_H)

        row = QHBoxLayout(self)
        # 右边距 14：优先级徽标不贴卡片边框（截图里 838 vs 825 那 12px 的来源）
        row.setContentsMargins(15, 0, 14, 0)
        row.setSpacing(12)

        dot = StateDot(item.get("state") or "todo", size=20)
        dot.clicked.connect(lambda: self.stateClicked.emit(item["uuid"]))
        row.addWidget(dot, 0, Qt.AlignVCenter)

        title = QLabel(item.get("title") or "")
        done = item.get("state") == "done"
        title.setObjectName("titleDone" if done else "title")
        if done:
            strike(title)
        title.setWordWrap(False)
        row.addWidget(title, 1, Qt.AlignVCenter)

        # 已完成的条目不再显示优先级徽章（2026-11-04 用户拍板）：
        # 完成态只剩状态点和删除线标题，徽章是「接下来先做哪条」的信号，
        # 对已经做完的没有意义
        chip = (
            priority_chip(item.get("priority") or "")
            if item.get("state") != "done"
            else None
        )
        if chip:
            row.addWidget(chip, 0, Qt.AlignVCenter)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.openClicked.emit(self.item["uuid"])
            event.accept()


class MainWindow(GlassWindow):
    """全部待办（含详情原地翻页）。"""

    collapseRequested = pyqtSignal()
    changed = pyqtSignal()
    pairRequested = pyqtSignal()
    linkToggleRequested = pyqtSignal()

    def __init__(self, on_moved: Callable[[int, int], None] | None = None) -> None:
        super().__init__(radius=24, on_moved=on_moved)
        if sys.platform == "darwin":
            # macOS：主窗口得是"真正的窗口"——进 Dock，才能点 Dock 图标唤回、
            # 才能用 Cmd+W / Cmd+Q。Qt.Tool 在 mac 上不占 Dock，
            # 用户把它关掉之后除了悬浮球就没有任何入口能再打开它。
            # 不置顶：它是工作区，不该盖住别的应用。
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self._space = todo_store.SPACE_WORK
        # 当前视图：todos（工作/生活列表+详情）
        self._view = "todos"
        self._collapsed: set[str] = set()
        self._sync_state = "idle"
        #: 令牌失效标记：一旦为真就一直显示「需重新配对」，直到真的连上为止。
        #: 不能只看同步线程那一帧状态——清掉令牌后下一轮会广播 idle，
        #: 徽标会在几秒内退回「仅本机」，用户刚好错过唯一那条提示。
        self._auth_lost = False
        self.setFixedSize(WINDOW_W, WINDOW_H)
        self._build()
        self.refresh()

    # ---- 布局 ----

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(10)

        # 页签行：顶部直接是「工作 / 生活」，右边留今日合计、本机状态和关闭。
        # 原来的 logo + 页面标题 + 项目条数整块删掉了——那是第二遍读才需要的信息。
        self._tabs_row = QWidget()
        tabs = QHBoxLayout(self._tabs_row)
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(6)
        # 返回键放在顶栏最左，与右上角「已同步 / ✕」平行对齐；
        # 列表页隐藏，详情页才出现
        self._back_btn = QPushButton("‹  返回")
        self._back_btn.setObjectName("link")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.setToolTip("返回列表")
        self._back_btn.clicked.connect(self.show_list)
        self._back_btn.hide()
        tabs.addWidget(self._back_btn)
        self._tab_work = QPushButton("工作")
        self._tab_life = QPushButton("生活")
        for btn in (self._tab_work, self._tab_life):
            btn.setObjectName("tab")
            btn.setCursor(Qt.PointingHandCursor)
            tabs.addWidget(btn)
        self._tab_work.clicked.connect(lambda: self._switch(todo_store.SPACE_WORK))
        self._tab_life.clicked.connect(lambda: self._switch(todo_store.SPACE_LIFE))
        tabs.addStretch(1)
        self._today = QLabel("")
        self._today.setObjectName("hint")
        tabs.addWidget(self._today)
        # 一键连接/断开：只看「本机有没有令牌」，和右边的同步徽标分工——
        # 徽标讲同步进行得怎么样，这个按钮讲连没连上、并给出切换入口
        self._link_btn = QPushButton("● 未连接")
        self._link_btn.setObjectName("linkPill")
        self._link_btn.setCursor(Qt.PointingHandCursor)
        self._link_btn.setToolTip("点击连接账号")
        self._link_btn.clicked.connect(self.linkToggleRequested.emit)
        tabs.addWidget(self._link_btn)
        self._sync_pill = QLabel("● 已同步")
        self._sync_pill.setObjectName("pill")
        tabs.addWidget(self._sync_pill)

        close = QPushButton("✕")
        close.setObjectName("icon")
        close.setFixedSize(30, 30)
        close.setToolTip("收到挂件（不是退出）")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.collapseRequested.emit)
        tabs.addWidget(close)
        root.addWidget(self._tabs_row)

        # 列表页
        self._list_page = QWidget()
        list_root = QVBoxLayout(self._list_page)
        list_root.setContentsMargins(0, 0, 0, 0)
        list_root.setSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        holder = QWidget()
        self._list_layout = QVBoxLayout(holder)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(holder)
        list_root.addWidget(self._scroll, 1)

        composer = QHBoxLayout()
        composer.setSpacing(7)
        self._title_input = QLineEdit()
        # 不给 placeholder：删了提示语之后输入框是空的，但右边有「添加」，
        # 空白反而是干净的；写一句"记一件要办的事"会在 16px 下被截断
        self._title_input.setMaxLength(200)
        self._title_input.returnPressed.connect(self._add)
        composer.addWidget(self._title_input, 1)

        self._project_input = QComboBox()
        self._project_input.setEditable(True)
        self._project_input.setFixedWidth(168)
        self._project_input.lineEdit().setPlaceholderText("项目")
        composer.addWidget(self._project_input)

        self._priority_input = QComboBox()
        self._priority_input.setFixedWidth(132)
        for text in ("未设置", "P0", "P1", "P2"):
            self._priority_input.addItem(text)
        composer.addWidget(self._priority_input)

        add = QPushButton("添加")
        add.setObjectName("add")
        add.setFixedSize(88, 44)
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._add)
        composer.addWidget(add)
        list_root.addLayout(composer)

        # 详情页
        self._detail_wrap = QWidget()
        wrap = QHBoxLayout(self._detail_wrap)
        wrap.setContentsMargins(2, 4, 2, 0)
        self._detail = DetailPage()
        # 不再定宽 660 居中：详情页铺满整个内容区（用户拍板：四周不要留白）
        self._detail.backRequested.connect(self.show_list)
        self._detail.changed.connect(self._on_detail_changed)
        self._detail.deleteRequested.connect(self._on_delete)
        wrap.addWidget(self._detail, 1)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._list_page)
        self._stack.addWidget(self._detail_wrap)
        root.addWidget(self._stack, 1)

    # ---- 数据 ----

    def _switch(self, space: str) -> None:
        self._view = "todos"
        self._space = space
        self._stack.setCurrentIndex(0)
        self._back_btn.hide()
        self.refresh()

    def _add(self) -> None:
        title = self._title_input.text().strip()
        if not title:
            self._title_input.setFocus()
            return
        priority = self._priority_input.currentText()
        todo_store.add(
            title,
            self._project_input.currentText().strip(),
            space=self._space,
            priority="" if priority in ("", "未设置") else priority,
        )
        self._title_input.clear()
        self._priority_input.setCurrentIndex(0)
        self._title_input.setFocus()
        self.refresh()
        self.changed.emit()

    def _cycle(self, item_uuid: str) -> None:
        item = todo_store.get_todo(item_uuid)
        if not item:
            return
        todo_store.set_state(item_uuid, theme.STATE_NEXT.get(item["state"], "todo"))
        self.refresh()
        self.changed.emit()

    def _on_detail_changed(self) -> None:
        self.changed.emit()

    def _on_delete(self, item_uuid: str) -> None:
        todo_store.delete(item_uuid)
        self.show_list()
        self.changed.emit()

    # ---- 项目 ----

    def _new_project(self) -> None:
        """保留给托盘菜单/命令行用；窗口右上角的「新建项目」按钮已按圈选删掉。

        日常新建项目不靠它：输入框右边的「项目」下拉是可编辑的，
        直接敲一个新名字 + 添加，项目就跟着待办建出来了。
        """
        dialog = ProjectDialog("新建项目", hint="新建后可以立刻往里添加待办；同步时项目名跟着待办走。", parent=self)
        if dialog.exec_() and dialog.value:
            todo_store.add_project(dialog.value)
            self._space = todo_store.SPACE_WORK
            self.refresh()
            self._project_input.setCurrentText(dialog.value)
            self._title_input.setFocus()

    def _rename_project(self, name: str) -> None:
        label = name or todo_store.UNCATEGORIZED
        dialog = ProjectDialog(
            "重命名项目",
            value=label,
            hint="组内所有待办的所属项目会一起改；已经同步过的会重新上传。",
            parent=self,
        )
        if dialog.exec_() and dialog.value and dialog.value != label:
            todo_store.rename_project(name, dialog.value)
            self.refresh()
            self.changed.emit()

    def _delete_project(self, name: str) -> None:
        label = name or todo_store.UNCATEGORIZED
        count = len(dict(todo_store.groups(todo_store.SPACE_WORK)).get(name, []))
        dialog = ProjectDialog(
            f"删除「{label}」",
            value=label,
            hint=(
                f"组内 {count} 条待办会被移到「未归类」，不会一起删掉。"
                if count
                else "这个项目还是空的，可以直接删除。"
            ),
            parent=self,
        )
        dialog._input.setReadOnly(True)
        dialog._input.setText(label)
        if dialog.exec_():
            todo_store.delete_project(name)
            self.refresh()
            self.changed.emit()

    # ---- 渲染 ----

    def refresh(self) -> None:
        # 整页重建会让滚动条被钳到最底：记住位置，重建后恢复
        scrollbar = self._scroll.verticalScrollBar()
        anchor = scrollbar.value()

        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        active = todo_store.active_info()
        active_uuid = active.get("uuid") if active else None

        if self._space == todo_store.SPACE_WORK:
            stats = todo_store.projects()
            for group in stats:
                self._render_group(group, active_uuid)
            total = sum(group["total"] for group in stats)
            if total == 0:
                self._empty("还没有工作待办", "在下面输入框记一条，或者先建一个项目")
        else:
            items = todo_store.list_todos(todo_store.SPACE_LIFE)
            for row_data in items:
                self._append_row(row_data, None)
            if not items:
                self._empty("生活待办只存本机", "放私事用的，永不上传；换电脑不会带走")

        scrollbar.setValue(min(anchor, scrollbar.maximum()))

        self._paint_tabs()
        self._refresh_projects()
        self._paint_sync_pill()

    def _render_group(self, group: dict, active_uuid: str | None) -> None:
        collapsed = group["name"] in self._collapsed
        header = GroupHeader(
            group["name"],
            group["label"] or todo_store.UNCATEGORIZED,
            collapsed,
        )
        header.addRequested.connect(self._prefill_project)
        header.renameRequested.connect(self._rename_project)
        header.deleteRequested.connect(self._delete_project)
        header.toggleRequested.connect(self._toggle_group)
        self._list_layout.insertWidget(self._list_layout.count() - 1, header)

        if collapsed:
            return
        if not group["total"]:
            tip = QLabel("这个项目还没有待办，点组头「＋ 添加」")
            tip.setObjectName("hint")
            tip.setContentsMargins(26, 2, 0, 6)
            self._list_layout.insertWidget(self._list_layout.count() - 1, tip)
            return
        for row_data in group["items"]:
            self._append_row(row_data, active_uuid)

    def _append_row(self, row_data: dict, active_uuid: str | None) -> None:
        running = bool(active_uuid and row_data["uuid"] == active_uuid)
        row = TodoRow(row_data, running=running)
        row.stateClicked.connect(self._cycle)
        row.openClicked.connect(self.show_detail)
        self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    def _empty(self, title: str, desc: str) -> None:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 52, 0, 0)
        col.setSpacing(8)
        line1 = QLabel(title)
        line1.setAlignment(Qt.AlignCenter)
        line1.setStyleSheet(f"color:{theme.C_TEXT2};font-size:16px;")
        line2 = QLabel(desc)
        line2.setAlignment(Qt.AlignCenter)
        line2.setStyleSheet(f"color:{theme.C_TEXT3};font-size:16px;")
        col.addWidget(line1)
        col.addWidget(line2)
        self._list_layout.insertWidget(self._list_layout.count() - 1, box)

    def _toggle_group(self, name: str) -> None:
        if name in self._collapsed:
            self._collapsed.discard(name)
        else:
            self._collapsed.add(name)
        self.refresh()

    def _prefill_project(self, name: str) -> None:
        self._project_input.setCurrentText("" if name == todo_store.UNCATEGORIZED else name)
        self._title_input.setFocus()

    def _refresh_projects(self) -> None:
        current = self._project_input.currentText()
        self._project_input.blockSignals(True)
        self._project_input.clear()
        self._project_input.addItem("")
        seen = set()
        for group in todo_store.projects():
            name = group["name"]
            if name and name not in seen:
                seen.add(name)
                self._project_input.addItem(name)
        self._project_input.setCurrentText(current)
        self._project_input.blockSignals(False)

    def _paint_tabs(self) -> None:
        # 页签不带数字了：条数在标题下面那行已经删过一轮，页签上再来一次
        # 只是让「工作 7」比「生活 4」宽窄不一，切换时按钮还会跳
        for btn, on in (
            (self._tab_work, self._view == "todos" and self._space == todo_store.SPACE_WORK),
            (self._tab_life, self._view == "todos" and self._space == todo_store.SPACE_LIFE),
        ):
            btn.setProperty("on", "true" if on else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        if self._view == "todos" and self._space == todo_store.SPACE_WORK:
            today = todo_store.today_total_sec(todo_store.SPACE_WORK)
            self._today.setText(f"今日 {theme.fmt_hours(today)}" if today else "")
        else:
            self._today.setText("")

    def set_sync_status(self, payload: dict) -> None:
        self._sync_state = payload.get("state", "idle")
        if self._sync_state == "unauthorized":
            self._auth_lost = True
        elif config.get_token():
            # 重新拿到令牌（配对成功）才算这一次失效翻篇
            self._auth_lost = False
        self._paint_sync_pill()

    def _paint_sync_pill(self) -> None:
        pending = todo_store.pending_count()
        text = {
            "idle": f"{pending} 条待上传" if pending else "已同步",
            "syncing": "上传中…",
            "offline": "离线 · 已存本机",
            "unauthorized": "需重新配对",
            "error": "同步异常",
        }.get(self._sync_state, "已同步")
        # 401 之后本地令牌已被清空，但这时不能退化成「仅本机」——
        # 用户需要看到"需要重新配对"这个可行动的信号，而不是一个
        # 看起来一切正常的"仅本机"。清完令牌的下一轮广播就是 idle，
        # 所以这里要靠 _auth_lost 记住刚才发生过什么。
        if self._auth_lost and not config.get_token():
            text = "需重新配对"
        elif not config.get_token():
            # 2026-11-04 用户拍板：未连接时不再显示「仅本机」——
            # 连接状态由左边的「● 未连接」开关表达，这里重复还占地方
            self._sync_pill.hide()
            self._paint_link_btn()
            return
        color = {
            "offline": theme.C_WARN,
            "unauthorized": theme.C_RED,
            "error": theme.C_RED,
        }.get(self._sync_state, theme.C_TEXT2)
        if self._auth_lost and not config.get_token():
            color = theme.C_RED
        self._sync_pill.setText(f"● {text}")
        self._sync_pill.setStyleSheet(
            f"font-size:16px;color:{color};padding:5px 12px;border-radius:12px;"
            f"background:{theme.C_SURFACE};border:1px solid {theme.C_LINE};"
        )
        # 生活空间本身就不参与同步，再挂一个"仅本机"徽标只是重复说明
        self._sync_pill.setVisible(self._space == todo_store.SPACE_WORK)
        self._paint_link_btn()

    def _paint_link_btn(self) -> None:
        """顶栏「一键连接/断开」的文案与配色。

        连接失效（401 后令牌被清）标红提醒，点了直接进配对流程；
        已连接用完成绿；未连接用中性灰。
        """
        if self._auth_lost and not config.get_token():
            text, color, tip = "● 连接失效", theme.C_RED, "点击重新配对"
        elif config.get_token():
            text, color, tip = "● 已连接", theme.C_SUCCESS, "点击断开本机账号"
        else:
            text, color, tip = "● 未连接", theme.C_TEXT3, "点击连接账号"
        self._link_btn.setText(text)
        self._link_btn.setToolTip(tip)
        self._link_btn.setStyleSheet(
            f"font-size:16px;color:{color};padding:5px 12px;border-radius:12px;"
            f"background:{theme.C_SURFACE};border:1px solid {theme.C_LINE};"
        )

    # ---- 页面切换 ----

    def show_detail(self, item_uuid: str) -> None:
        item = todo_store.get_todo(item_uuid)
        if not item:
            return
        self._view = "todos"
        self._detail.load(item_uuid)
        self._stack.setCurrentIndex(1)
        # 顶栏切到详情态：左边「‹ 返回」，右边「已同步 / ✕」，一行平行；
        # 页签和今日合计收起来免得两套导航打架
        self._back_btn.show()
        self._tab_work.hide()
        self._tab_life.hide()
        self._today.hide()

    def show_list(self) -> None:
        self._view = "todos"
        self._stack.setCurrentIndex(0)
        self._back_btn.hide()
        self._tab_work.show()
        self._tab_life.show()
        self._today.show()
        self._tabs_row.show()
        self.refresh()
