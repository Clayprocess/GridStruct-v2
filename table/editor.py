"""原图上的可编辑网格：拖线、加线、删线、框选格子合并。

交互对照 v1 ui/table_grid_editor.py：Shift+单击加横线、Ctrl+单击加竖线、Alt+单击删线，
方向键选格（Shift 扩选），Ctrl+方向键选线后用方向键按 1 像素微调，Delete 删线。
网格始终用表格框内的物理像素；画布显示整页（缩放后可自由看别处），框外压暗，
页内偏移和显示缩放只在绘制和鼠标换算时出现一次。
"""

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from styles import tokens as T
from ui import tinted_icon
from .model import CellMerge, GridSpec, MIN_CELL_SIZE
from .regions import MIN_REGION

SELECT, ADD_HORIZONTAL, ADD_VERTICAL, DELETE = "select", "add_h", "add_v", "delete"
LINE_HIT_DISTANCE = 6
MIN_CELL = MIN_CELL_SIZE
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
    toolFinished = Signal()
    boundsChanged = Signal(QRect)

    def __init__(self, image, bounds=None, parent=None):
        """image 为整页；bounds 为这张表在页里的位置，省略即整页一张表。"""
        super().__init__(parent)
        self._pixmap = QPixmap.fromImage(image)
        self._pixmap.setDevicePixelRatio(1)
        self._scale = 1.0
        self.bounds = QRect(bounds) if bounds is not None else QRect(0, 0, image.width(), image.height())
        self.grid = GridSpec((0, self.bounds.height()), (0, self.bounds.width()))
        self.tool = SELECT
        self._selected = set()
        self._anchor = None
        self._cursor_cell = None
        self._dragging = None
        self._hovered = None
        self._preview = None
        self._pointer = None
        self._keyboard_line = None
        self._bounds_drag = None
        self._bounds_preview = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("表格网格")

    # ---- 对外接口 ----

    @property
    def scale(self):
        return self._scale

    def set_scale(self, scale):
        self._scale = scale
        self.setFixedSize(self.sizeHint())
        self.update()

    def fit_scale(self, size):
        return min(size.width() / self._pixmap.width(), size.height() / self._pixmap.height())

    def pans_at(self, point):
        """表格框外是空白处，左键拖动看整页。"""
        x, y = self._to_grid(point)
        return (self._corner_at(x, y) is None and self._edge_add_at(x, y) is None
                and not self.bounds.contains(round(point.x() / self._scale), round(point.y() / self._scale)))

    def sizeHint(self):
        return QSize(round(self._pixmap.width() * self._scale), round(self._pixmap.height() * self._scale))

    def set_grid(self, grid):
        """外部替换网格（换方案、撤销）时清掉选区，避免指向已不存在的格子。"""
        self.grid = grid
        self._selected.clear()
        self._anchor = self._cursor_cell = None
        self._dragging = self._hovered = self._keyboard_line = None
        self._preview = self._pointer = None
        self._cancel_bounds_drag()
        self.selectionChanged.emit()
        self.update()

    def set_tool(self, tool):
        self._cancel_bounds_drag()
        self.tool = tool
        if self._dragging is not None:
            # 拖线时按快捷键换工具：这次拖动按松开提交，不留半截编辑。
            self._dragging = None
            self.gridChanged.emit(self.grid, True)
        self._set_hovered(None)
        self._preview = None
        self._pointer = None
        self._tool_cursor()
        self.update()

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
        page = QRectF(self._pixmap.rect())
        display_bounds = self._bounds_preview if self._bounds_preview is not None else self.bounds
        if QRectF(display_bounds) != page:
            outside = QPainterPath()
            outside.addRect(page)
            inner = QPainterPath()
            inner.addRect(QRectF(display_bounds))
            painter.fillPath(outside.subtracted(inner), QColor(*T.TABLE_OUTSIDE))
        painter.translate(self.bounds.topLeft())
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
        painter.drawRect(QRectF(0, 0, self.bounds.width(), self.bounds.height()).adjusted(inset, inset, -inset, -inset))
        delete_target = self._delete_target()
        if delete_target is not None:
            self._draw_delete(painter, delete_target)
        self._draw_preview(painter)
        self._draw_bounds_handles(painter)
        active = self._dragging or self._keyboard_line or (self._hovered if self.tool == SELECT and self._preview is None else None)
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

    def _draw_bounds_handles(self, painter):
        if self.tool != SELECT:
            return
        box = QRectF(self._bounds_preview if self._bounds_preview is not None else self.bounds)
        box.translate(-self.bounds.x(), -self.bounds.y())
        pen = QPen(_color("frame"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if self._bounds_preview is not None:
            painter.drawRect(box)
        painter.setBrush(QColor(T.TABLE_HANDLE))
        radius = 3 / self._scale
        for point in (box.topLeft(), box.topRight(), box.bottomLeft(), box.bottomRight()):
            painter.drawRect(QRectF(point.x() - radius, point.y() - radius, radius * 2, radius * 2))

    def _corner_at(self, x, y):
        if self.tool != SELECT:
            return None
        slop = 7 / self._scale
        for name, px, py in (("lt", 0, 0), ("rt", self.bounds.width(), 0),
                             ("lb", 0, self.bounds.height()), ("rb", self.bounds.width(), self.bounds.height())):
            if abs(x - px) <= slop and abs(y - py) <= slop:
                return name
        return None

    def _preview_bounds(self, point):
        edge, rect = self._bounds_drag
        x = max(0, min(self._pixmap.width(), round(point.x() / self._scale)))
        y = max(0, min(self._pixmap.height(), round(point.y() / self._scale)))
        left, top, right, bottom = rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()
        if "l" in edge:
            left = min(x, right - min(MIN_REGION, rect.width()))
        if "r" in edge:
            right = max(x, left + min(MIN_REGION, rect.width()))
        if "t" in edge:
            top = min(y, bottom - min(MIN_REGION, rect.height()))
        if "b" in edge:
            bottom = max(y, top + min(MIN_REGION, rect.height()))
        self._bounds_preview = QRect(left, top, right - left, bottom - top)
        self.update()

    def _cancel_bounds_drag(self):
        # 外框会触发重新分析，未松手前保持旧网格坐标，Esc/失焦/换工具只撤掉预览。
        self._bounds_drag = None
        self._bounds_preview = None
        self.update()

    def _tool_cursor(self):
        if self.tool not in (ADD_HORIZONTAL, ADD_VERTICAL, DELETE):
            self.setCursor(Qt.ArrowCursor)
            return
        # 透明细线，不携带特定办公软件的专有位图资源。
        pixmap = QPixmap(40, 40)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)
        with QPainter(pixmap) as painter:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor(T.LIGHT["foreground"]), 1))
            path = QPainterPath()
            if self.tool == DELETE:
                path.moveTo(3, 11)
                path.lineTo(10, 4)
                path.lineTo(15, 9)
                path.lineTo(8, 16)
                path.lineTo(3, 11)
                path.moveTo(6, 8)
                path.lineTo(11, 13)
            else:
                path.moveTo(3, 16)
                path.lineTo(4, 12)
                path.lineTo(12, 4)
                path.lineTo(15, 7)
                path.lineTo(7, 15)
                path.closeSubpath()
                path.moveTo(10, 6)
                path.lineTo(13, 9)
            painter.drawPath(path)
        self.setCursor(QCursor(pixmap, 3, 16 if self.tool != DELETE else 11))

    def _delete_target(self):
        if self._preview is not None and self._preview[0] == "delete":
            return self._preview[1]
        return self._hovered if self.tool == DELETE else None

    def _draw_delete(self, painter, target):
        pen = QPen(QColor(T.LIGHT["destructive"]), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for start, end in self._segments(*target):
            length = (end - start).manhattanLength() * self._scale
            path = QPainterPath(start)
            distance = 2.0
            step = 0
            while distance < length:
                delta = distance / self._scale
                offset = (2 if step % 2 == 0 else -2) / self._scale
                point = start + (QPointF(delta, offset) if target[0] == "h" else QPointF(offset, delta))
                path.lineTo(point)
                distance += 4
                step += 1
            path.lineTo(end)
            painter.drawPath(path)

    def _edge_add_at(self, x, y):
        if self.tool != SELECT or self._corner_at(x, y) is not None:
            return None
        slop = 10 / self._scale
        if abs(x) <= slop and MIN_CELL <= y <= self.bounds.height() - MIN_CELL:
            return "h", round(y)
        if abs(y) <= slop and MIN_CELL <= x <= self.bounds.width() - MIN_CELL:
            return "v", round(x)
        return None

    def _can_add(self, orientation, position):
        lines = self.grid.lines(orientation)
        return lines[0] + MIN_CELL <= position <= lines[-1] - MIN_CELL and all(
            abs(existing - position) >= MIN_CELL for existing in lines)

    def _draw_preview(self, painter):
        preview = self._preview
        edge = None
        if preview is None and self._pointer is not None:
            x, y = self._pointer
            edge = self._edge_add_at(x, y)
            if edge is not None:
                preview = edge
            elif self._inside(x, y) and self.tool in (ADD_HORIZONTAL, ADD_VERTICAL):
                preview = ("h", round(y)) if self.tool == ADD_HORIZONTAL else ("v", round(x))
        if preview is None or preview[0] not in ("h", "v"):
            return
        orientation, position = preview
        color = _color("active")
        if not self._can_add(orientation, position):
            color.setAlpha(70)
        pen = QPen(color, 1)
        pen.setCosmetic(True)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        start, end = ((QPointF(0, position), QPointF(self.bounds.width(), position)) if orientation == "h"
                      else (QPointF(position, 0), QPointF(position, self.bounds.height())))
        painter.drawLine(start, end)
        if edge is not None:
            pen.setStyle(Qt.SolidLine)
            painter.setPen(pen)
            painter.setBrush(QColor(T.TABLE_HANDLE))
            radius = 7 / self._scale
            painter.drawEllipse(start, radius, radius)
            r = 3 / self._scale
            painter.drawLine(start - QPointF(r, 0), start + QPointF(r, 0))
            painter.drawLine(start - QPointF(0, r), start + QPointF(0, r))

    def _draw_line(self, painter, orientation, index):
        segments = self._segments(orientation, index)
        for start, end in segments:
            painter.drawLine(start, end)
        return segments

    def _segments(self, orientation, index):
        """线被合并区域挡住的部分不画、也不能点中。"""
        grid = self.grid
        if orientation == "h":
            y, extent = grid.horizontal_lines[index], self.bounds.width()
            blocked = sorted((grid.vertical_lines[m.left], grid.vertical_lines[m.right])
                             for m in grid.merges if m.top < index < m.bottom)
        else:
            x, extent = grid.vertical_lines[index], self.bounds.height()
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
        return self._to_grid(event.position())

    def _to_grid(self, point):
        return point.x() / self._scale - self.bounds.x(), point.y() / self._scale - self.bounds.y()

    def _inside(self, x, y):
        return 0 <= x < self.bounds.width() and 0 <= y < self.bounds.height()

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
        if not self._inside(x, y):
            return None
        return self.grid.cell_at(x, y)

    def mousePressEvent(self, event):
        x, y = self._point(event)
        corner = self._corner_at(x, y)
        if event.button() == Qt.LeftButton and corner is not None:
            self._bounds_drag = (corner, QRect(self.bounds))
            self._bounds_preview = QRect(self.bounds)
            self._anchor = None
            self._pointer = self._hovered = self._keyboard_line = None
            self.setCursor(Qt.SizeFDiagCursor if corner in ("lt", "rb") else Qt.SizeBDiagCursor)
            self.update()
            return
        edge = self._edge_add_at(x, y)
        if event.button() == Qt.LeftButton and edge is not None:
            self._add_line(*edge)
            return
        # 框外点击不加线、不选格：那里是整页里别的内容，左键留给拖动看图（ui.zoom）。
        if event.button() != Qt.LeftButton or not self._inside(x, y):
            return
        modifiers = event.modifiers()
        if modifiers & Qt.AltModifier or self.tool == DELETE:
            line = self._line_at(x, y)
            if line is not None:
                self._remove_line(*line)
                if self.tool == DELETE:
                    self.set_tool(SELECT)
                    self.toolFinished.emit()
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
        if self._bounds_drag is not None:
            self._preview_bounds(event.position())
            return
        self._pointer = (x, y)
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
        line = self._line_at(x, y) if self._inside(x, y) and self.tool in (SELECT, DELETE) else None
        self._set_hovered(line)
        self.update()
        if self.tool in (ADD_HORIZONTAL, ADD_VERTICAL, DELETE):
            return
        corner = self._corner_at(x, y)
        if corner is not None:
            self.setCursor(Qt.SizeFDiagCursor if corner in ("lt", "rb") else Qt.SizeBDiagCursor)
        elif self._edge_add_at(x, y) is not None:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor if line is None else Qt.SizeVerCursor if line[0] == "h" else Qt.SizeHorCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._bounds_drag is not None:
            self._preview_bounds(event.position())
            changed = QRect(self._bounds_preview)
            self._cancel_bounds_drag()
            if changed != self.bounds:
                self.boundsChanged.emit(changed)
            return
        if event.button() == Qt.LeftButton and self._dragging is not None:
            self._dragging = None
            self.gridChanged.emit(self.grid, True)
            self.update()

    def focusOutEvent(self, event):
        self._cancel_bounds_drag()
        super().focusOutEvent(event)

    def leaveEvent(self, event):
        self._pointer = None
        self._set_hovered(None)
        self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        x, y = self._to_grid(QPointF(event.pos()))
        if not self._inside(x, y):
            return
        line = self._line_at(x, y)
        cell = self._cell_at(x, y)
        if cell is not None and self.grid.anchor(*cell) not in self._selected:
            self.select_cell(*cell)
        menu = QMenu(self)
        mode = QApplication.instance().property("theme_mode") or "light"
        color = T.THEMES[mode]["foreground"]
        previews = {}
        for orientation, position, name, label in (("h", round(y), "table-rows-split", "在此添加横线"),
                                                   ("v", round(x), "table-columns-split", "在此添加竖线")):
            action = menu.addAction(tinted_icon(name, color), label)
            action.triggered.connect(lambda _checked=False, axis=orientation, value=position: self._add_line(axis, value))
            action.setEnabled(self._can_add(orientation, position))
            previews[action] = (orientation, position)
        if line is not None:
            action = menu.addAction(tinted_icon("eraser", color), "删除这条线", lambda: self._remove_line(*line))
            previews[action] = ("delete", line)
        menu.addSeparator()
        menu.addAction(tinted_icon("table-cells-merge", color), "合并选中格子", self.merge_selected).setEnabled(self.can_merge)
        menu.addAction(tinted_icon("table-cells-split", color), "取消合并", self.split_selected).setEnabled(self.can_split)
        self._pointer = None
        self._set_hovered(None)
        menu.hovered.connect(lambda action: self._menu_preview(previews.get(action)))
        menu.aboutToHide.connect(lambda: self._menu_preview(None))
        menu.exec(event.globalPos())
        self._menu_preview(None)
        menu.deleteLater()

    def _menu_preview(self, preview):
        self._preview = preview
        self.update()

    # ---- 键盘 ----

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key_Escape and self._bounds_drag is not None:
            self._cancel_bounds_drag()
            self._tool_cursor()
        elif key == Qt.Key_Escape and self.tool != SELECT:
            self.set_tool(SELECT)
            self.toolFinished.emit()
        elif key == Qt.Key_Escape and (self._selected or self._keyboard_line):
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
        if not self._can_add(orientation, position):
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
