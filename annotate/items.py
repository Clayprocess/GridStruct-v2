# 标注图元：QGraphicsItem 子类。样式在创建时从 MarkStyle 快照，
# 之后改样式不影响已有图元。

import math
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsTextItem
from PySide6.QtGui import (
    QBrush, QFont, QColor, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QPolygonF, QTextCharFormat,
    QTextBlockFormat,
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
        self.arrow_start = "none"
        self.arrow_end = "filled"
        self.polyline_cap = "round"
        self.polyline_join = "round"
        self.text_alignment = "left"
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
        self._cap = Qt.RoundCap if style.polyline_cap == "round" else Qt.SquareCap
        self._join = Qt.RoundJoin if style.polyline_join == "round" else Qt.MiterJoin

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
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        # 尖角的外延取自同一支 stroker，避免固定 padding 截断锐角。
        return self._stroke_shape(path).boundingRect().united(path.boundingRect()).adjusted(-2, -2, 2, 2)

    def _stroke_shape(self, path, width=None):
        stroker = QPainterPathStroker()
        stroker.setWidth(max(8, self._width if width is None else width))
        stroker.setCapStyle(self._cap)
        stroker.setJoinStyle(self._join)
        stroker.setMiterLimit(2)
        return stroker.createStroke(path)

    def paint(self, p: QPainter, _opt, _w):
        points = self.points + ([self._preview] if self._preview is not None else [])
        if len(points) < 2:
            return
        p.setPen(QPen(self._color, self._width, Qt.SolidLine,
                      self._cap, self._join))
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
            mode = QApplication.instance().property("theme_mode") or "light"
            color = QColor(T.THEMES[mode]["foreground"])
            color.setAlpha(40)
            p.fillRect(self._rect, color)
            return
        p.drawPixmap(self._rect, self._pixmap, QRectF(self._pixmap.rect()))


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
        self._start_head = style.arrow_start
        self._end_head = style.arrow_end
        self._control = None
        self.comment_item = QGraphicsTextItem(self)
        self.comment_item.setFont(QFont(style.font_family, style.font_size))
        self.comment_item.setDefaultTextColor(self._color)
        self.comment_item.setTextInteractionFlags(Qt.NoTextInteraction)
        self.comment_item.document().contentsChanged.connect(self._position_comment)
        self.comment_item.hide()

    def set_line(self, p1: QPointF, p2: QPointF):
        self.prepareGeometryChange()
        self._p1, self._p2 = QPointF(p1), QPointF(p2)
        if self._arrow_type == "curve":
            delta = p2 - p1
            self._control = (p1 + p2) / 2 + QPointF(-delta.y(), delta.x()) / 4
        self._position_comment()

    @property
    def control_point(self):
        return QPointF(self._control) if self._control is not None else None

    def set_control_point(self, point):
        self.prepareGeometryChange()
        self._control = QPointF(point)
        self._position_comment()
        self.update()

    def set_comment(self, text):
        self.comment_item.setPlainText(text)
        self.comment_item.setVisible(bool(text))
        self.comment_item.setTextInteractionFlags(Qt.NoTextInteraction)
        self._position_comment()

    def _path(self):
        path = QPainterPath(self._p1)
        if self._control is not None:
            path.quadTo(self._control, self._p2)
        else:
            path.lineTo(self._p2)
        return path

    def _position_comment(self):
        center = self._path().pointAtPercent(0.5)
        rect = self.comment_item.boundingRect()
        self.comment_item.setPos(center + QPointF(-rect.width() / 2, -rect.height() - 4))

    def boundingRect(self):
        rect = self._path().boundingRect()
        if self._control is not None:
            rect = rect.united(QRectF(self._control.x() - 6, self._control.y() - 6, 12, 12))
        pad = self._pad() + self._head
        return rect.adjusted(-pad, -pad, pad, pad)

    def shape(self):
        path = self._stroke_shape(self._path(), max(self._head, self._width))
        if self.isSelected() and self._control is not None:
            path.addEllipse(self._control, 7, 7)
        return path

    def paint(self, painter: QPainter, _opt, _w):
        if self._p1 == self._p2:
            return
        solid = self._arrow_type.startswith("solid")
        if solid:
            delta = self._p2 - self._p1
            length = math.hypot(delta.x(), delta.y())
            direction = delta / length
            normal = QPointF(-direction.y(), direction.x())
            head = min(self._head, length / 3)
            start_filled = self._start_head == "filled"
            end_filled = self._end_head == "filled"
            start = self._p1 + direction * head if start_filled else self._p1
            end = self._p2 - direction * head if end_filled else self._p2
            half = self._width / 2
            points = [start - normal * half, end - normal * half]
            if end_filled:
                points += [end - normal * head / 2, self._p2, end + normal * head / 2]
            points += [end + normal * half, start + normal * half]
            if start_filled:
                points += [start + normal * head / 2, self._p1, start - normal * head / 2]
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._color)
            painter.drawPolygon(QPolygonF(points))
        else:
            painter.setPen(QPen(self._color, self._width, Qt.SolidLine, Qt.RoundCap))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self._path())
        painter.setPen(QPen(self._color, self._width, Qt.SolidLine, Qt.RoundCap))
        start_other = self._control if self._control is not None else self._p2
        end_other = self._control if self._control is not None else self._p1
        if start_other == self._p1:
            start_other = self._p2
        if end_other == self._p2:
            end_other = self._p1
        for tip, other, style in ((self._p1, start_other, self._start_head),
                                  (self._p2, end_other, self._end_head)):
            if style == "none":
                continue
            if solid and style == "filled":
                continue
            delta = tip - other
            length = math.hypot(delta.x(), delta.y())
            if not length:
                continue
            direction = delta / length
            normal = QPointF(-direction.y(), direction.x())
            head = min(self._head, length / 3)
            left = tip - direction * head + normal * head / 2
            right = tip - direction * head - normal * head / 2
            if style == "open":
                painter.setBrush(Qt.NoBrush)
                painter.drawPolyline(QPolygonF([left, tip, right]))
            else:
                painter.setBrush(self._color)
                painter.drawPolygon(QPolygonF([tip, left, right]))
            if self._arrow_type == "dimension":
                painter.drawLine(tip - normal * head, tip + normal * head)
        if self.isSelected() and self._control is not None:
            # 控制点只在选中时显示，导出清除选中态后不会落入图片。
            mode = QApplication.instance().property("theme_mode") or "light"
            painter.setBrush(QColor(T.THEMES[mode]["background"]))
            painter.setPen(QPen(self._color, 1))
            painter.drawEllipse(self._control, 5, 5)


class TextMarkItem(QGraphicsTextItem):
    def __init__(self, style: MarkStyle):
        super().__init__()
        font = QFont(style.font_family)
        font.setPointSize(style.font_size)
        self.setFont(font)
        self.setDefaultTextColor(QColor(style.color))
        option = self.document().defaultTextOption()
        option.setAlignment({"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                             "right": Qt.AlignRight}[style.text_alignment])
        self.document().setDefaultTextOption(option)
        # Qt 的默认块格式覆盖 QTextOption，须设置首块让后续换行继承。
        block = QTextBlockFormat()
        block.setAlignment(option.alignment())
        cursor = self.textCursor()
        cursor.setBlockFormat(block)
        self.setTextCursor(cursor)
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
        self.document().contentsChanged.connect(self._resize_text)

    def _resize_text(self):
        # 多行共享自然宽度才有对齐空间；每次输入后重算，避免固定宽度造成换行。
        self.setTextWidth(-1)
        self.setTextWidth(self.document().idealWidth())

    def paint(self, painter, option, widget):
        if self._background is not None:
            painter.fillRect(self.boundingRect(), self._background)
        super().paint(painter, option, widget)
