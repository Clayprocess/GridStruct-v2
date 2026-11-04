"""表格框选：在原图（截图、贴图或文档的一页）上调整、删除、新画表格框。

自动检测只认有线表，无线表、三线表或漏检的表由用户在这里补框；框外的正文不进表格。
坐标始终用整页物理像素，显示缩放只在绘制和鼠标换算时出现一次（同 editor.GridCanvas）。
"""

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

from styles import tokens as T
from .analysis import reading_order

MIN_REGION = 16          # 新画的框两边都短于它视为误点，不留
HANDLE_HIT = 7           # 角和边的可拖范围（屏幕像素）
HANDLE_RADIUS = 4
_EDGES = {"l": (1, 0, 0, 0), "t": (0, 1, 0, 0), "r": (0, 0, 1, 0), "b": (0, 0, 0, 1)}
_CURSORS = {"lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor, "rt": Qt.SizeBDiagCursor, "lb": Qt.SizeBDiagCursor,
            "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor}


def _color(name):
    color = QColor(T.TABLE_GRID)
    color.setAlpha(T.TABLE_GRID_ALPHA[name])
    return color


def region_label(index):
    return f"表{index + 1}"


class RegionCanvas(QWidget):
    """regionsChanged()：增删、移动或改大小松手后发出；regions 为按阅读顺序编号前的当前框。"""
    regionsChanged = Signal()

    def __init__(self, image, regions, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap.fromImage(image)
        self._pixmap.setDevicePixelRatio(1)
        self._bounds = QRect(0, 0, image.width(), image.height())
        self.scale = 1.0
        self.regions = [QRect(rect) for rect in regions]
        self.selected = 0 if self.regions else None
        self._drag = None  # (方式, 起点, 原框)；方式为 "new"、"move" 或边角代号
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName("表格框选")

    # ---- 对外接口 ----

    def set_scale(self, scale):
        self.scale = scale
        self.setFixedSize(self.sizeHint())
        self.update()

    def fit_scale(self, size):
        return min(size.width() / self._pixmap.width(), size.height() / self._pixmap.height())

    @staticmethod
    def pans_at(_point):
        # 空白处拖动是画新框，拖图只能按住空格或中键。
        return False

    def sizeHint(self):
        return QSize(round(self._pixmap.width() * self.scale), round(self._pixmap.height() * self.scale))

    def remove_selected(self):
        if self.selected is None:
            return
        del self.regions[self.selected]
        self.selected = min(self.selected, len(self.regions) - 1) if self.regions else None
        self.regionsChanged.emit()
        self.update()

    # ---- 绘制 ----

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.scale(self.scale, self.scale)
        painter.drawPixmap(0, 0, self._pixmap)
        regions = list(self.regions)
        if self._drag is not None and self._drag[0] == "new":
            regions.append(self._drag[2])
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setBold(True)
        # 编号与导出时的工作表名一致：按阅读顺序，不按画框先后。
        order = reading_order(self.regions)
        numbers = {id(rect): number for number, rect in enumerate(order)}
        for index, rect in enumerate(regions):
            box = QRectF(rect)
            active = index == self.selected or index == len(self.regions)
            painter.fillRect(box, _color("selection" if active else "merge"))
            pen = QPen(_color("frame" if active else "line"), 2 if active else 1.5)
            pen.setCosmetic(True)
            if index == len(self.regions):
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(box)
            if index < len(self.regions):
                self._draw_label(painter, box, region_label(numbers[id(rect)]), font)
        if self.selected is not None and (self._drag is None or self._drag[0] != "new"):
            self._draw_handles(painter, QRectF(self.regions[self.selected]))

    def _draw_label(self, painter, box, text, font):
        # 标签按屏幕像素绘制，缩放后仍清晰可读。
        painter.save()
        painter.resetTransform()
        corner = QPointF(box.left() * self.scale, box.top() * self.scale)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text) + 10
        tag = QRectF(corner.x(), max(0.0, corner.y() - 20), width, 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_color("frame"))
        painter.drawRoundedRect(tag, 4, 4)
        painter.setPen(QColor(T.TABLE_HANDLE))
        painter.drawText(tag, Qt.AlignCenter, text)
        painter.restore()

    def _draw_handles(self, painter, box):
        painter.save()
        painter.resetTransform()
        pen = QPen(_color("frame"), 1.5)
        painter.setPen(pen)
        painter.setBrush(QColor(T.TABLE_HANDLE))
        scaled = QRectF(box.left() * self.scale, box.top() * self.scale, box.width() * self.scale, box.height() * self.scale)
        for x in (scaled.left(), scaled.center().x(), scaled.right()):
            for y in (scaled.top(), scaled.center().y(), scaled.bottom()):
                if (x, y) != (scaled.center().x(), scaled.center().y()):
                    painter.drawEllipse(QPointF(x, y), HANDLE_RADIUS, HANDLE_RADIUS)
        painter.restore()

    # ---- 鼠标 ----

    def _point(self, event):
        return QPointF(event.position().x() / self.scale, event.position().y() / self.scale)

    def _edge_at(self, point):
        """选中框的角或边：返回 "lt"、"r" 这类代号，不在边上返回 None。"""
        if self.selected is None:
            return None
        rect = QRectF(self.regions[self.selected])
        slop = HANDLE_HIT / self.scale
        if not rect.adjusted(-slop, -slop, slop, slop).contains(point):
            return None
        horizontal = "l" if abs(point.x() - rect.left()) <= slop else "r" if abs(point.x() - rect.right()) <= slop else ""
        vertical = "t" if abs(point.y() - rect.top()) <= slop else "b" if abs(point.y() - rect.bottom()) <= slop else ""
        return (horizontal + vertical) or None

    def _region_at(self, point):
        # 后画的在上面，重叠时先选中最上层。
        for index in range(len(self.regions) - 1, -1, -1):
            if QRectF(self.regions[index]).contains(point):
                return index
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        point = self._point(event)
        edge = self._edge_at(point)
        if edge is not None:
            self._drag = (edge, point, QRect(self.regions[self.selected]))
            return
        index = self._region_at(point)
        if index is not None:
            self.selected = index
            self._drag = ("move", point, QRect(self.regions[index]))
            self.setCursor(Qt.ClosedHandCursor)
        else:
            start = self._clamp_point(point)
            self._drag = ("new", start, QRect(start.toPoint(), QSize(0, 0)))
        self.update()

    def mouseMoveEvent(self, event):
        point = self._point(event)
        if self._drag is None:
            edge = self._edge_at(point)
            self.setCursor(_CURSORS[edge] if edge else Qt.OpenHandCursor if self._region_at(point) is not None
                           else Qt.CrossCursor)
            return
        mode, start, original = self._drag
        if mode == "new":
            end = self._clamp_point(point)
            self._drag = (mode, start, QRectF(start, end).normalized().toRect())
        elif mode == "move":
            dx, dy = round(point.x() - start.x()), round(point.y() - start.y())
            moved = original.translated(dx, dy)
            # 整框平移时不出页面，也不改大小。
            moved.moveLeft(max(0, min(moved.left(), self._bounds.width() - moved.width())))
            moved.moveTop(max(0, min(moved.top(), self._bounds.height() - moved.height())))
            self.regions[self.selected] = moved
        else:
            self.regions[self.selected] = self._resized(original, mode, self._clamp_point(point))
        self.update()

    def _resized(self, rect, edge, point):
        left, top, right, bottom = rect.left(), rect.top(), rect.right() + 1, rect.bottom() + 1
        x, y = round(point.x()), round(point.y())
        if "l" in edge:
            left = min(x, right - MIN_REGION)
        if "r" in edge:
            right = max(x, left + MIN_REGION)
        if "t" in edge:
            top = min(y, bottom - MIN_REGION)
        if "b" in edge:
            bottom = max(y, top + MIN_REGION)
        return QRect(left, top, right - left, bottom - top)

    def _clamp_point(self, point):
        return QPointF(max(0.0, min(point.x(), self._bounds.width())), max(0.0, min(point.y(), self._bounds.height())))

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._drag is None:
            return
        mode, _start, rect = self._drag
        self._drag = None
        if mode == "new":
            if rect.width() >= MIN_REGION and rect.height() >= MIN_REGION:
                self.regions.append(rect.intersected(self._bounds))
                self.selected = len(self.regions) - 1
                self.regionsChanged.emit()
        elif self.regions[self.selected] != rect:
            self.regionsChanged.emit()
        self.mouseMoveEvent(event)
        self.update()

    def contextMenuEvent(self, event):
        index = self._region_at(QPointF(event.pos().x() / self.scale, event.pos().y() / self.scale))
        if index is None:
            return
        self.selected = index
        self.update()
        menu = QMenu(self)
        menu.addAction("删除这个框", self.remove_selected)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected()
        elif event.key() == Qt.Key_Tab and self.regions:
            self.selected = 0 if self.selected is None else (self.selected + 1) % len(self.regions)
            self.update()
        else:
            super().keyPressEvent(event)

    def focusNextPrevChild(self, _next):
        # Tab 在框之间切换，不把焦点移出画布。
        return False
