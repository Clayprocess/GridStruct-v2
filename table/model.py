"""表格网格、单元格与文档：纯数据，坐标一律是原图物理像素。

移植自 v1 domain/models.py，只保留截图表格用到的部分。
"""

from bisect import bisect_right
from dataclasses import dataclass, replace
from itertools import pairwise

# 模型置信度只是复核提示，不代表真实正确率；阈值沿用 v1。
LOW_CONFIDENCE_WEIGHTED = 0.85
LOW_CONFIDENCE_MINIMUM = 0.70

PENDING = "pending"
CONFIRMED = "confirmed"
CORRECTED = "corrected"


@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    @property
    def center_x(self):
        return (self.left + self.right) / 2

    @property
    def center_y(self):
        return (self.top + self.bottom) / 2


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    score: float
    box: Rect


@dataclass(frozen=True, slots=True)
class CellMerge:
    """右、下边界不含；覆盖 [top, bottom) × [left, right)。"""
    top: int
    left: int
    bottom: int
    right: int

    def contains(self, row, column):
        return self.top <= row < self.bottom and self.left <= column < self.right

    def overlaps(self, other):
        return not (self.bottom <= other.top or other.bottom <= self.top
                    or self.right <= other.left or other.right <= self.left)


@dataclass(frozen=True, slots=True)
class GridSpec:
    horizontal_lines: tuple
    vertical_lines: tuple
    merges: tuple = ()

    def __post_init__(self):
        for lines in (self.horizontal_lines, self.vertical_lines):
            if len(lines) < 2 or lines[0] != 0 or any(a >= b for a, b in pairwise(lines)):
                raise ValueError("网格线必须从 0 开始严格递增")
        for index, merge in enumerate(self.merges):
            if (merge.bottom > self.row_count or merge.right > self.column_count
                    or merge.bottom - merge.top < 1 or merge.right - merge.left < 1):
                raise ValueError("合并单元格超出网格")
            if any(merge.overlaps(other) for other in self.merges[index + 1:]):
                raise ValueError("合并单元格不能重叠")

    @property
    def row_count(self):
        return len(self.horizontal_lines) - 1

    @property
    def column_count(self):
        return len(self.vertical_lines) - 1

    def merge_at(self, row, column):
        return next((merge for merge in self.merges if merge.contains(row, column)), None)

    def anchor(self, row, column):
        merge = self.merge_at(row, column)
        return (merge.top, merge.left) if merge is not None else (row, column)

    def is_covered(self, row, column):
        return self.anchor(row, column) != (row, column)

    def cell_at(self, x, y):
        """原图坐标所在的格子（未折算合并）；框外取最近的边缘格。"""
        row = max(0, min(self.row_count - 1, bisect_right(self.horizontal_lines, y) - 1))
        column = max(0, min(self.column_count - 1, bisect_right(self.vertical_lines, x) - 1))
        return row, column

    def cell_rect(self, row, column):
        """锚点格返回整个合并区域。"""
        merge = self.merge_at(row, column)
        top, left, bottom, right = ((merge.top, merge.left, merge.bottom, merge.right) if merge is not None
                                    else (row, column, row + 1, column + 1))
        return Rect(self.vertical_lines[left], self.horizontal_lines[top],
                    self.vertical_lines[right], self.horizontal_lines[bottom])

    def lines(self, orientation):
        return self.horizontal_lines if orientation == "h" else self.vertical_lines

    def _with_lines(self, orientation, lines, merges):
        # 删线后两侧合并区域可能并到一起，后出现的重叠合并让位给先出现的。
        kept = []
        for merge in merges:
            if (merge.bottom - merge.top) * (merge.right - merge.left) > 1 and not any(merge.overlaps(m) for m in kept):
                kept.append(merge)
        if orientation == "h":
            return GridSpec(tuple(lines), self.vertical_lines, tuple(kept))
        return GridSpec(self.horizontal_lines, tuple(lines), tuple(kept))

    def with_line(self, orientation, position):
        """插入一条线；跨过它的合并区域随之多占一行/列。"""
        lines = self.lines(orientation)
        index = bisect_right(lines, position)
        shift = lambda value: value + (value >= index)
        merges = [CellMerge(shift(m.top), m.left, shift(m.bottom), m.right) if orientation == "h"
                  else CellMerge(m.top, shift(m.left), m.bottom, shift(m.right)) for m in self.merges]
        return self._with_lines(orientation, (*lines[:index], position, *lines[index:]), merges)

    def without_line(self, orientation, index):
        """删掉第 index 条内部线，两侧的行/列并成一个。"""
        lines = self.lines(orientation)
        start = lambda value: value - (value >= index)
        end = lambda value: value - (value > index)
        merges = [CellMerge(start(m.top), m.left, end(m.bottom), m.right) if orientation == "h"
                  else CellMerge(m.top, start(m.left), m.bottom, end(m.right)) for m in self.merges]
        return self._with_lines(orientation, (*lines[:index], *lines[index + 1:]), merges)

    def with_moved_line(self, orientation, index, position):
        lines = list(self.lines(orientation))
        lines[index] = position
        return self._with_lines(orientation, lines, self.merges)

    def with_merge(self, merge):
        """新合并吞掉与它相交的旧合并。"""
        return GridSpec(self.horizontal_lines, self.vertical_lines,
                        (*(m for m in self.merges if not m.overlaps(merge)), merge))

    def without_merges(self, cells):
        return GridSpec(self.horizontal_lines, self.vertical_lines,
                        tuple(m for m in self.merges if not any(m.contains(*cell) for cell in cells)))


@dataclass(frozen=True, slots=True)
class TableCell:
    """status：空串为普通格；pending 待复核；confirmed 人工确认；corrected 人工改字。"""
    text: str = ""
    original_text: str = ""
    tokens: tuple = ()
    status: str = ""

    @property
    def weighted_confidence(self):
        weights = [(token.score, len(token.text.strip())) for token in self.tokens]
        total = sum(count for _, count in weights)
        return sum(score * count for score, count in weights) / total if total else None

    @property
    def minimum_confidence(self):
        return min((token.score for token in self.tokens), default=None)

    @property
    def low_confidence(self):
        weighted, minimum = self.weighted_confidence, self.minimum_confidence
        return ((weighted is not None and weighted < LOW_CONFIDENCE_WEIGHTED)
                or (minimum is not None and minimum < LOW_CONFIDENCE_MINIMUM))

    @property
    def human(self):
        return self.status in (CONFIRMED, CORRECTED)


@dataclass(frozen=True, slots=True)
class TableDocument:
    """cells 与网格同形；被合并覆盖的格子恒为空 TableCell。"""
    grid: GridSpec
    cells: tuple

    @property
    def rows(self):
        return tuple(tuple(cell.text for cell in row) for row in self.cells)

    @property
    def pending_count(self):
        return sum(cell.status == PENDING for row in self.cells for cell in row)

    def cell(self, row, column):
        return self.cells[row][column]

    def with_cell(self, row, column, cell):
        cells = [list(values) for values in self.cells]
        cells[row][column] = cell
        return replace(self, cells=tuple(tuple(values) for values in cells))
