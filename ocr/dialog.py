"""逐条/逐段校正联动原图，全文自由编辑保留为独立导出草稿。"""

from PySide6.QtCore import QSignalBlocker, Qt, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)
from ui import BaseDialog
from .layer import add_text_layer
from .layout import format_text
from .model import OCRDocument


class _ImagePreview(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def showEvent(self, event):
        super().showEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)


class OCRResultDialog(BaseDialog):
    def __init__(self, results, parent=None, pixmap=None):
        super().__init__(parent)
        from settings import get_settings
        self._settings = get_settings()
        image = pixmap.toImage() if pixmap is not None else None
        self._document = results if isinstance(results, OCRDocument) else OCRDocument(results, image)
        self._results = self._document
        if pixmap is None and not self._document.image.isNull():
            pixmap = QPixmap.fromImage(self._document.image)
        self.setWindowTitle("文字复核与校正 · 本地 OCR")
        self.resize(1100, 690)
        self._selection_source = None
        self._selection_text = ""
        self._refreshing = False
        self._pending = {}
        self._editing_ids = ()
        self._metadata_base = ""
        self._owns_focus = False
        self._publishing_focus = False
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("中文 / English · 本地识别"))
        row.addStretch()
        self._show_image = QCheckBox("显示原图与文字层")
        self._show_image.setChecked(pixmap is not None)
        self._show_image.setEnabled(pixmap is not None)
        row.addWidget(self._show_image)
        layout.addLayout(row)
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._preview = _ImagePreview()
        self._preview.setMinimumWidth(200)
        scene = QGraphicsScene(self._preview)
        self._preview.setScene(scene)
        self._layer = None
        if pixmap is not None:
            scene.addPixmap(pixmap)
            mode = QApplication.instance().property("theme_mode") or "light"
            self._layer = add_text_layer(scene, self._document, mode)
            self._layer.selectionChanged.connect(self._selection_changed)
            size = pixmap.deviceIndependentSize()
            scene.setSceneRect(0, 0, size.width(), size.height())
        self._preview.setVisible(pixmap is not None)
        self._show_image.toggled.connect(self._preview.setVisible)
        self._splitter.addWidget(self._preview)
        self._tabs = QTabWidget()
        correction = QWidget()
        panel = QVBoxLayout(correction)
        selection = QHBoxLayout()
        selection.addWidget(QLabel("校正单位"))
        self._unit = QComboBox()
        self._unit.addItem("逐条校正", "item")
        self._unit.addItem("逐段校正", "paragraph")
        self._unit.currentIndexChanged.connect(self._unit_changed)
        selection.addWidget(self._unit)
        selection.addStretch()
        panel.addLayout(selection)
        self._items = QListWidget()
        self._items.setMinimumHeight(100)
        self._items.currentItemChanged.connect(self._selected)
        self._items.itemClicked.connect(self._clicked)
        panel.addWidget(self._items, 2)
        self._metadata = QLabel()
        self._metadata.setWordWrap(True)
        panel.addWidget(self._metadata)
        panel.addWidget(QLabel("原始识别文字（保留不变）"))
        self._original = QPlainTextEdit()
        self._original.setReadOnly(True)
        self._original.setMaximumHeight(70)
        panel.addWidget(self._original)
        panel.addWidget(QLabel("校正文字 · 应用后同步原图、全文和贴图"))
        self._editor = QPlainTextEdit()
        self._editor.textChanged.connect(self._editor_changed)
        self._editor.setMinimumHeight(65)
        panel.addWidget(self._editor, 1)
        actions = QHBoxLayout()
        self._reset = QPushButton("恢复原始文字")
        self._reset.setProperty("class", "outline")
        self._reset.clicked.connect(self._reset_selected)
        self._apply = QPushButton("应用校正")
        # 底部"复制校正结果"是本窗口唯一主操作，应用校正降为次按钮。
        self._apply.setProperty("class", "secondary")
        self._apply.clicked.connect(self._apply_correction)
        actions.addWidget(self._reset)
        actions.addStretch()
        actions.addWidget(self._apply)
        panel.addLayout(actions)
        self._tabs.addTab(correction, "原图联动校正")
        draft = QWidget()
        draft_layout = QVBoxLayout(draft)
        self._draft_notice = QLabel()
        self._draft_notice.setWordWrap(True)
        draft_layout.addWidget(self._draft_notice)
        self._text = QPlainTextEdit()
        self._text.setLayoutDirection(Qt.LeftToRight)
        self._text.textChanged.connect(self._draft_changed)
        self._text.selectionChanged.connect(self._draft_selection_changed)
        draft_layout.addWidget(self._text)
        draft_actions = QHBoxLayout()
        draft_actions.addStretch()
        copy_draft = QPushButton("复制导出草稿")
        copy_draft.setProperty("class", "outline")
        copy_draft.clicked.connect(self._copy_draft)
        draft_actions.addWidget(copy_draft)
        draft_layout.addLayout(draft_actions)
        self._tabs.addTab(draft, "独立导出草稿")
        self._splitter.addWidget(self._tabs)
        self._splitter.setSizes([450, 600])
        layout.addWidget(self._splitter, 1)
        formats = QHBoxLayout()
        self._no_breaks = QCheckBox("去换行")
        self._no_spaces = QCheckBox("去空格")
        self._copy_on_close = QCheckBox("关闭时复制校正结果")
        self._direct = QCheckBox("下次直接复制")
        self._direct.setChecked(self._settings.value("ocr/direct_copy", False, type=bool))
        self._direct.toggled.connect(self._set_direct_copy)
        self._auto_close = QCheckBox("复制后关闭")
        for widget in (self._no_breaks, self._no_spaces, self._copy_on_close, self._direct, self._auto_close):
            formats.addWidget(widget)
        layout.addLayout(formats)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self._copy_selection = QPushButton("复制选中")
        self._copy_selection.setProperty("class", "outline")
        self._copy_selection.setEnabled(False)
        self._copy_selection.clicked.connect(self._copy_selected)
        to_table = QPushButton("按表格整理…")
        to_table.setProperty("class", "outline")
        to_table.setToolTip("复用本次识别的文字，在原图上划分行列后导出 Excel")
        to_table.setEnabled(not self._document.image.isNull())
        to_table.clicked.connect(self._open_table)
        buttons.insertWidget(0, to_table)
        close = QPushButton("关闭")
        close.setProperty("class", "secondary")
        close.clicked.connect(self.accept)
        all_text = QPushButton("复制校正结果")
        all_text.setProperty("class", "primary")
        all_text.clicked.connect(self._copy_all)
        for widget in (self._copy_selection, close, all_text):
            buttons.addWidget(widget)
        layout.addLayout(buttons)
        self._document.changed.connect(self._refresh)
        self._document.focused.connect(self._focus_from_image)
        self._refresh()

    @Slot(bool)
    def _set_direct_copy(self, value):
        self._settings.setValue("ocr/direct_copy", value)

    def _ids(self):
        item = self._items.currentItem()
        return tuple(item.data(Qt.UserRole)) if item is not None else ()

    @Slot()
    def _refresh(self):
        selected_ids = self._ids() or self._document.focused_ids
        self._refreshing = True
        self._items.clear()
        if self._unit.currentData() == "paragraph":
            groups = self._document.groups()
        else:
            groups = [result.source_ids or (result.item_id,) for result in self._document.results]
        for index, ids in enumerate(groups):
            displayed = [result for result in self._document.results if set(ids).intersection(result.source_ids or (result.item_id,))]
            text = format_text(displayed)
            entry = QListWidgetItem(f"{index + 1}. {text.replace(chr(10), ' ')[:90] or '（校正为空）'}")
            entry.setData(Qt.UserRole, ids)
            entry.setToolTip(text)
            self._items.addItem(entry)
        row = next((index for index, ids in enumerate(groups) if set(ids).intersection(selected_ids)), 0)
        self._items.setCurrentRow(row if groups else -1)
        draft_text = self._document.draft if self._document.draft is not None else self._document.text()
        if self._text.toPlainText() != draft_text:
            self._text.setPlainText(draft_text)
        self._draft_notice.setText(
            "独立导出草稿：原图校正已变化，草稿未同步；已有编辑已保留。" if self._document.draft_stale else
            "独立导出草稿：可任意增删或合并段落；不会覆盖校正结果，也不映射回原图。")
        self._refreshing = False
        self._load_selected()
        # 只有发起定位的窗口负责调整拆回后的范围，其他窗口仅跟随，避免互相抢焦点。
        if self._owns_focus and self._ids() != selected_ids:
            self._publish_focus()

    def _current_text(self, ids):
        selected = set(ids)
        current = [result for result in self._document.results if selected.intersection(result.source_ids or (result.item_id,))]
        return format_text(current)

    def _load_selected(self):
        ids = self._ids()
        selected = set(ids)
        raw = [result for result in self._document.raw_results if result.item_id in selected]
        current = [result for result in self._document.results if selected.intersection(result.source_ids or (result.item_id,))]
        self._original.setPlainText("\n".join(result.original_text for result in raw))
        self._editing_ids = ids
        text = self._pending[ids][0] if ids in self._pending else format_text(current)
        if self._editor.toPlainText() != text:
            with QSignalBlocker(self._editor):
                self._editor.setPlainText(text)
        score = min((result.score for result in raw), default=0)
        confidence = f"模型置信度 {score:.0%}（仅供复核参考）" if raw else "未检测到文字"
        if raw and score < .8:
            confidence += " · 建议优先复核，文字未自动删除"
        if len(ids) > 1:
            confidence += "\n段落修改按整体区域定位，不将新文字猜测分配给旧行。"
        changed = any(result.text != result.original_text for result in current)
        if changed:
            confidence += " · 已人工校正"
        self._metadata_base = confidence
        self._update_pending_notice()
        self._apply.setEnabled(bool(ids))
        self._reset.setEnabled(bool(ids))
        self._editor.setEnabled(bool(ids))

    def _update_pending_notice(self):
        message = self._metadata_base
        pending = self._pending.get(self._editing_ids)
        if pending is not None:
            message += "\n有待应用校正，输入仅在当前窗口保留。"
            if self._current_text(self._editing_ids) != pending[1]:
                message += " 当前结果已在其他位置修改；你的输入已保留，应用将替换当前结果。"
        self._metadata.setText(message)

    @Slot()
    def _editor_changed(self):
        ids = self._editing_ids
        current = self._current_text(ids)
        text = self._editor.toPlainText()
        if text == current:
            self._pending.pop(ids, None)
        else:
            previous = self._pending.get(ids)
            self._pending[ids] = (text, previous[1] if previous is not None else current)
        self._update_pending_notice()

    def _publish_focus(self):
        self._publishing_focus = True
        self._document.focus(self._ids())
        self._publishing_focus = False

    def _unit_changed(self):
        self._refresh()
        self._publish_focus()

    def _clicked(self, item):
        self._load_selected()
        self._publish_focus()

    def _selected(self, current, previous):
        if self._refreshing:
            return
        self._load_selected()
        self._publish_focus()

    @Slot(object)
    def _focus_from_image(self, ids):
        self._owns_focus = self._publishing_focus
        wanted = set(ids)
        for row in range(self._items.count()):
            if wanted.intersection(self._items.item(row).data(Qt.UserRole)):
                self._refreshing = True
                self._items.setCurrentRow(row)
                self._refreshing = False
                self._load_selected()
                break
        if self._layer is not None:
            for item in self._layer.childItems():
                if wanted.intersection(item._ids):
                    self._preview.ensureVisible(item.sceneBoundingRect(), 20, 20)

    def _apply_correction(self):
        ids, text = self._ids(), self._editor.toPlainText()
        self._pending.pop(ids, None)
        self._publish_focus()
        self._document.correct(ids, text)

    def _reset_selected(self):
        ids = self._ids()
        self._pending.pop(ids, None)
        self._publish_focus()
        self._document.reset_correction(ids)

    def _draft_changed(self):
        if not self._refreshing:
            self._document.set_draft(self._text.toPlainText())

    def _draft_selection_changed(self):
        self._selection_changed(self._text, self._text.textCursor().selectedText())

    @Slot(object, str)
    def _selection_changed(self, source, text):
        if text:
            self._selection_source = source
            self._selection_text = text.replace("\u2029", "\n")
        elif self._selection_source is source or (source is self._layer and self._selection_source is not self._text):
            self._selection_text = ""
        self._copy_selection.setEnabled(bool(self._selection_text))

    def _formatted(self, text):
        if self._no_breaks.isChecked():
            text = text.replace("\n", "")
        if self._no_spaces.isChecked():
            text = text.replace(" ", "").replace("\u3000", "")
        return text

    def _copy_value(self, text):
        QApplication.clipboard().setText(self._formatted(text))
        if self._auto_close.isChecked():
            self.accept()

    def _open_table(self):
        from table import open_table
        open_table(self._document.image, self._document.raw_results)

    def _copy_all(self):
        self._copy_value(self._document.text())

    def _copy_draft(self):
        self._copy_value(self._text.toPlainText())

    def _copy_selected(self):
        self._copy_value(self._selection_text)

    def done(self, result):
        if self._copy_on_close.isChecked():
            QApplication.clipboard().setText(self._formatted(self._document.text()))
        super().done(result)
