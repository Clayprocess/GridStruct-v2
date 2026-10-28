"""原图上的可编辑网格：拖线、加线、删线、框选格子合并。

交互对照 v1 ui/table_grid_editor.py：Shift+单击加横线、Ctrl+单击加竖线、Alt+单击删线，
方向键选格（Shift 扩选），Ctrl+方向键选线后用方向键按 1 像素微调，Delete 删线。
网格始终用原图物理像素；显示缩放只在绘制和鼠标换算时出现一次。
"""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

from styles import tokens as T
from .model import CellMerge, GridSpec

SELECT, ADD_HORIZONTAL, ADD_VERTICAL, DELETE = "select", "add_h", "add_v", "delete"
LINE_HIT_DISTANCE = 6
MIN_CELL = 3
MAX_SCALE = 2.0
ARROWS = (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down)


def _color(name):
    color = QColor(T.TABLE_GRID)
    color.setAlpha(T.TABLE_GRID_ALPHA[name])
    return color


class GridCanvas(QWidget):
    """gridChanged(grid, final)：拖动中 final=False 只用于实时预览，松手时再记一次撤销。"""
    gridChanged = Signal(object, bool)
    cellSelected = Signal(int, int)
    selectionChanged = Signal()

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap.fromImage(image)
        self._pixmap.setDevicePixelRatio(1)
        self._scale = 1.0
        self.grid = GridSpec((0, image.height()), (0, image.width()))
        self.tool = SELECT
        self._selected = set()
        self._anchor = None
        self._cursor_cell = None
        self._dragging = None
        self._hovered = None
        self._keyboard_line = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("表格网格")

    # ---- 对外接口 ----

    def set_scale(self, scale):
        self._scale = min(MAX_SCALE, scale)
        self.setFixedSize(self.sizeHint())
        self.update()

    def fit_scale(self, size):
        return min(size.width() / self._pixmap.width(), size.height() / self._pixmap.height())

    def sizeHint(self):
        return QSize(round(self._pixmap.width() * self._scale), round(self._pixmap.height() * self._scale))

    def set_grid(self, grid):
        """外部替换网格（换方案、撤销）时清掉选区，避免指向已不存在的格子。"""
        self.grid = grid
        self._selected.clear()
        self._anchor = self._cursor_cell = None
        self._dragging = self._hovered = self._keyboard_line = None
        self.selectionChanged.emit()
        self.update()

    def set_tool(self, tool):
        self.tool = tool
        self._dragging = None
        self._set_hovered(None)
        self.setCursor(Qt.CrossCursor if tool in (ADD_HORIZONTAL, ADD_VERTICAL) else Qt.ArrowCursor)

    def select_cell(self, row, column):
        self._selected = {self.grid.anchor(row, column)}
        self._cursor_cell = self._anchor = (row, column)
        self._keyboard_line = None
        self.selectionChanged.emit()
        self.update()

    @property
    def can_merge(self):
        return len({self.grid.anchor(*cell) for cell in self._selected}) > 1

    @property
    def can_split(self):
        return any(self.grid.merge_at(*cell) is not None for cell in self._selected)

    def merge_selected(self):
        cells = [cell for anchor in self._selected for cell in self._merge_cells(anchor)]
        rows, columns = [cell[0] for cell in cells], [cell[1] for cell in cells]
        merge = CellMerge(min(rows), min(columns), max(rows) + 1, max(columns) + 1)
        self._commit(self.grid.with_merge(merge), {(merge.top, merge.left)})

    def split_selected(self):
        self._commit(self.grid.without_merges(self._selected))

    # ---- 绘制 ----

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.scale(self._scale, self._scale)
        painter.drawPixmap(0, 0, self._pixmap)
        for merge in self.grid.merges:
            painter.fillRect(self._rect(merge.top, merge.left), _color("merge"))
        for cell in self._selected:
            painter.fillRect(self._rect(*cell), _color("selection"))
        pen = QPen(_color("line"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for orientation in ("h", "v"):
            for index in range(1, len(self.grid.lines(orientation)) - 1):
                self._draw_line(painter, orientation, index)
        pen = QPen(_color("frame"), 1.5)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for cell in self._selected:
            painter.drawRect(self._rect(*cell))
        inset = 0.75 / self._scale
        painter.drawRect(QRectF(0, 0, self._pixmap.width(), self._pixmap.height()).adjusted(inset, inset, -inset, -inset))
        active = self._dragging or self._keyboard_line or self._hovered
        if active is not None:
            pen = QPen(_color("active"), 2.5)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            segments = self._draw_line(painter, *active)
            if not segments:
                return
            # 端点手柄提示这条线可以拖动。
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(QColor(T.TABLE_HANDLE))
            radius = 3.5 / self._scale
            for point in (segments[0][0], segments[-1][1]):
                painter.drawEllipse(point, radius, radius)

    def _draw_line(self, painter, orientation, index):
        segments = self._segments(orientation, index)
        for start, end in segments:
            painter.drawLine(start, end)
        return segments

    def _segments(self, orientation, index):
        """线被合并区域挡住的部分不画、也不能点中。"""
        grid = self.grid
        if orientation == "h":
            y, extent = grid.horizontal_lines[index], self._pixmap.width()
            blocked = sorted((grid.vertical_lines[m.left], grid.vertical_lines[m.right])
                             for m in grid.merges if m.top < index < m.bottom)
        else:
            x, extent = grid.vertical_lines[index], self._pixmap.height()
            blocked = sorted((grid.horizontal_lines[m.top], grid.horizontal_lines[m.bottom])
                             for m in grid.merges if m.left < index < m.right)
        spans, cursor = [], 0
        for start, end in blocked:
            if start > cursor:
                spans.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < extent:
            spans.append((cursor, extent))
        if orientation == "h":
            return [(QPointF(a, y), QPointF(b, y)) for a, b in spans]
        return [(QPointF(x, a), QPointF(x, b)) for a, b in spans]

    def _rect(self, row, column):
        rect = self.grid.cell_rect(row, column)
        return QRectF(rect.left, rect.top, rect.width, rect.height)

    # ---- 鼠标 ----

    def _point(self, event):
        return event.position().x() / self._scale, event.position().y() / self._scale

    def _line_at(self, x, y):
        best = None
        for orientation, position, along in (("h", y, x), ("v", x, y)):
            lines = self.grid.lines(orientation)
            for index in range(1, len(lines) - 1):
                visible = any((a.x() if orientation == "h" else a.y()) <= along <= (b.x() if orientation == "h" else b.y())
                              for a, b in self._segments(orientation, index))
                distance = abs(position - lines[index])
                if visible and (best is None or distance < best[0]):
                    best = (distance, orientation, index)
        if best is not None and best[0] <= LINE_HIT_DISTANCE / self._scale:
            return best[1], best[2]
        return None

    def _cell_at(self, x, y):
        if not (0 <= x < self._pixmap.width() and 0 <= y < self._pixmap.height()):
            return None
        return self.grid.cell_at(x, y)

    def mousePressEvent(self, event):
        x, y = self._point(event)
        if event.button() != Qt.LeftButton:
            return
        modifiers = event.modifiers()
        if modifiers & Qt.AltModifier or self.tool == DELETE:
            line = self._line_at(x, y)
            if line is not None:
                self._remove_line(*line)
            return
        if modifiers & Qt.ShiftModifier or self.tool == ADD_HORIZONTAL:
            self._add_line("h", round(y))
            return
        if modifiers & Qt.ControlModifier or self.tool == ADD_VERTICAL:
            self._add_line("v", round(x))
            return
        line = self._line_at(x, y)
        if line is not None:
            self._dragging = line
            self._keyboard_line = None
            self.setCursor(Qt.SizeVerCursor if line[0] == "h" else Qt.SizeHorCursor)
            self.update()
            return
        cell = self._cell_at(x, y)
        if cell is not None:
            self._anchor = cell
            self._keyboard_line = None
            self._select_range(cell, cell)

    def mouseMoveEvent(self, event):
        x, y = self._point(event)
        if self._dragging is not None:
            orientation, index = self._dragging
            lines = self.grid.lines(orientation)
            value = round(y if orientation == "h" else x)
            value = max(lines[index - 1] + MIN_CELL, min(value, lines[index + 1] - MIN_CELL))
            if value != lines[index]:
                self.grid = self.grid.with_moved_line(orientation, index, value)
                self.gridChanged.emit(self.grid, False)
                self.update()
            return
        if event.buttons() & Qt.LeftButton and self._anchor is not None:
            cell = self._cell_at(x, y)
            if cell is not None:
                self._select_range(self._anchor, cell)
            return
        line = self._line_at(x, y) if self.tool in (SELECT, DELETE) else None
        self._set_hovered(line)
        if self.tool in (ADD_HORIZONTAL, ADD_VERTICAL):
            return
        self.setCursor(Qt.ArrowCursor if line is None else Qt.SizeVerCursor if line[0] == "h" else Qt.SizeHorCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging is not None:
            self._dragging = None
            self.gridChanged.emit(self.grid, True)
            self.update()

    def leaveEvent(self, event):
        self._set_hovered(None)
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        x, y = event.pos().x() / self._scale, event.pos().y() / self._scale
        line = self._line_at(x, y)
        cell = self._cell_at(x, y)
        if cell is not None and self.grid.anchor(*cell) not in self._selected:
            self.select_cell(*cell)
        menu = QMenu(self)
        menu.addAction("在此添加横线", lambda: self._add_line("h", round(y)))
        menu.addAction("在此添加竖线", lambda: self._add_line("v", round(x)))
        if line is not None:
            menu.addAction("删除这条线", lambda: self._remove_line(*line))
        menu.addSeparator()
        menu.addAction("合并选中格子", self.merge_selected).setEnabled(self.can_merge)
        menu.addAction("取消合并", self.split_selected).setEnabled(self.can_split)
        menu.exec(event.globalPos())
        menu.deleteLater()

    # ---- 键盘 ----

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key_Escape and (self._selected or self._keyboard_line):
            self._selected.clear()
            self._keyboard_line = None
            self.selectionChanged.emit()
            self.update()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace) and self._keyboard_line is not None:
            self._remove_line(*self._keyboard_line)
        elif key in ARROWS and modifiers & Qt.ControlModifier:
            self._select_line(key)
        elif key in ARROWS and self._keyboard_line is not None:
            self._nudge_line(key)
        elif key in ARROWS:
            self._move_cell(key, bool(modifiers & Qt.ShiftModifier))
        else:
            super().keyPressEvent(event)

    def _select_line(self, key):
        orientation = "v" if key in (Qt.Key_Left, Qt.Key_Right) else "h"
        forward = key in (Qt.Key_Right, Qt.Key_Down)
        count = len(self.grid.lines(orientation)) - 2
        if count <= 0:
            return
        if self._keyboard_line is not None and self._keyboard_line[0] == orientation:
            index = max(1, min(count, self._keyboard_line[1] + (1 if forward else -1)))
        else:
            index = 1 if forward else count
        self._keyboard_line = (orientation, index)
        self.update()

    def _nudge_line(self, key):
        orientation, index = self._keyboard_line
        if (orientation == "v") != (key in (Qt.Key_Left, Qt.Key_Right)):
            return
        lines = self.grid.lines(orientation)
        delta = -1 if key in (Qt.Key_Left, Qt.Key_Up) else 1
        value = max(lines[index - 1] + MIN_CELL, min(lines[index] + delta, lines[index + 1] - MIN_CELL))
        if value != lines[index]:
            self._commit(self.grid.with_moved_line(orientation, index, value), keep_line=True)

    def _move_cell(self, key, extend):
        row, column = self._cursor_cell or (0, 0)
        if self._cursor_cell is not None:
            row += {Qt.Key_Up: -1, Qt.Key_Down: 1}.get(key, 0)
            column += {Qt.Key_Left: -1, Qt.Key_Right: 1}.get(key, 0)
        target = (max(0, min(self.grid.row_count - 1, row)), max(0, min(self.grid.column_count - 1, column)))
        if not extend or self._anchor is None:
            self._anchor = target
        self._select_range(self._anchor, target)

    # ---- 编辑 ----

    def _select_range(self, start, end):
        top, bottom = sorted((start[0], end[0]))
        left, right = sorted((start[1], end[1]))
        selected = {self.grid.anchor(row, column) for row in range(top, bottom + 1) for column in range(left, right + 1)}
        self._cursor_cell = end
        if selected != self._selected:
            self._selected = selected
            self.selectionChanged.emit()
            if len(selected) == 1:
                self.cellSelected.emit(*next(iter(selected)))
        self.update()

    def _merge_cells(self, anchor):
        merge = self.grid.merge_at(*anchor)
        if merge is None:
            return [anchor]
        return [(merge.top, merge.left), (merge.bottom - 1, merge.right - 1)]

    def _add_line(self, orientation, position):
        lines = self.grid.lines(orientation)
        if not lines[0] + MIN_CELL <= position <= lines[-1] - MIN_CELL:
            return
        if any(abs(existing - position) < MIN_CELL for existing in lines):
            return
        # 行列编号整体变了，旧选区不再指向同一格。
        self._commit(self.grid.with_line(orientation, position), set())

    def _remove_line(self, orientation, index):
        self._commit(self.grid.without_line(orientation, index), set())

    def _commit(self, grid, selected=None, keep_line=False):
        self.grid = grid
        self._selected = selected if selected is not None else {grid.anchor(*cell) for cell in self._selected}
        if not self._selected:
            self._anchor = self._cursor_cell = None
        self._hovered = None
        if not keep_line:
            self._keyboard_line = None
        self.selectionChanged.emit()
        self.gridChanged.emit(grid, True)
        self.update()

    def _set_hovered(self, line):
        if line != self._hovered:
            self._hovered = line
            self.update()
