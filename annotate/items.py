# 标注图元：QGraphicsItem 子类。样式在创建时从 MarkStyle 快照，
# 之后改样式不影响已有图元。

import math
from PySide6.QtWidgets import QGraphicsItem, QGraphicsTextItem
from PySide6.QtGui import (
    QBrush, QFont, QColor, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QPolygonF, QTextCharFormat,
)
from PySide6.QtCore import Qt, QRectF, QPointF

from styles import tokens as T


class MarkStyle:
    """共享样式（画布持有，工具引用，图元创建时快照）。"""

    def __init__(self):
        self.color = QColor(T.ANNOTATION_COLORS[0])
        self.width = 3
        self.font_size = 16
        self.fill_mode = "outline"
        self.round_radius = 0
        self.arrow_type = "straight"
        self.arrow_size = 16
        self.line_style = "solid"
        self.font_family = "Microsoft YaHei UI"
        self.text_background = False
        self.text_outline = False
        self.background_color = QColor(T.LIGHT["background"])
        self.outline_color = QColor(T.LIGHT["background"])
        self.serial_size = 32
        self.serial_start = 1
        self.serial_global = False
        self.serial_auto_renumber = True
        self.mosaic_mode = "pixel"
        self.mosaic_strength = 10
        self.opacity = 32
        self.eraser_size = 24
        self.zoom = 2
        self.wheel_enabled = True


class _MarkItemBase(QGraphicsItem):
    def __init__(self, style: MarkStyle):
        super().__init__()
        self._color = QColor(style.color)
        self._width = style.width
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

    def _pad(self):
        return self._width + 2

    def _stroke_shape(self, path, width=None):
        stroker = QPainterPathStroker()
        stroker.setWidth(max(8, self._width if width is None else width))
        return stroker.createStroke(path)


class RectMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._rect = QRectF()
        self._fill_mode = style.fill_mode
        self._radius = style.round_radius

    def set_rect(self, r: QRectF):
        self.prepareGeometryChange()
        self._rect = r

    def boundingRect(self):
        p = self._pad()
        return self._rect.adjusted(-p, -p, p, p)

    def paint(self, p: QPainter, _opt, _w):
        p.setPen(QPen(self._color, self._width) if self._fill_mode != "fill" else Qt.NoPen)
        p.setBrush(self._color if self._fill_mode != "outline" else Qt.NoBrush)
        p.drawRoundedRect(self._rect, self._radius, self._radius)

    def shape(self):
        path = QPainterPath()
        path.addRoundedRect(self._rect, self._radius, self._radius)
        return path if self._fill_mode != "outline" else self._stroke_shape(path)


class EllipseMarkItem(RectMarkItem):
    def shape(self):
        path = QPainterPath()
        path.addEllipse(self._rect)
        return path if self._fill_mode != "outline" else self._stroke_shape(path)

    def paint(self, p: QPainter, _opt, _w):
        p.setPen(QPen(self._color, self._width) if self._fill_mode != "fill" else Qt.NoPen)
        p.setBrush(self._color if self._fill_mode != "outline" else Qt.NoBrush)
        p.drawEllipse(self._rect)


class LineMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._p1 = QPointF()
        self._p2 = QPointF()
        self._line_style = style.line_style

    def set_line(self, p1: QPointF, p2: QPointF):
        self.prepareGeometryChange()
        self._p1, self._p2 = p1, p2

    def boundingRect(self):
        p = self._pad() * 3
        return QRectF(self._p1, self._p2).normalized().adjusted(-p, -p, p, p)

    def shape(self):
        path = QPainterPath(self._p1)
        path.lineTo(self._p2)
        return self._stroke_shape(path, self._width * 3)

    def paint(self, p: QPainter, _opt, _w):
        pen = QPen(self._color, self._width, Qt.SolidLine, Qt.RoundCap)
        if self._line_style in ("dash", "dot"):
            pen.setStyle(Qt.DashLine if self._line_style == "dash" else Qt.DotLine)
        p.setPen(pen)
        delta = self._p2 - self._p1
        length = math.hypot(delta.x(), delta.y())
        if not length:
            return
        normal = QPointF(-delta.y() / length, delta.x() / length)
        if self._line_style == "double":
            offset = normal * self._width
            p.drawLine(self._p1 + offset, self._p2 + offset)
            p.drawLine(self._p1 - offset, self._p2 - offset)
        elif self._line_style == "wavy":
            path = QPainterPath(self._p1)
            steps = max(2, int(length))
            for i in range(1, steps + 1):
                t = i / steps
                path.lineTo(self._p1 + delta * t + normal * (math.sin(t * length / 5) * self._width))
            p.drawPath(path)
        else:
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

    def set_preview(self, point: QPointF | None):
        self.prepareGeometryChange()
        self._preview = QPointF(point) if point is not None else None

    def remove_last_point(self):
        self.prepareGeometryChange()
        self.points.pop()

    def boundingRect(self):
        points = self.points + ([self._preview] if self._preview is not None else [])
        if not points:
            return QRectF()
        xs, ys = [pt.x() for pt in points], [pt.y() for pt in points]
        rect = QRectF(QPointF(min(xs), min(ys)), QPointF(max(xs), max(ys)))
        p = self._pad()
        return rect.adjusted(-p, -p, p, p)

    def paint(self, p: QPainter, _opt, _w):
        points = self.points + ([self._preview] if self._preview is not None else [])
        if len(points) < 2:
            return
        p.setPen(QPen(self._color, self._width, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(QPolygonF(points))

    def shape(self):
        path = QPainterPath()
        if self.points:
            path.moveTo(self.points[0])
            for point in self.points[1:]:
                path.lineTo(point)
        return self._stroke_shape(path)


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

    def set_straight(self, start: QPointF, end: QPointF):
        self.prepareGeometryChange()
        self._path = QPainterPath(start)
        self._path.lineTo(end)
        self.point_count = 2

    def boundingRect(self):
        p = self._pad()
        return self._path.boundingRect().adjusted(-p, -p, p, p)

    def paint(self, p: QPainter, _opt, _w):
        p.setPen(QPen(self._color, self._width, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(self._path)

    def shape(self):
        return self._stroke_shape(self._path)


class HighlightMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._path = QPainterPath()
        self.point_count = 0
        self._highlight_width = max(14, self._width * 4)
        self._opacity = style.opacity

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
        color.setAlphaF(self._opacity / 100)
        p.setPen(QPen(color, self._highlight_width, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(self._path)

    def shape(self):
        return self._stroke_shape(self._path, self._highlight_width)


class MosaicMarkItem(QGraphicsItem):
    def __init__(self):
        super().__init__()
        self._pixmap = QPixmap()
        self._rect = QRectF()
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

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


class MagnifierMarkItem(QGraphicsItem):
    def __init__(self, bg_pixmap: QPixmap, center: QPointF,
                 radius=70.0, zoom=2.0):
        super().__init__()
        self._bg_pixmap = bg_pixmap
        self._center = QPointF(center)
        self._radius = radius
        self._zoom = zoom
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

    def boundingRect(self):
        r = self._radius
        return QRectF(self._center.x() - r, self._center.y() - r,
                      r * 2, r * 2)

    def paint(self, p: QPainter, _opt, _w):
        target = self.boundingRect()
        source_size = self._radius * 2 / self._zoom
        dpr = self._bg_pixmap.devicePixelRatio()
        source = QRectF(
            (self._center.x() - source_size / 2) * dpr,
            (self._center.y() - source_size / 2) * dpr,
            source_size * dpr,
            source_size * dpr,
        )
        clip = QPainterPath()
        clip.addEllipse(target)
        p.save()
        p.setClipPath(clip)
        p.drawPixmap(target, self._bg_pixmap, source)
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(T.LIGHT["foreground"]), 2))
        p.drawEllipse(target)


class SerialMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle, index: int):
        super().__init__(style)
        self.index = index
        self._font_size = style.font_size
        self._radius = style.serial_size / 2

    def set_index(self, index: int):
        self.index = index
        self.update()

    def boundingRect(self):
        r = self._radius
        return QRectF(-r, -r, r * 2, r * 2)

    def paint(self, p: QPainter, _opt, _w):
        rect = self.boundingRect()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(self._color))
        p.drawEllipse(rect)
        font = QFont()
        font.setPixelSize(max(8, int(self._radius * 1.1)))
        p.setFont(font)
        p.setPen(QColor(T.LIGHT["background"]))
        p.drawText(rect, Qt.AlignCenter, str(self.index))


class ArrowMarkItem(_MarkItemBase):
    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._p1 = QPointF()
        self._p2 = QPointF()
        self._arrow_type = style.arrow_type
        self._head = style.arrow_size

    def set_line(self, p1: QPointF, p2: QPointF):
        self.prepareGeometryChange()
        self._p1, self._p2 = p1, p2

    def boundingRect(self):
        p = self._pad() + self._head
        return QRectF(self._p1, self._p2).normalized().adjusted(-p, -p, p, p)

    def shape(self):
        path = QPainterPath(self._p1)
        path.lineTo(self._p2)
        return self._stroke_shape(path, max(self._head, self._width))

    def paint(self, p: QPainter, _opt, _w):
        if self._p1 == self._p2:
            return
        delta = self._p2 - self._p1
        length = math.hypot(delta.x(), delta.y())
        unit = delta / length
        normal = QPointF(-unit.y(), unit.x())
        head = min(self._head, length / 3)
        bilateral = self._arrow_type.endswith("_both") or self._arrow_type == "dimension"
        solid = self._arrow_type.startswith("solid")
        open_head = self._arrow_type.startswith("line") or self._arrow_type == "dimension"
        p.setPen(QPen(self._color, self._width, Qt.SolidLine, Qt.RoundCap))
        if solid:
            half = self._width / 2
            start = self._p1 + unit * head if bilateral else self._p1
            end = self._p2 - unit * head
            points = [self._p1] if bilateral else [start - normal * half]
            if bilateral:
                points += [start - normal * head / 2, start - normal * half]
            points += [end - normal * half, end - normal * head / 2, self._p2,
                       end + normal * head / 2, end + normal * half, start + normal * half]
            if bilateral:
                points += [start + normal * head / 2]
            p.setBrush(self._color)
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF(points))
        else:
            p.drawLine(self._p1, self._p2)
            ends = [(self._p2, unit)]
            if bilateral:
                ends.append((self._p1, -unit))
            for tip, direction in ends:
                left = tip - direction * head + normal * head / 2
                right = tip - direction * head - normal * head / 2
                if open_head:
                    p.drawPolyline(QPolygonF([left, tip, right]))
                else:
                    p.setBrush(self._color)
                    p.drawPolygon(QPolygonF([tip, left, right]))
            if self._arrow_type == "dimension":
                for endpoint in (self._p1, self._p2):
                    p.drawLine(endpoint - normal * head, endpoint + normal * head)


class TextMarkItem(QGraphicsTextItem):
    def __init__(self, style: MarkStyle):
        super().__init__()
        font = QFont(style.font_family)
        font.setPointSize(style.font_size)
        self.setFont(font)
        self.setDefaultTextColor(QColor(style.color))
        self._background = QColor(style.background_color) if style.text_background else None
        self.document().setDocumentMargin(5)
        if style.text_outline:
            fmt = QTextCharFormat()
            fmt.setTextOutline(QPen(style.outline_color, 1))
            cursor = self.textCursor()
            cursor.setCharFormat(fmt)
            self.setTextCursor(cursor)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFlags(self.flags() | QGraphicsTextItem.ItemIsSelectable | QGraphicsTextItem.ItemIsMovable)

    def paint(self, painter, option, widget):
        if self._background is not None:
            painter.fillRect(self.boundingRect(), self._background)
        super().paint(painter, option, widget)
