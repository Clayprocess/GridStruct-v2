"""按网格把 OCR 文字分进格子，并保护人工校对结果。

分格规则移植自 v1 domain/layout.py；"重新分格不能覆盖人工结果"移植自 v1 domain/table_review.py。
v1 只允许同形网格重跑，这里网格随拖线实时变化，所以按人工格的中心点找回新位置。
"""

from bisect import bisect_right
from dataclasses import replace
from statistics import median
from unicodedata import east_asian_width

from .model import CONFIRMED, CORRECTED, PENDING, CellCorrection, Rect, TableCell, TableDocument


def visual_rows(tokens):
    """同一格内的文字按行聚类，行内从左到右。"""
    if not tokens:
        return ()
    threshold = max(2.0, median(token.box.height for token in tokens) * 0.6)
    rows, centers = [], []
    for token in sorted(tokens, key=lambda item: (item.box.center_y, item.box.left)):
        if not rows or abs(token.box.center_y - centers[-1]) > threshold:
            rows.append([token])
            centers.append(token.box.center_y)
            continue
        rows[-1].append(token)
        centers[-1] = sum(item.box.center_y for item in rows[-1]) / len(rows[-1])
    return tuple(tuple(sorted(row, key=lambda item: item.box.left)) for row in rows)


def _latin(ch):
    # 拉丁字母（含带重音的西文字母）；数字、汉字、全角符号都不算。
    return ch.isalpha() and ord(ch) < 0x250


def join_lines(lines):
    """格内多行合成一行：两边都是英文字母才补空格，数字、中文不补（Clay 2026-11-03 实测决定）。

    英文句读后接字母也补空格，免得 "Hello,world"；行尾连字符原样保留、不补空格，
    不猜它是断词还是本来的连字符。
    """
    text = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if text and (_latin(text[-1]) or text[-1] in ",.;:!?)]") and _latin(line[0]):
            text += " "
        text += line
    return text


def _char_spans(token):
    """token.text 每个字符的横向范围。

    优先用识别模型的逐字位置（实测误差约 2px）；没有或对不上时按字宽估算：
    中文等全角字算 1，西文、数字、半角符号算 0.55，空格算 0.35。
    """
    text = token.text
    glyphs = [index for index, ch in enumerate(text) if not ch.isspace()]
    if token.chars and len(token.chars) == len(glyphs) and all(
            text[index] == ch for index, (ch, _, _) in zip(glyphs, token.chars)):
        spans = [None] * len(text)
        for index, (_, left, right) in zip(glyphs, token.chars):
            spans[index] = (left, right)
        edge = token.box.left
        for index, span in enumerate(spans):
            # 空格跟着前一个字，切开后会被 strip 掉。
            spans[index] = span or (edge, edge)
            edge = spans[index][1]
        return spans
    weights = [0.35 if ch.isspace() else 1.0 if east_asian_width(ch) in "WF" else 0.55 for ch in text]
    total = sum(weights) or 1.0
    scale = token.box.width / total
    spans, edge = [], token.box.left
    for weight in weights:
        spans.append((edge, edge + weight * scale))
        edge += weight * scale
    return spans


def split_at_columns(token, grid):
    """OCR 常把同一基线上、分属相邻两列的文字合成一个框（长账号顶满列宽、窄列文字贴线）。

    按中心点整条落格会让左列多吃、右列丢字，所以分格前沿列线把框切开：
    每个字按自己的中心归到列线某一侧。合并格内部的线两侧本是同一格，不切。
    检测框只是略微压线、字都在一侧时原样返回，不算切开。
    """
    row = grid.cell_at(token.box.center_x, token.box.center_y)[0]
    lines = grid.vertical_lines
    cuts = [lines[index] for index in range(1, len(lines) - 1)
            if token.box.left < lines[index] < token.box.right
            and grid.anchor(row, index - 1) != grid.anchor(row, index)]
    if not cuts:
        return (token,)
    groups = {}
    for ch, (left, right) in zip(token.text, _char_spans(token)):
        groups.setdefault(bisect_right(cuts, (left + right) / 2), []).append((ch, left, right))
    parts = []
    for _, chars in sorted(groups.items()):
        text = "".join(ch for ch, _, _ in chars).strip()
        glyphs = tuple((ch, left, right) for ch, left, right in chars if not ch.isspace())
        if not text:
            continue
        left = round(min(left for _, left, _ in glyphs))
        right = max(left + 1, round(max(right for _, _, right in glyphs)))
        parts.append((text, Rect(left, token.box.top, right, token.box.bottom), glyphs))
    if len(parts) < 2:
        return (token,)
    return tuple(replace(token, text=text, box=box, chars=glyphs, split=True) for text, box, glyphs in parts)


def build_document(tokens, grid):
    """文字按中心点落格，落进合并区域的归锚点格；跨列线的文字框先沿线切开。"""
    buckets = [[[] for _ in range(grid.column_count)] for _ in range(grid.row_count)]
    for token in (part for token in tokens for part in split_at_columns(token, grid)):
        row, column = grid.anchor(*grid.cell_at(token.box.center_x, token.box.center_y))
        buckets[row][column].append(token)
    cells = []
    for row_tokens in buckets:
        row = []
        for cell_tokens in row_tokens:
            lines = visual_rows(tuple(cell_tokens))
            text = join_lines(" ".join(token.text.strip() for token in line) for line in lines)
            cell = TableCell(text, text, tuple(token for line in lines for token in line))
            row.append(replace(cell, status=PENDING) if cell.needs_review else cell)
        cells.append(tuple(row))
    return TableDocument(grid, tuple(cells))


def unreplaced_tokens(tokens, corrections):
    """排除人工区域内的原文，避免重新分格或确认时复活已替换文字。"""
    return tuple(token for token in tokens if not any(
        part.box.left <= token.box.center_x < part.box.right
        and part.box.top <= token.box.center_y < part.box.bottom for part in corrections))


def carry_over(base, recognized):
    """校正按原区域落格；合并只改变展示，拆分时仍使用各条校正的原位置。"""
    grouped = {}
    for row, values in enumerate(base.cells):
        for column, cell in enumerate(values):
            if base.grid.is_covered(row, column):
                continue
            corrections = cell.corrections
            if not corrections and cell.human:
                corrections = (CellCorrection(base.grid.cell_rect(row, column), cell.text, cell.status),)
            for correction in corrections:
                rect = correction.box
                target = recognized.grid.anchor(*recognized.grid.cell_at(rect.center_x, rect.center_y))
                grouped.setdefault(target, []).append(correction)
    all_corrections = tuple(part for parts in grouped.values() for part in parts)
    cells = []
    for row, values in enumerate(recognized.cells):
        updated = []
        for column, fresh in enumerate(values):
            corrections = tuple(grouped.get((row, column), ()))
            # 校正区域可能被新线切开，所有格子都排除已被校正替代的原文，避免拆分后重复出现。
            remaining = unreplaced_tokens(fresh.tokens, all_corrections)
            lines = visual_rows((*remaining, *(part for part in corrections if part.text)))
            text = join_lines(" ".join(part.text for part in line) for line in lines)
            status = (CORRECTED if any(part.status == CORRECTED for part in corrections) else CONFIRMED) if corrections else ""
            if TableCell(tokens=remaining).needs_review:
                status = PENDING
            updated.append(replace(fresh, text=text, status=status, corrections=corrections))
        cells.append(tuple(updated))
    return TableDocument(recognized.grid, tuple(cells))


class TableReview:
    """网格与校对的唯一状态源，带撤销/重做。

    拖线时 set_grid(final=False) 只更新预览，始终从拖动前的状态重新分格，
    划过其他格子的中间位置不会把人工结果挤丢。
    """

    def __init__(self, tokens, grid):
        self._tokens = tuple(tokens)
        self.document = build_document(self._tokens, grid)
        self._base = None
        self._undo = []
        self._redo = []

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def set_grid(self, grid, *, final=True):
        if self._base is None:
            self._base = self.document
        self.document = carry_over(self._base, build_document(self._tokens, grid))
        if final:
            if self.document != self._base:
                self._record(self._base)
            self._base = None

    def edit(self, row, column, text):
        cell = self.document.cell(row, column)
        if text == cell.text:
            return
        self._record(self.document)
        # 改回原文等于撤销人工修改，格子回到识别时的状态。
        status = CORRECTED if text != cell.original_text else PENDING if cell.needs_review else ""
        self.document = self.document.with_cell(row, column, replace(cell, text=text, status=status, corrections=()))

    def confirm(self, row, column):
        cell = self.document.cell(row, column)
        if cell.status != PENDING:
            return False
        self._record(self.document)
        # 确认不是整格重写：保留原校正的位置，并分别记住剩余文字已确认。
        existing = tuple(part for values in self.document.cells for value in values for part in value.corrections)
        remaining = unreplaced_tokens(cell.tokens, existing)
        corrections = cell.corrections + tuple(CellCorrection(token.box, token.text.strip(), CONFIRMED) for token in remaining)
        self.document = self.document.with_cell(row, column, replace(cell, status=CONFIRMED, corrections=corrections))
        return True

    def pending_cells(self):
        return [(row, column) for row, values in enumerate(self.document.cells)
                for column, cell in enumerate(values) if cell.status == PENDING]

    def undo(self):
        self._redo.append(self.document)
        self.document = self._undo.pop()

    def redo(self):
        self._undo.append(self.document)
        self.document = self._redo.pop()

    def _record(self, document):
        self._undo.append(document)
        self._redo.clear()
