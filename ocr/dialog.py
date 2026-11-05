"""文字识别窗口：左边原图可缩放、滑选，右边是一份可直接修改的全文（复核窗口方案 A，Clay 2026-11-05 定）。

布局与表格识别窗口一致：上方工具行、中间原图对照、底部状态 + 主操作。
改字只改这份全文，不回写原图文字层（文字层透明，改了也看不见）。上方切换的是排版，不是视图。
全文和原图双向定位：光标所在的识别文字在原图上高亮，点原图上的字，光标跳到全文里对应位置；
低置信度的文字标波浪线，F8 逐处跳转。定位靠在全文里依次找回每条识别文字，改过的那条找不到就不再定位、不再标记。
"""

from PySide6.QtCore import QPointF, QSignalBlocker, Qt, Slot
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPixmap, QShortcut, QTextBlockFormat, QTextCharFormat, QTextCursor, QTransform
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QFrame, QGraphicsScene, QGraphicsTextItem, QGraphicsView,
    QHBoxLayout, QLabel, QPushButton, QSplitter, QTextEdit, QToolButton, QVBoxLayout,
)
from styles import tokens as T
from ui import BaseDialog, PreviewZoom, ask_confirmation, show_toast, standard_window, topmost_button
from ui.motion import SelectionPill
from .layer import add_text_layer
from .layout import LAYOUTS, saved_layout
from .model import OCRDocument

# 这里只提示模型分数，不代表人工复核进度。
_PENDING_SCORE = .8


def _utf16(text):
    """Python 下标转 QTextDocument 位置：后者按 UTF-16 计数，生僻字（扩展区）占两位。"""
    if all(ord(ch) <= 0xFFFF for ch in text):
        return lambda index: index
    offsets, total = [], 0
    for ch in text:
        offsets.append(total)
        total += 2 if ord(ch) > 0xFFFF else 1
    offsets.append(total)
    return offsets.__getitem__


def find_spans(text, results):
    """按阅读顺序在全文里找回每条识别文字，返回 [(起, 止, 条目)]（QTextDocument 位置）。

    下一条反而出现得更早，说明这一条被改掉或删了，后面那处同样的字不是它，跳过。
    """
    position = _utf16(text)
    needles = [(item.text.strip(), item) for item in results]
    spans, start = [], 0
    for index, (needle, item) in enumerate(needles):
        found = text.find(needle, start) if needle else -1
        if found < 0:
            continue
        following = next((value for value, _item in needles[index + 1:] if value), "")
        ahead = text.find(following, start) if following else -1
        if 0 <= ahead < found:
            continue
        spans.append((position(found), position(found + len(needle)), item))
        start = found + len(needle)
    return spans


class _ImagePreview(QGraphicsView):
    """原图预览，缩放与拖动交给 ui.zoom（同表格识别）。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("OCRPreview")
        self.setAlignment(Qt.AlignCenter)
        # 缩小显示时不开平滑，文字笔画会被隔行丢掉，看着发虚、锯齿。
        self.setRenderHint(QPainter.SmoothPixmapTransform)

    def zoom_scale(self):
        return self.transform().m11()

    def set_zoom_scale(self, scale):
        self.setTransform(QTransform.fromScale(scale, scale))

    def fit_zoom_scale(self, size):
        rect = self.sceneRect()
        # 历史纯文字结果没有原图，预览隐藏，场景为空。
        if rect.isEmpty():
            return 1.0
        return min(size.width() / rect.width(), size.height() / rect.height())

    def to_content(self, point):
        return self.mapToScene(point)

    def from_content(self, point):
        return QPointF(self.mapFromScene(point))

    def pans_at(self, point):
        return not isinstance(self.itemAt(point), QGraphicsTextItem)


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
        standard_window(self)
        self.resize(1100, 680)
        self._mode = QApplication.instance().property("theme_mode") or "light"
        self._layer = None
        self._spans = []
        # 两边互相定位时，别让对方的回应再反过来挪自己。
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_3, T.SPACE_3, T.SPACE_3, T.SPACE_3)
        layout.setSpacing(T.SPACE_2)
        layout.addLayout(self._build_toolbar())

        self._preview = _ImagePreview()
        self._preview.setMinimumWidth(200)
        self._preview.setScene(QGraphicsScene(self._preview))
        self._zoom = PreviewZoom(self._preview, self._preview, self)

        self._text = QTextEdit()
        self._text.setObjectName("OCRFullText")
        self._text.setAcceptRichText(False)
        self._text.textChanged.connect(self._edited)
        self._text.cursorPositionChanged.connect(self._cursor_moved)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._preview)
        splitter.addWidget(self._text)
        splitter.setSizes([560, 520])
        layout.addWidget(splitter, 1)
        layout.addLayout(self._build_footer())

        self._next_shortcut = QShortcut(QKeySequence("F8"), self, self._next_pending)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self._copy_all)
        self._load(document, pixmap)

    def _load(self, document, pixmap):
        """pixmap 为空表示历史纯文字结果，没有原图可对照。"""
        self._document, self._pixmap = document, pixmap
        if pixmap is not None:
            scene = self._preview.scene()
            scene.addPixmap(pixmap).setTransformationMode(Qt.SmoothTransformation)
            self._layer = add_text_layer(scene, document, self._mode)
            size = pixmap.deviceIndependentSize()
            scene.setSceneRect(0, 0, size.width(), size.height())
            self._zoom.fit()
        self._preview.setVisible(pixmap is not None)
        self._to_table.setEnabled(pixmap is not None)
        # 没改过的结果按上次选的排版打开；改过的保持改的时候那种排版。
        if document.draft is None:
            document.set_layout(saved_layout(self._settings))
        self._layout_buttons[document.layout].setChecked(True)
        document.changed.connect(self._refresh)
        document.focused.connect(self._focused)
        self._refresh()

    # ---- 构造 ----

    def _build_toolbar(self):
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        caption = QLabel("排版")
        caption.setObjectName("OCRStatus")
        row.addWidget(caption)
        frame = QFrame()
        frame.setObjectName("SegmentGroup")
        segments = QHBoxLayout(frame)
        segments.setContentsMargins(1, 1, 1, 1)
        segments.setSpacing(2)
        pill = SelectionPill(frame, "background", "border")
        group = QButtonGroup(frame)
        self._layout_buttons = {}
        for mode, label, tip in LAYOUTS:
            button = QToolButton()
            button.setObjectName("Segment")
            button.setProperty("pill", True)
            button.setText(label)
            button.setToolTip(tip)
            button.setCheckable(True)
            button.toggled.connect(lambda on, value=mode, target=button: on and (pill.track(target), self._choose_layout(value)))
            group.addButton(button)
            segments.addWidget(button)
            self._layout_buttons[mode] = button
        row.addWidget(frame)
        row.addStretch()
        # 只影响复制出去的文字，不改全文。
        self._no_spaces = QCheckBox("去空格")
        self._no_spaces.setToolTip("复制时去掉所有空格")
        row.addWidget(self._no_spaces)
        row.addWidget(topmost_button(self, "window/ocr_topmost"))
        return row

    def _build_footer(self):
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        self._status = QLabel()
        self._status.setObjectName("OCRStatus")
        row.addWidget(self._status, 1)
        self._pending = QLabel()
        self._pending.setObjectName("OCRPending")
        self._pending.setToolTip("模型置信度较低的文字标了波浪线，仅供参考；文字正确时无需修改")
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
        self._copy.setToolTip("复制右侧全部文字 (Ctrl+Shift+C)")
        self._copy.clicked.connect(self._copy_all)
        row.addWidget(self._to_table)
        row.addWidget(self._copy)
        return row

    # ---- 排版与全文 ----

    def _choose_layout(self, mode):
        document = self._document
        if mode == document.layout:
            return
        if document.draft is not None and not ask_confirmation(
                self, "重新排版", "重新排版会按识别结果重新整理全文，你改过的文字会丢掉。", confirm_text="重新排版"):
            self._layout_buttons[document.layout].setChecked(True)
            return
        self._settings.setValue("ocr/layout", mode)
        document.set_layout(mode)

    @Slot()
    def _refresh(self):
        """文档变了（换排版、别的窗口改了同一份全文）：编辑框跟上，光标尽量留在原处。"""
        text = self._document.text()
        if self._text.toPlainText() != text:
            position = self._text.textCursor().position()
            with QSignalBlocker(self._text):
                self._text.setPlainText(text)
                self._format_blocks()
                cursor = self._text.textCursor()
                cursor.setPosition(min(position, len(self._text.toPlainText())))
                self._text.setTextCursor(cursor)
        self._layout_buttons[self._document.layout].setChecked(True)
        self._update_marks()

    def _format_blocks(self):
        # 行距放宽一点便于对照；“段落”排版一段只有一行，折行后看不出分段，段后再留点空。不进撤销记录。
        document = self._text.document()
        block = QTextBlockFormat()
        block.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        block.setBottomMargin(T.SPACE_2 if self._document.layout == "paragraph" else 0)
        document.setUndoRedoEnabled(False)
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.Document)
        cursor.mergeBlockFormat(block)
        document.setUndoRedoEnabled(True)

    def _edited(self):
        self._document.set_draft(self._text.toPlainText())
        self._update_marks()

    def _update_marks(self):
        self._spans = find_spans(self._text.toPlainText(), self._document.results)
        self._paint_marks()
        self._update_status()

    def _paint_marks(self):
        theme = T.THEMES[self._mode]
        pending = QTextCharFormat()
        pending.setBackground(QColor(theme["pending"]))
        pending.setUnderlineStyle(QTextCharFormat.WaveUnderline)
        pending.setUnderlineColor(QColor(theme["pending_line"]))
        focused = QTextCharFormat()
        focused.setBackground(QColor(*T.OCR_HOVER))
        wanted = set(self._document.focused_ids)
        selections = []
        for start, end, item in self._spans:
            for style, show in ((pending, item.score < _PENDING_SCORE), (focused, item.item_id in wanted)):
                if show:
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = QTextCursor(self._text.document())
                    selection.cursor.setPosition(start)
                    selection.cursor.setPosition(end, QTextCursor.KeepAnchor)
                    selection.format = style
                    selections.append(selection)
        self._text.setExtraSelections(selections)

    def _pending_spans(self):
        return [span for span in self._spans if span[2].score < _PENDING_SCORE]

    def _update_status(self):
        if not self._document:
            message = "未检测到文字"
        else:
            message = "已修改，复制以这里为准" if self._document.draft is not None else "可直接修改文字"
            if self._layer is not None:
                message += " · 点原图上的字，定位到这里"
        self._status.setText(message)
        count = len(self._pending_spans())
        self._pending.setText(f"低置信度 {count} 处")
        for widget in (self._pending, self._next):
            widget.setVisible(count > 0)
        self._next_shortcut.setEnabled(count > 0)

    # ---- 联动 ----

    def _cursor_moved(self):
        if self._syncing:
            return
        position = self._text.textCursor().position()
        span = next((span for span in self._spans if span[0] <= position <= span[1]), None)
        if span is not None and (span[2].item_id,) != self._document.focused_ids:
            self._syncing = True
            self._document.focus((span[2].item_id,))
            self._syncing = False

    @Slot(object)
    def _focused(self, ids):
        wanted = set(ids)
        self._paint_marks()
        span = next((span for span in self._spans if span[2].item_id in wanted), None)
        # 从原图点过来的：光标挪到全文里那处，不抢键盘焦点。
        if span is not None and not self._syncing:
            self._syncing = True
            cursor = self._text.textCursor()
            cursor.setPosition(span[0])
            self._text.setTextCursor(cursor)
            self._text.ensureCursorVisible()
            self._syncing = False
        if self._layer is not None:
            visible = self._preview.mapToScene(self._preview.viewport().rect()).boundingRect()
            for item in self._layer.childItems():
                rect = item.sceneBoundingRect()
                # 只在这一行完全不在视野里时才滚：在原图上点字也会走到这里，放大后一点就跳会很怪；
                # 整行比视野还宽时对准行首，而不是被拉到行尾。
                if wanted.intersection(item._ids) and not visible.intersects(rect):
                    rect.setWidth(min(rect.width(), visible.width() / 2))
                    self._preview.ensureVisible(rect, 20, 20)

    def _next_pending(self):
        spans = self._pending_spans()
        if not spans:
            return
        position = self._text.textCursor().position()
        start = next((span[0] for span in spans if span[0] > position), spans[0][0])
        cursor = self._text.textCursor()
        cursor.setPosition(start)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()
        self._text.setFocus()

    # ---- 输出 ----

    def _copy_all(self):
        text = self._document.text()
        if self._no_spaces.isChecked():
            text = text.replace(" ", "").replace("\u3000", "")
        QApplication.clipboard().setText(text)
        show_toast("已复制全文")

    def _open_table(self):
        from table import open_table
        open_table(self._pixmap, self._document.results)
