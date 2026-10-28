"""按网格把 OCR 文字分进格子，并保护人工校对结果。

分格规则移植自 v1 domain/layout.py；"重新分格不能覆盖人工结果"移植自 v1 domain/table_review.py。
v1 只允许同形网格重跑，这里网格随拖线实时变化，所以按人工格的中心点找回新位置。
"""

from dataclasses import replace
from statistics import median

from .model import CONFIRMED, CORRECTED, PENDING, TableCell, TableDocument


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


def build_document(tokens, grid):
    """文字按中心点落格，落进合并区域的归锚点格。"""
    buckets = [[[] for _ in range(grid.column_count)] for _ in range(grid.row_count)]
    for token in tokens:
        row, column = grid.anchor(*grid.cell_at(token.box.center_x, token.box.center_y))
        buckets[row][column].append(token)
    cells = []
    for row_tokens in buckets:
        row = []
        for cell_tokens in row_tokens:
            lines = visual_rows(tuple(cell_tokens))
            text = "\n".join(" ".join(token.text.strip() for token in line) for line in lines)
            cell = TableCell(text, text, tuple(token for line in lines for token in line))
            row.append(replace(cell, status=PENDING) if cell.low_confidence else cell)
        cells.append(tuple(row))
    return TableDocument(grid, tuple(cells))


def carry_over(base, recognized):
    """人工确认或改过的格子保留原文字；中心点落到同一新格时先到先得，其余靠撤销找回。"""
    claimed = set()
    document = recognized
    for row, values in enumerate(base.cells):
        for column, cell in enumerate(values):
            if not cell.human or base.grid.is_covered(row, column):
                continue
            rect = base.grid.cell_rect(row, column)
            target = recognized.grid.anchor(*recognized.grid.cell_at(rect.center_x, rect.center_y))
            if target in claimed:
                continue
            claimed.add(target)
            fresh = recognized.cell(*target)
            document = document.with_cell(*target, replace(fresh, text=cell.text, status=cell.status))
    return document


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
        status = CORRECTED if text != cell.original_text else PENDING if cell.low_confidence else ""
        self.document = self.document.with_cell(row, column, replace(cell, text=text, status=status))

    def confirm(self, row, column):
        cell = self.document.cell(row, column)
        if cell.status != PENDING:
            return False
        self._record(self.document)
        self.document = self.document.with_cell(row, column, replace(cell, status=CONFIRMED))
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
