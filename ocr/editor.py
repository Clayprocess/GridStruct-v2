"""一份可直接修改的识别全文：文字识别窗口和截图里的识别卡片共用。

改字只写回 OCRDocument.draft，不回写原图文字层（文字层透明，改了也看不见）。
全文和原图双向定位：光标所在的识别文字在原图上高亮，点原图上的字，光标跳到这里对应位置；
低置信度的文字标波浪线，F8 逐处跳转。定位靠在全文里依次找回每条识别文字，改过的那条找不到就不再定位、不再标记。
"""

from difflib import SequenceMatcher

from PySide6.QtCore import QSignalBlocker, Signal, Slot
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit
from styles import tokens as T
from ui import ask_confirmation
from .layout import saved_layout

# 这里只提示模型分数，不代表人工复核进度。
PENDING_SCORE = .8


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


def find_spans(text, original, results):
    """把每条识别文字在全文里的位置找出来，返回 [(起, 止, 条目)]（QTextDocument 位置）。

    original 是排版整理出的原文，条目在里面按阅读顺序依次出现，位置是确定的；再按改动把位置
    映射到当前全文：只有整条落在没改过的片段里才保留，改过、删掉的那条不再定位。
    不在全文里直接搜字，是因为重复的字（两处“100”）会被改过的那条占走。
    """
    base, start = [], 0
    for item in results:
        needle = item.text.strip()
        found = original.find(needle, start) if needle else -1
        if found >= 0:
            base.append((found, found + len(needle), item))
            start = found + len(needle)
    if text != original:
        # 改动通常集中在一处：先去掉相同的头尾，只对中间做比对，长文逐字输入也不卡。
        head = 0
        while head < min(len(text), len(original)) and text[head] == original[head]:
            head += 1
        tail = 0
        while tail < min(len(text), len(original)) - head and text[-1 - tail] == original[-1 - tail]:
            tail += 1
        blocks = [(0, 0, head), (len(original) - tail, len(text) - tail, tail)]
        matcher = SequenceMatcher(None, original[head:len(original) - tail], text[head:len(text) - tail], autojunk=False)
        blocks += [(a + head, b + head, size) for a, b, size in matcher.get_matching_blocks() if size]
        kept = []
        for begin, end, item in base:
            block = next((block for block in blocks if block[0] <= begin and end <= block[0] + block[2]), None)
            if block is not None:
                kept.append((begin + block[1] - block[0], end + block[1] - block[0], item))
        base = kept
    position = _utf16(text)
    return [(position(begin), position(end), item) for begin, end, item in base]


def span_at(spans, position):
    """光标所在的条目：中文合行时前一条的终点就是下一条的起点，起点优先归下一条。"""
    return next((span for span in spans if span[0] <= position < span[1]), None) \
        or next((span for span in spans if span[1] == position), None)


class OCRTextEdit(QTextEdit):
    """绑定一份 OCRDocument 的全文编辑框；marksChanged 在定位、低置信度标记更新后发出，供外面刷新状态。"""

    marksChanged = Signal()

    def __init__(self, mode="light", parent=None):
        super().__init__(parent)
        self.setObjectName("OCRFullText")
        self.setAcceptRichText(False)
        self._mode = mode
        self._document = None
        self.spans = []
        # 两边互相定位时，别让对方的回应再反过来挪自己。
        self._syncing = False
        self.textChanged.connect(self._edited)
        self.cursorPositionChanged.connect(self._cursor_moved)

    @property
    def document_model(self):
        return self._document

    def set_document(self, document):
        """换绑另一份识别结果；None 表示清空。没改过的结果按上次选的排版打开，改过的保持改的时候那种。"""
        if self._document is not None:
            self._document.changed.disconnect(self._refresh)
            self._document.focused.disconnect(self._focused)
        self._document = document
        if document is None:
            self.spans = []
            with QSignalBlocker(self):
                self.clear()
            self.setExtraSelections([])
            self.marksChanged.emit()
            return
        if document.draft is None:
            from settings import get_settings
            document.set_layout(saved_layout(get_settings()))
        document.changed.connect(self._refresh)
        document.focused.connect(self._focused)
        self._refresh()

    def set_mode(self, mode):
        self._mode = mode
        if self._document is not None:
            self._paint_marks()

    def choose_layout(self, mode):
        """切换排版：改过的全文会被重排丢掉，先确认。返回是否换成了。"""
        document = self._document
        if mode == document.layout:
            return True
        if document.draft is not None and not ask_confirmation(
                self.window(), "重新排版", "重新排版会按识别结果重新整理全文，你改过的文字会丢掉。",
                confirm_text="重新排版"):
            return False
        from settings import get_settings
        get_settings().setValue("ocr/layout", mode)
        document.set_layout(mode)
        return True

    def pending_spans(self):
        return [span for span in self.spans if span[2].score < PENDING_SCORE]

    def next_pending(self):
        spans = self.pending_spans()
        if not spans:
            return
        position = self.textCursor().position()
        start = next((span[0] for span in spans if span[0] > position), spans[0][0])
        cursor = self.textCursor()
        cursor.setPosition(start)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        self.setFocus()

    def content_height(self, width):
        """排好版后整篇的高度（含边距），卡片按它决定编辑框高几行。"""
        document = self.document().clone(self)
        document.setTextWidth(width)
        height = document.size().height()
        document.deleteLater()
        return height

    # ---- 文档与编辑框同步 ----

    @Slot()
    def _refresh(self):
        """文档变了（换排版、别的窗口改了同一份全文）：编辑框跟上，光标尽量留在原处。"""
        text = self._document.text()
        if self.toPlainText() != text:
            position = self.textCursor().position()
            with QSignalBlocker(self):
                self.setPlainText(text)
                self._format_blocks()
                cursor = self.textCursor()
                cursor.setPosition(min(position, len(self.toPlainText())))
                self.setTextCursor(cursor)
        self._update_marks()

    def _format_blocks(self):
        # 行距放宽一点便于对照；“段落”排版一段只有一行，折行后看不出分段，段后再留点空。不进撤销记录。
        document = self.document()
        block = QTextBlockFormat()
        block.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        block.setBottomMargin(T.SPACE_2 if self._document.layout == "paragraph" else 0)
        document.setUndoRedoEnabled(False)
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.Document)
        cursor.mergeBlockFormat(block)
        document.setUndoRedoEnabled(True)

    def _edited(self):
        if self._document is None:
            return
        self._document.set_draft(self.toPlainText())
        self._update_marks()

    def _update_marks(self):
        self.spans = find_spans(self.toPlainText(), self._document.recognized_text(), self._document.results)
        self._paint_marks()
        self.marksChanged.emit()

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
        for start, end, item in self.spans:
            for style, show in ((pending, item.score < PENDING_SCORE), (focused, item.item_id in wanted)):
                if show:
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = QTextCursor(self.document())
                    selection.cursor.setPosition(start)
                    selection.cursor.setPosition(end, QTextCursor.KeepAnchor)
                    selection.format = style
                    selections.append(selection)
        self.setExtraSelections(selections)

    # ---- 联动 ----

    def _cursor_moved(self):
        if self._syncing or self._document is None:
            return
        span = span_at(self.spans, self.textCursor().position())
        if span is not None and (span[2].item_id,) != self._document.focused_ids:
            self._syncing = True
            self._document.focus((span[2].item_id,))
            self._syncing = False

    @Slot(object)
    def _focused(self, ids):
        self._paint_marks()
        span = next((span for span in self.spans if span[2].item_id in set(ids)), None)
        # 从原图点过来的：光标挪到全文里那处，不抢键盘焦点。
        if span is not None and not self._syncing:
            self._syncing = True
            cursor = self.textCursor()
            cursor.setPosition(span[0])
            self.setTextCursor(cursor)
            self.ensureCursorVisible()
            self._syncing = False
