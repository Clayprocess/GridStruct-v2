"""文字识别窗口：左边原图可滑选，右边按行/按段直接改字，或切到全文自由编辑。

布局与表格识别窗口一致：上方工具行、中间原图对照、底部状态 + 主操作。
按行/按段的改字立即同步原图文字层、贴图和复制结果；全文是独立草稿，不回写原图。
"""

from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QFrame, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QSplitter,
    QStackedWidget, QStyledItemDelegate, QToolButton, QVBoxLayout,
)
from styles import tokens as T
from ui import BaseDialog, MultilineDelegate, show_toast
from ui.motion import SelectionPill
from .layer import add_text_layer
from .layout import format_text
from .model import OCRDocument

VIEWS = (("line", "按行", "每行一条，改字同步原图"), ("paragraph", "按段", "每段一条，改字同步原图"),
         ("full", "全文", "自由增删合并；不回写原图"))
# 这里只提示模型分数，不代表人工复核进度。
_PENDING_SCORE = .8


class _LineDelegate(MultilineDelegate):
    """列表项 QSS 有圆角时 Qt 不再画 BackgroundRole，低置信度底色在这里自己画。"""

    def paint(self, painter, option, index):
        color = index.data(Qt.BackgroundRole)
        if color is not None:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(option.rect, T.RADIUS_SM, T.RADIUS_SM)
            painter.restore()
        super().paint(painter, option, index)


class _ImagePreview(QGraphicsView):
    """默认适应窗口；Ctrl+滚轮缩放（同表格识别），缩放后不再自动适应。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("OCRPreview")
        self.setAlignment(Qt.AlignCenter)
        self._fit = True

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self._fit = False
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def showEvent(self, event):
        super().showEvent(event)
        if self._fit:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)


class OCRResultDialog(BaseDialog):
    def __init__(self, results, parent=None, pixmap=None):
        super().__init__(parent)
        from settings import get_settings
        self._settings = get_settings()
        image = pixmap.toImage() if pixmap is not None else None
        document = results if isinstance(results, OCRDocument) else OCRDocument(results, image)
        if pixmap is None and not document.image.isNull():
            pixmap = QPixmap.fromImage(document.image)
        self.setWindowTitle("文字识别 · 本地")
        self.resize(1100, 680)
        self._mode = QApplication.instance().property("theme_mode") or "light"
        self._layer = None
        self._refreshing = False
        self._stale = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_3, T.SPACE_3, T.SPACE_3, T.SPACE_3)
        layout.setSpacing(T.SPACE_2)
        layout.addLayout(self._build_toolbar())

        self._preview = _ImagePreview()
        self._preview.setMinimumWidth(200)
        self._preview.setScene(QGraphicsScene(self._preview))

        self._items = QListWidget()
        self._items.setObjectName("OCRLines")
        self._items.setWordWrap(True)
        self._items.setSelectionMode(QAbstractItemView.SingleSelection)
        self._items.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                    | QAbstractItemView.AnyKeyPressed)
        delegate = _LineDelegate(self._items)
        # 编辑时别处改了同一文档：先保住输入框，关掉后再刷新列表。
        delegate.closeEditor.connect(lambda *_: QTimer.singleShot(0, self._refresh_if_stale))
        self._items.setItemDelegate(delegate)
        self._items.setContextMenuPolicy(Qt.CustomContextMenu)
        self._items.customContextMenuRequested.connect(self._item_menu)
        self._items.currentItemChanged.connect(self._selected)
        self._items.itemChanged.connect(self._item_edited)
        self._text = QPlainTextEdit()
        self._text.setObjectName("OCRFullText")
        self._text.textChanged.connect(self._draft_changed)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._items)
        self._stack.addWidget(self._text)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._preview)
        splitter.addWidget(self._stack)
        splitter.setSizes([560, 520])
        layout.addWidget(splitter, 1)
        layout.addLayout(self._build_footer())

        self._next_shortcut = QShortcut(QKeySequence("F8"), self, self._next_pending)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self._copy_all)
        self._load(document, pixmap)
        view = self._settings.value("ocr/view", "line")
        self._view_buttons.get(view, self._view_buttons["line"]).setChecked(True)

    def _load(self, document, pixmap):
        """pixmap 为空表示历史纯文字结果，没有原图可对照。"""
        self._document, self._pixmap = document, pixmap
        if pixmap is not None:
            scene = self._preview.scene()
            scene.addPixmap(pixmap)
            self._layer = add_text_layer(scene, document, self._mode)
            size = pixmap.deviceIndependentSize()
            scene.setSceneRect(0, 0, size.width(), size.height())
        self._preview.setVisible(pixmap is not None)
        self._to_table.setEnabled(pixmap is not None)
        document.changed.connect(self._refresh)
        document.focused.connect(self._focus_from_image)
        self._refresh()

    # ---- 构造 ----

    def _build_toolbar(self):
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        frame = QFrame()
        frame.setObjectName("SegmentGroup")
        segments = QHBoxLayout(frame)
        segments.setContentsMargins(1, 1, 1, 1)
        segments.setSpacing(2)
        pill = SelectionPill(frame, "background", "border")
        group = QButtonGroup(frame)
        self._view_buttons = {}
        for view, label, tip in VIEWS:
            button = QToolButton()
            button.setObjectName("Segment")
            button.setProperty("pill", True)
            button.setText(label)
            button.setToolTip(tip)
            button.setCheckable(True)
            button.toggled.connect(lambda on, value=view, target=button: on and (pill.track(target), self._set_view(value)))
            group.addButton(button)
            segments.addWidget(button)
            self._view_buttons[view] = button
        row.addWidget(frame)
        row.addStretch()
        # 只影响复制出去的文字，不改识别结果。
        self._no_breaks = QCheckBox("复制时去换行")
        self._no_spaces = QCheckBox("去空格")
        row.addWidget(self._no_breaks)
        row.addWidget(self._no_spaces)
        return row

    def _build_footer(self):
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        self._status = QLabel()
        self._status.setObjectName("OCRStatus")
        row.addWidget(self._status, 1)
        self._pending = QLabel()
        self._pending.setObjectName("OCRPending")
        self._pending.setToolTip("未修改文字的模型置信度较低，仅供参考；文字正确时无需修改")
        self._next = QPushButton("下一处")
        self._next.setProperty("class", "ghost")
        self._next.setToolTip("跳到下一处低置信度文字 (F8)")
        self._next.clicked.connect(self._next_pending)
        row.addWidget(self._pending)
        row.addWidget(self._next)
        row.addSpacing(T.SPACE_2)
        self._to_table = QPushButton("按表格整理…")
        self._to_table.setProperty("class", "outline")
        self._to_table.setToolTip("复用本次识别的文字，在原图上划分行列后导出 Excel")
        self._to_table.clicked.connect(self._open_table)
        self._copy = QPushButton("复制全文")
        self._copy.setProperty("class", "primary")
        self._copy.setToolTip("复制右侧当前显示的全部文字 (Ctrl+Shift+C)")
        self._copy.clicked.connect(self._copy_all)
        row.addWidget(self._to_table)
        row.addWidget(self._copy)
        return row

    # ---- 视图 ----

    @property
    def view(self):
        return next((view for view, button in self._view_buttons.items() if button.isChecked()), "line")

    def _set_view(self, view):
        self._settings.setValue("ocr/view", view)
        self._stack.setCurrentWidget(self._text if view == "full" else self._items)
        self._refresh()
        if view != "full":
            self._publish_focus()

    def _groups(self):
        if self.view == "paragraph":
            return self._document.groups()
        return [result.source_ids or (result.item_id,) for result in self._document.results]

    def _members(self, ids):
        selected = set(ids)
        return [result for result in self._document.results if selected.intersection(result.source_ids or (result.item_id,))]

    def _ids(self):
        item = self._items.currentItem()
        return tuple(item.data(Qt.UserRole)) if item is not None else ()

    @Slot()
    def _refresh(self):
        if self._items.state() == QAbstractItemView.EditingState:
            self._stale = True
            return
        self._stale = False
        selected_ids = self._ids() or self._document.focused_ids
        pending = QColor(T.THEMES[self._mode]["pending"])
        self._refreshing = True
        with QSignalBlocker(self._items):
            self._items.clear()
            groups = self._groups() if self.view != "full" else []
            for ids in groups:
                members = self._members(ids)
                text = format_text(members)
                original = format_text([replace(member, text=member.original_text) for member in members])
                score = min((member.score for member in members), default=1)
                entry = QListWidgetItem(text)
                entry.setData(Qt.UserRole, ids)
                entry.setData(Qt.UserRole + 1, original)
                entry.setFlags(entry.flags() | Qt.ItemIsEditable)
                if text != original:
                    entry.setToolTip(f"已人工修改；原识别：{original or '（空）'}")
                elif score < _PENDING_SCORE:
                    entry.setData(Qt.BackgroundRole, pending)
                    entry.setToolTip(f"低置信度：模型置信度 {score:.0%}，仅供参考；需要时双击改字")
                self._items.addItem(entry)
            row = next((index for index, ids in enumerate(groups) if set(ids).intersection(selected_ids)), 0)
            self._items.setCurrentRow(row if groups else -1)
        draft = self._document.draft if self._document.draft is not None else self._document.text()
        if self._text.toPlainText() != draft:
            with QSignalBlocker(self._text):
                self._text.setPlainText(draft)
        self._refreshing = False
        self._update_status()

    def _refresh_if_stale(self):
        if self._stale:
            self._refresh()

    def _pending_rows(self):
        return [row for row in range(self._items.count())
                if self._items.item(row).data(Qt.BackgroundRole) is not None]

    def _update_status(self):
        document = self._document
        if not document:
            message = "未检测到文字"
        elif self.view == "full":
            message = ("原图上的改字在全文之后发生，全文未跟着变；已有编辑保留" if document.draft_stale
                       else "全文可自由增删、合并段落，不会改动原图文字")
        else:
            changed = sum(result.text != result.original_text for result in document.results)
            unit = "段" if self.view == "paragraph" else "行"
            message = f"共 {self._items.count()} {unit}" + (f" · 已改 {changed} 处" if changed else "") + " · 双击改字，Enter 提交"
        self._status.setText(message)
        count = len(self._pending_rows()) if self.view != "full" else 0
        self._pending.setText(f"低置信度 {count} 处")
        for widget in (self._pending, self._next):
            widget.setVisible(count > 0)
        self._next_shortcut.setEnabled(count > 0)

    # ---- 联动 ----

    def _publish_focus(self):
        ids = self._ids()
        if ids:
            self._document.focus(ids)

    def _selected(self, current, previous):
        if not self._refreshing:
            self._publish_focus()

    @Slot(object)
    def _focus_from_image(self, ids):
        wanted = set(ids)
        for row in range(self._items.count()):
            if wanted.intersection(self._items.item(row).data(Qt.UserRole)):
                if row != self._items.currentRow():
                    self._refreshing = True
                    self._items.setCurrentRow(row)
                    self._refreshing = False
                break
        if self._layer is not None:
            for item in self._layer.childItems():
                if wanted.intersection(item._ids):
                    self._preview.ensureVisible(item.sceneBoundingRect(), 20, 20)

    def _item_edited(self, item):
        if self._refreshing:
            return
        ids, text = tuple(item.data(Qt.UserRole)), item.text()
        # 改回原文等于撤销人工修改（同表格识别）。
        if text == item.data(Qt.UserRole + 1):
            self._document.reset_correction(ids)
        else:
            self._document.correct(ids, text)

    def _item_menu(self, position):
        item = self._items.itemAt(position)
        if item is None:
            return
        menu = QMenu(self._items)
        menu.addAction("改字", lambda: self._items.editItem(item))
        menu.addAction("复制这条", lambda: QApplication.clipboard().setText(self._formatted(item.text())))
        restore = menu.addAction("恢复识别原文", lambda: self._document.reset_correction(tuple(item.data(Qt.UserRole))))
        restore.setEnabled(item.text() != item.data(Qt.UserRole + 1))
        menu.exec(self._items.viewport().mapToGlobal(position))
        menu.deleteLater()

    def _next_pending(self):
        rows = self._pending_rows()
        if not rows:
            return
        target = next((row for row in rows if row > self._items.currentRow()), rows[0])
        self._items.setCurrentRow(target)
        self._items.scrollToItem(self._items.currentItem())
        self._items.setFocus()

    def _draft_changed(self):
        self._document.set_draft(self._text.toPlainText())

    # ---- 输出 ----

    def _formatted(self, text):
        if self._no_breaks.isChecked():
            text = text.replace("\n", "")
        if self._no_spaces.isChecked():
            text = text.replace(" ", "").replace("　", "")
        return text

    def _commit_editor(self):
        # 快捷键不会先让编辑器失焦，输出前须提交仍在输入框里的改字。
        editor = QApplication.focusWidget()
        if isinstance(editor, QPlainTextEdit) and self._items.isAncestorOf(editor):
            delegate = self._items.itemDelegate()
            delegate.commitData.emit(editor)
            delegate.closeEditor.emit(editor, QStyledItemDelegate.NoHint)
            self._items.setFocus()

    def _copy_all(self):
        self._commit_editor()
        text = self._text.toPlainText() if self.view == "full" else self._document.text()
        QApplication.clipboard().setText(self._formatted(text))
        show_toast("已复制全文")

    def _open_table(self):
        from table import open_table
        self._commit_editor()
        open_table(self._pixmap, self._document.results)
