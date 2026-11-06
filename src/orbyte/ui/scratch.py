"""暂存页：文本 + 图片的随手暂存（纯本机）。

与原型（docs/prototype-todo-scratch.html）对齐的口径：
- 左侧窄栏草稿历史（118px@1x → 1.33 倍 ≈ 158px），默认名 草稿1/2/3…，
  悬停出 ✎重命名 / ×删除，底部「＋ 新建草稿」；
- 右侧编辑区：一行头部（草稿名 + 重命名 + 三枚小图标）+ 大草稿板 + 图片缩略行；
- 三枚小图标 = ✓保存 / ⧉存为新草稿 / 🗑清空内容（原型定稿：底部按钮行已删）；
- 图片支持粘贴（Ctrl+V）与拖入，缩略图右上角 × 单独移除；
- 文字输入 800ms 防抖自动保存；全部数据只落本地库，永不上传。
"""

from __future__ import annotations

import tempfile

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import scratch_store
from . import theme

#: 侧栏宽（1.33 倍口径）
SIDEBAR_W = 158
#: 草稿行高
DRAFT_ROW_H = 76
#: 自动保存防抖（毫秒）
AUTOSAVE_MS = 800


def _stamp() -> str:
    import time

    return time.strftime("%m-%d %H:%M")


def _short_time(updated_at: str) -> str:
    """ISO 时间 → 「MM-DD HH:MM」。"""
    return (updated_at or "")[5:16].replace("T", " ")


class _IconBtn(QPushButton):
    """小图标按钮：✓ / ⧉ / 🗑，26px 手绘坐标、按按钮尺寸等比放大。"""

    def __init__(self, kind: str, tip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.underMouse():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.C_SURFACE))
            painter.drawRoundedRect(self.rect(), 9, 9)
        w = self.width()
        painter.translate(w / 2, w / 2)
        painter.scale(w / 26, w / 26)
        pen = QPen(QColor(theme.C_TEXT2 if not self.underMouse() else theme.C_TEXT), 1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if self._kind == "save":
            # 对勾
            painter.drawLine(-6, 1, -2, 5)
            painter.drawLine(-2, 5, 7, -5)
        elif self._kind == "copy":
            # 两层圆角矩形
            painter.drawRoundedRect(-3, -7, 12, 12, 3, 3)
            painter.drawRoundedRect(-9, -1, 12, 12, 3, 3)
        else:  # trash
            # 垃圾桶
            painter.drawLine(-8, -6, 8, -6)
            painter.drawLine(-3, -6, -3, -9)
            painter.drawLine(3, -6, 3, -9)
            painter.drawRoundedRect(-7, -6, 14, 16, 3, 3)
            painter.drawLine(-2, -2, -2, 6)
            painter.drawLine(2, -2, 2, 6)


class _DraftThumb(QFrame):
    """图片缩略图，右上角 × 移除。"""

    removed = pyqtSignal(int)

    def __init__(self, image: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image = image
        self.setFixedSize(112, 80)
        self.setObjectName("thumb")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.C_LINE), 1))
        painter.setBrush(QColor(theme.C_SURFACE))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        pixmap = QPixmap(self.image["path"])
        if not pixmap.isNull():
            # 居中裁剪铺满
            scaled = pixmap.scaled(
                self.width(), self.height(),
                Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
            )
            sx = (scaled.width() - self.width()) // 2
            sy = (scaled.height() - self.height()) // 2
            painter.save()
            painter.setClipRect(self.rect())
            painter.drawPixmap(-sx, -sy, scaled)
            painter.restore()
        # 右上角 × 圆钮
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 23, 31, 205))
        painter.drawEllipse(self.width() - 24, 6, 18, 18)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.8))
        painter.drawLine(self.width() - 19, 11, self.width() - 11, 19)
        painter.drawLine(self.width() - 11, 11, self.width() - 19, 19)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            pos = event.pos()
            btn_x, btn_y = self.width() - 24, 6
            if btn_x <= pos.x() <= btn_x + 18 and btn_y <= pos.y() <= btn_y + 18:
                self.removed.emit(self.image["id"])
                return
        event.accept()


class _DraftRow(QWidget):
    """左侧草稿条目：名称 + 摘要行；悬停出 重命名/删除。"""

    selected = pyqtSignal(int)
    renameClicked = pyqtSignal(int)
    deleteClicked = pyqtSignal(int)

    def __init__(self, draft: dict, active: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.draft_id = draft["id"]
        self._active = active
        self._hover = False
        self.setFixedHeight(DRAFT_ROW_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("draftRow")
        self.setAttribute(Qt.WA_StyledBackground, True)

        col = QVBoxLayout(self)
        col.setContentsMargins(12, 8, 6, 8)
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(4)
        self._name = QLabel(draft["name"])
        self._name.setStyleSheet(
            f"font-size:18px;color:{theme.C_TEXT};background:transparent;"
            + ("font-weight:500;" if active else "")
        )
        head.addWidget(self._name)
        head.addStretch(1)
        self._ops = QWidget()
        ops = QHBoxLayout(self._ops)
        ops.setContentsMargins(0, 0, 0, 0)
        ops.setSpacing(2)
        for glyph, tip, cb in (
            ("✎", "重命名", self.renameClicked),
            ("×", "删除", self.deleteClicked),
        ):
            b = QPushButton(glyph)
            b.setFixedSize(22, 22)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;color:{theme.C_TEXT3};"
                f"font-size:14px;border-radius:6px;}}"
                f"QPushButton:hover{{background:{theme.C_SURFACE2};color:{theme.C_TEXT};}}"
            )
            b.clicked.connect(lambda _, cb=cb, i=draft["id"]: cb.emit(i))
            ops.addWidget(b)
        self._ops.hide()
        head.addWidget(self._ops)
        col.addLayout(head)

        imgs = f"、{draft['img_count']}张图" if draft["img_count"] else ""
        preview = draft["preview"] or "（空）"
        stamp = _short_time(draft.get("updated_at") or "")
        meta_text = f"{preview}{imgs} · {stamp}" if stamp else preview
        self._meta = QLabel(meta_text)
        self._meta.setStyleSheet(
            f"font-size:14px;color:{theme.C_TEXT3};background:transparent;"
            "white-space:nowrap;"
        )
        col.addWidget(self._meta)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._ops.show()
        self._refresh_bg()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._ops.hide()
        self._refresh_bg()
        super().leaveEvent(event)

    def _refresh_bg(self) -> None:
        bg = theme.C_SURFACE if (self._active or self._hover) else "transparent"
        line = theme.C_PRIMARY if self._active else "transparent"
        self.setStyleSheet(
            f"QWidget#draftRow{{background:{bg};border-left:3px solid {line};}}"
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.draft_id)
        event.accept()


class _DraftTextArea(QPlainTextEdit):
    """草稿文本区：拦截图片粘贴（Ctrl+V），转给页面处理。"""

    imagePasted = pyqtSignal(object)  # QImage

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        if source.hasImage():
            image = source.imageData()
            if image is not None:
                self.imagePasted.emit(image)
                return
        super().insertFromMimeData(source)


class ScratchPage(QWidget):
    """主窗口的「暂存」页（stack 第三页）。"""

    changed = pyqtSignal()  # 预留：将来若要在别处显示草稿数

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drafts: list[dict] = []
        self._current_id: int | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(AUTOSAVE_MS)
        self._save_timer.timeout.connect(self._autosave)
        self._loading = False
        self._build()
        self.refresh()

    # ---- 布局 ----

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左：草稿历史窄栏
        sidebar = QWidget()
        sidebar.setObjectName("scratchSidebar")
        sidebar.setFixedWidth(SIDEBAR_W)
        side_col = QVBoxLayout(sidebar)
        side_col.setContentsMargins(0, 0, 0, 0)
        side_col.setSpacing(0)
        cap = QLabel("草稿历史")
        cap.setStyleSheet(
            f"font-size:15px;color:{theme.C_TEXT3};padding:12px 12px 6px;background:transparent;"
        )
        side_col.addWidget(cap)

        self._list_holder = QWidget()
        self._list_col = QVBoxLayout(self._list_holder)
        self._list_col.setContentsMargins(0, 0, 0, 0)
        self._list_col.setSpacing(0)
        self._list_col.addStretch(1)
        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setWidget(self._list_holder)
        side_scroll.setFrameShape(QFrame.NoFrame)
        side_col.addWidget(side_scroll, 1)

        new_btn = QPushButton("＋ 新建草稿")
        new_btn.setObjectName("scratchNew")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(self._new_draft)
        side_col.addWidget(new_btn)
        root.addWidget(sidebar)

        # 右：编辑区
        editor = QWidget()
        editor.setObjectName("scratchEditor")
        ed_col = QVBoxLayout(editor)
        ed_col.setContentsMargins(12, 8, 18, 14)
        ed_col.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._name_label = QLabel("")
        self._name_label.setStyleSheet(
            f"font-size:21px;font-weight:500;color:{theme.C_TEXT};background:transparent;"
        )
        head.addWidget(self._name_label)
        rename = QPushButton("✎ 重命名")
        rename.setObjectName("link")
        rename.setCursor(Qt.PointingHandCursor)
        rename.clicked.connect(self._rename_current)
        head.addWidget(rename)
        head.addStretch(1)
        self._btn_save = _IconBtn("save", "保存")
        self._btn_save.clicked.connect(self._save_now)
        head.addWidget(self._btn_save)
        self._btn_copy = _IconBtn("copy", "存为新草稿")
        self._btn_copy.clicked.connect(self._save_as_new)
        head.addWidget(self._btn_copy)
        self._btn_clear = _IconBtn("trash", "清空内容")
        self._btn_clear.clicked.connect(self._clear_current)
        head.addWidget(self._btn_clear)
        ed_col.addLayout(head)

        self._text = _DraftTextArea()
        self._text.setObjectName("scratchText")
        self._text.setPlaceholderText("在这里粘贴或输入文字…")
        self._text.textChanged.connect(self._on_text_changed)
        self._text.imagePasted.connect(self._on_image_pasted)
        self._text.setStyleSheet(
            "QPlainTextEdit#scratchText{"
            f"background:{theme.C_CARD};border:1px solid {theme.C_LINE};"
            "border-radius:12px;padding:12px 14px;"
            f"font-size:20px;color:{theme.C_TEXT};}}"
            f"QPlainTextEdit#scratchText:focus{{border:1px solid {theme.C_PRIMARY};}}"
        )
        ed_col.addWidget(self._text, 1)

        self._thumbs_row = QWidget()
        self._thumbs_col = QHBoxLayout(self._thumbs_row)
        self._thumbs_col.setContentsMargins(0, 2, 0, 0)
        self._thumbs_col.setSpacing(10)
        self._thumbs_col.addStretch(1)
        ed_col.addWidget(self._thumbs_row)

        self._drop_hint = QPushButton("＋ 粘贴 / 拖入 / 选择图片")
        self._drop_hint.setObjectName("scratchDrop")
        self._drop_hint.setCursor(Qt.PointingHandCursor)
        self._drop_hint.clicked.connect(self._pick_image)
        ed_col.addWidget(self._drop_hint)

        root.addWidget(editor, 1)

        self.setAcceptDrops(True)
        self._apply_sidebar_style()

    def _apply_sidebar_style(self) -> None:
        self.setStyleSheet(
            "QWidget#scratchSidebar{"
            f"background:{theme.C_PRIMARY_L};border-right:1px solid {theme.C_LINE};}}"
            "QWidget#scratchEditor{background:transparent;}"
            "QPushButton#scratchNew{"
            f"background:{theme.C_CARD};border:1px solid {theme.C_PRIMARY_LINE};"
            "border-radius:10px;padding:9px;margin:8px 10px 12px;"
            f"font-size:17px;color:{theme.C_TEXT};}}"
            f"QPushButton#scratchNew:hover{{background:{theme.C_SURFACE};}}"
            "QPushButton#scratchDrop{"
            f"background:transparent;border:1px dashed {theme.C_LINE};"
            "border-radius:10px;padding:8px;margin-top:2px;"
            f"font-size:16px;color:{theme.C_TEXT3};}}"
            f"QPushButton#scratchDrop:hover{{border:1px dashed {theme.C_PRIMARY};color:{theme.C_TEXT};}}"
            f"QLabel{{background:transparent;}}"
        )

    # ---- 数据 ----

    def refresh(self) -> None:
        drafts = scratch_store.list_drafts()
        if not drafts:
            scratch_store.create_draft()
            drafts = scratch_store.list_drafts()
        self._drafts = drafts
        if self._current_id is None or all(d["id"] != self._current_id for d in drafts):
            self._current_id = drafts[0]["id"] if drafts else None
        self._render_list()
        self._load_current()

    def _render_list(self) -> None:
        while self._list_col.count() > 1:
            item = self._list_col.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for draft in self._drafts:
            row = _DraftRow(draft, active=draft["id"] == self._current_id)
            row.selected.connect(self._select)
            row.renameClicked.connect(self._rename)
            row.deleteClicked.connect(self._delete)
            self._list_col.insertWidget(self._list_col.count() - 1, row)

    def _load_current(self) -> None:
        self._loading = True
        try:
            if self._current_id is None:
                self._text.clear()
                self._name_label.setText("")
                self._render_thumbs([])
                return
            draft = scratch_store.get_draft(self._current_id)
            if not draft:
                return
            self._name_label.setText(draft["name"])
            if self._text.toPlainText() != draft["content"]:
                self._text.setPlainText(draft["content"])
            self._render_thumbs(draft["images"])
        finally:
            self._loading = False

    def _render_thumbs(self, images: list[dict]) -> None:
        while self._thumbs_col.count() > 1:
            item = self._thumbs_col.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for image in images:
            thumb = _DraftThumb(image)
            thumb.removed.connect(self._remove_image)
            self._thumbs_col.insertWidget(self._thumbs_col.count() - 1, thumb)
        has = bool(images)
        self._thumbs_row.setVisible(has)
        self._drop_hint.setVisible(not has)

    # ---- 动作 ----

    def _select(self, draft_id: int) -> None:
        if draft_id == self._current_id:
            return
        self._flush_pending_text()
        self._current_id = draft_id
        self._render_list()
        self._load_current()

    def _new_draft(self) -> None:
        self._flush_pending_text()
        self._current_id = scratch_store.create_draft()
        self.refresh()
        self._text.setFocus()
        self.changed.emit()

    def _rename(self, draft_id: int) -> None:
        draft = scratch_store.get_draft(draft_id)
        if not draft:
            return
        name, ok = QInputDialog.getText(self, "重命名草稿", "名称：", text=draft["name"])
        if ok and name.strip():
            scratch_store.rename_draft(draft_id, name)
            if draft_id == self._current_id:
                self._name_label.setText(name.strip())
            self._render_list()

    def _rename_current(self) -> None:
        if self._current_id is not None:
            self._rename(self._current_id)

    def _delete(self, draft_id: int) -> None:
        if len(self._drafts) <= 1:
            QMessageBox.information(self, "ORBYTE", "至少保留一个草稿")
            return
        names = {d["id"]: d["name"] for d in self._drafts}
        answer = QMessageBox.question(
            self, "删除草稿",
            f"删除「{names.get(draft_id, '')}」？不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        scratch_store.delete_draft(draft_id)
        if self._current_id == draft_id:
            self._current_id = None
        self.refresh()
        self.changed.emit()

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._save_timer.start()

    def _flush_pending_text(self) -> None:
        """切走/保存前把编辑框里的文字立刻落库（别丢最后几秒的输入）。"""
        self._save_timer.stop()
        if self._current_id is not None and not self._loading:
            scratch_store.save_content(self._current_id, self._text.toPlainText())

    def _autosave(self) -> None:
        if self._current_id is None:
            return
        scratch_store.save_content(self._current_id, self._text.toPlainText())
        # 更新左侧摘要但不打断输入（不动文本框）
        self._drafts = scratch_store.list_drafts()
        self._render_list()

    def _save_now(self) -> None:
        self._flush_pending_text()
        self.refresh()
        QMessageBox.information(self, "ORBYTE", "已保存到本机")

    def _save_as_new(self) -> None:
        self._flush_pending_text()
        if self._current_id is None:
            return
        content = self._text.toPlainText()
        images = scratch_store.list_images(self._current_id)
        new_id = scratch_store.create_draft()
        scratch_store.save_content(new_id, content)
        for image in images:
            scratch_store.add_image(new_id, image["path"])
        self._current_id = new_id
        self.refresh()
        self.changed.emit()

    def _clear_current(self) -> None:
        if self._current_id is None:
            return
        if not self._text.toPlainText() and not scratch_store.list_images(self._current_id):
            return
        answer = QMessageBox.question(
            self, "清空草稿", "清空这个草稿的文字和图片？（草稿本身保留）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        for image in scratch_store.list_images(self._current_id):
            scratch_store.remove_image(self._current_id, image["id"])
        self._text.clear()
        scratch_store.save_content(self._current_id, "")
        self._load_current()
        self._render_list()

    # ---- 图片 ----

    def _on_image_pasted(self, image) -> None:
        if self._current_id is None:
            return
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".png")
        import os

        os.close(handle)
        image.save(path, "PNG")
        record = scratch_store.add_image(self._current_id, path)
        try:
            os.unlink(path)  # 复制进数据目录后临时文件即弃
        except OSError:
            pass
        if record:
            self._load_current()
            self.changed.emit()

    def _pick_image(self) -> None:
        if self._current_id is None:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.gif *.bmp *.webp)"
        )
        added = False
        for file in files:
            if scratch_store.add_image(self._current_id, file):
                added = True
        if added:
            self._load_current()
            self.changed.emit()

    # ---- 拖放 ----

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if any(url.toLocalFile().lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")) for url in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        if self._current_id is None:
            return
        added = False
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and scratch_store.add_image(self._current_id, path):
                added = True
        if added:
            self._load_current()
            self.changed.emit()
        event.acceptProposedAction()
