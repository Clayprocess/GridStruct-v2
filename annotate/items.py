# 标注图元：QGraphicsItem 子类。样式在创建时从 MarkStyle 快照，
# 之后改样式不影响已有图元。

import math
from PySide6.QtWidgets import QGraphicsItem, QGraphicsTextItem
from PySide6.QtGui import (
    QBrush, QFont, QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtCore import Qt, QRectF, QPointF

from styles import tokens as T


class MarkStyle:
    """共享样式（画布持有，工具引用，图元创建时快照）。"""

    def __init__(self):
        self.color = QColor("#ef4444")  # 默认 PixPin 红
        self.width = 3
        self.font_size = 16


class _MarkItemBase(QGraphicsItem):
    def __init__(self, style: MarkStyle):
        super().__init__()
        self._color = QColor(style.color)
        self._width = style.width
        self.setFlags(QGraphicsItem.ItemIsSelectable)

    def _pad(self):
        return self._width + 2


class RectMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._rect = QRectF()

    def set_rect(self, r: QRectF):
        self.prepareGeometryChange()
        self._rect = r

    def boundingRect(self):
        p = self._pad()
        return self._rect.adjusted(-p, -p, p, p)

    def paint(self, p: QPainter, _opt, _w):
        p.setPen(QPen(self._color, self._width))
        p.drawRect(self._rect)


class EllipseMarkItem(RectMarkItem):
    def paint(self, p: QPainter, _opt, _w):
        p.setPen(QPen(self._color, self._width))
        p.drawEllipse(self._rect)


class LineMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._p1 = QPointF()
        self._p2 = QPointF()

    def set_line(self, p1: QPointF, p2: QPointF):
        self.prepareGeometryChange()
        self._p1, self._p2 = p1, p2

    def boundingRect(self):
        p = self._pad()
        return QRectF(self._p1, self._p2).normalized().adjusted(-p, -p, p, p)

    def paint(self, p: QPainter, _opt, _w):
        pen = QPen(self._color, self._width, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(self._p1, self._p2)


class PolylineMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self.points: list[QPointF] = []
        self._preview: QPointF | None = None

    def add_point(self, point: QPointF):
        self.prepareGeometryChange()
        self.points.append(QPointF(point))
        self._preview = None

    def set_preview(self, point: QPointF):
        self.prepareGeometryChange()
        self._preview = QPointF(point)

    def remove_last_point(self):
        self.prepareGeometryChange()
        self.points.pop()

    def boundingRect(self):
        points = self.points + ([self._preview] if self._preview is not None else [])
        if not points:
            return QRectF()
        rect = QRectF(points[0], points[0])
        for point in points[1:]:
            rect = rect.united(QRectF(point, point))
        p = self._pad()
        return rect.adjusted(-p, -p, p, p)

    def paint(self, p: QPainter, _opt, _w):
        points = self.points + ([self._preview] if self._preview is not None else [])
        if len(points) < 2:
            return
        p.setPen(QPen(self._color, self._width, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(QPolygonF(points))


class PencilMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._path = QPainterPath()
        self.point_count = 0

    def move_to(self, point: QPointF):
        self.prepareGeometryChange()
        self._path.moveTo(point)
        self.point_count = 1

    def line_to(self, point: QPointF):
        self.prepareGeometryChange()
        self._path.lineTo(point)
        self.point_count += 1

    def boundingRect(self):
        p = self._pad()
        return self._path.boundingRect().adjusted(-p, -p, p, p)

    def paint(self, p: QPainter, _opt, _w):
        p.setPen(QPen(self._color, self._width, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(self._path)


class HighlightMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._path = QPainterPath()
        self.point_count = 0
        self._highlight_width = max(14, self._width * 4)

    def move_to(self, point: QPointF):
        self.prepareGeometryChange()
        self._path.moveTo(point)
        self.point_count = 1

    def line_to(self, point: QPointF):
        self.prepareGeometryChange()
        self._path.lineTo(point)
        self.point_count += 1

    def boundingRect(self):
        p = self._highlight_width + 2
        return self._path.boundingRect().adjusted(-p, -p, p, p)

    def paint(self, p: QPainter, _opt, _w):
        color = QColor(self._color)
        color.setAlpha(80)
        p.setPen(QPen(color, self._highlight_width, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(self._path)


class MosaicMarkItem(QGraphicsItem):
    def __init__(self):
        super().__init__()
        self._pixmap = QPixmap()
        self._rect = QRectF()
        self.setFlags(QGraphicsItem.ItemIsSelectable)

    def set_preview_rect(self, rect: QRectF):
        self.prepareGeometryChange()
        self._rect = rect

    def set_mosaic(self, pixmap: QPixmap, rect: QRectF):
        self.prepareGeometryChange()
        self._pixmap = pixmap
        self._rect = rect

    def boundingRect(self):
        return self._rect

    def paint(self, p: QPainter, _opt, _w):
        if self._pixmap.isNull():
            color = QColor(T.LIGHT["foreground"])
            color.setAlpha(40)
            p.fillRect(self._rect, color)
            return
        p.drawPixmap(self._rect, self._pixmap, QRectF(self._pixmap.rect()))


class SerialMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle, index: int):
        super().__init__(style)
        self.index = index
        self._font_size = style.font_size

    def set_index(self, index: int):
        self.index = index
        self.update()

    def boundingRect(self):
        r = self._font_size
        return QRectF(-r, -r, r * 2, r * 2)

    def paint(self, p: QPainter, _opt, _w):
        rect = self.boundingRect()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(self._color))
        p.drawEllipse(rect)
        font = QFont()
        font.setPointSize(self._font_size)
        p.setFont(font)
        p.setPen(QColor(T.LIGHT["background"]))
        p.drawText(rect, Qt.AlignCenter, str(self.index))


class ArrowMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._p1 = QPointF()
        self._p2 = QPointF()

    def set_line(self, p1: QPointF, p2: QPointF):
        self.prepareGeometryChange()
        self._p1, self._p2 = p1, p2

    def boundingRect(self):
        p = self._pad() + 14
        return QRectF(self._p1, self._p2).normalized().adjusted(-p, -p, p, p)

    def paint(self, p: QPainter, _opt, _w):
        if self._p1 == self._p2:
            return
        pen = QPen(self._color, self._width)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(self._p1, self._p2)
        # 箭头
        angle = math.atan2(self._p2.y() - self._p1.y(),
                           self._p2.x() - self._p1.x())
        head = max(12.0, self._width * 4.0)
        a1 = angle + math.pi - 0.42
        a2 = angle + math.pi + 0.42
        poly = QPolygonF([
            self._p2,
            self._p2 + QPointF(math.cos(a1), math.sin(a1)) * head,
            self._p2 + QPointF(math.cos(a2), math.sin(a2)) * head,
        ])
        p.setBrush(self._color)
        p.setPen(Qt.NoPen)
        p.drawPolygon(poly)


class TextMarkItem(QGraphicsTextItem):
    def __init__(self, style: MarkStyle):
        super().__init__()
        font = QFont()
        font.setPointSize(style.font_size)
        self.setFont(font)
        self.setDefaultTextColor(QColor(style.color))
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFlags(QGraphicsTextItem.ItemIsSelectable)
