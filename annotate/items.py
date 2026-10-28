# 每个图元保留自己的样式，默认绘制值与选中编辑互不污染。

import math
from copy import copy
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsTextItem
from PySide6.QtGui import (
    QBrush, QFont, QColor, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QPolygonF, QTextCharFormat,
    QTextBlockFormat, QTextCursor,
)
from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QPointF

from styles import tokens as T


class MarkStyle:
    """共享样式（画布持有，工具引用，图元创建时快照）。"""

    def __init__(self):
        self.color = QColor(T.ANNOTATION_COLORS[0])
        self.width = 3
        self.font_size = 16
        self.fill_mode = "outline"
        self.round_radius = 0
        self.arrow_type = "line"
        self.arrow_size = 16
        self.arrow_start = "none"
        self.arrow_end = "open"
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
        self.serial_type = "circle"
        self.serial_start = 1
        self.serial_global = False
        self.serial_auto_renumber = True
        self.mosaic_shape = "brush"
        self.mosaic_mode = "pixel"
        self.mosaic_strength = 10
        self.mosaic_size = 32
        self.opacity = 32
        self.eraser_size = 24
        self.zoom = 2
        self.wheel_enabled = True


def arrow_heads(arrow_type):
    """箭头类型对应的（起点, 终点）样式：线形和尺寸箭头用开放头，双向和尺寸箭头两端都有。"""
    head = "open" if arrow_type.startswith("line") or arrow_type == "dimension" else "filled"
    return (head if arrow_type.endswith("_both") or arrow_type == "dimension" else "none"), head


class _MarkItemBase(QGraphicsItem):
    style_keys = ("color", "width")

    def __init__(self, style: MarkStyle):
        super().__init__()
        self.style = copy(style)
        self._color = QColor(style.color)
        self._width = style.width
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

    def set_style(self, style):
        self.prepareGeometryChange()
        self.style = copy(style)
        self._color = QColor(style.color)
        self._width = style.width
        self.update()

    def _pad(self):
        return self._width + 2

    def _stroke_shape(self, path, width=None):
        stroker = QPainterPathStroker()
        stroker.setWidth(max(8, self._width if width is None else width))
        return stroker.createStroke(path)


class RectMarkItem(_MarkItemBase):
    tool_id = "rect"
    style_keys = (*_MarkItemBase.style_keys, "fill_mode", "round_radius")

    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._rect = QRectF()
        self._fill_mode = style.fill_mode
        self._radius = style.round_radius

    def set_style(self, style):
        super().set_style(style)
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
    tool_id = "ellipse"
    style_keys = (*_MarkItemBase.style_keys, "fill_mode")

    def shape(self):
        path = QPainterPath()
        path.addEllipse(self._rect)
        return path if self._fill_mode != "outline" else self._stroke_shape(path)

    def paint(self, p: QPainter, _opt, _w):
        p.setPen(QPen(self._color, self._width) if self._fill_mode != "fill" else Qt.NoPen)
        p.setBrush(self._color if self._fill_mode != "outline" else Qt.NoBrush)
        p.drawEllipse(self._rect)


class LineMarkItem(_MarkItemBase):
    tool_id = "line"
    style_keys = (*_MarkItemBase.style_keys, "line_style")

    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._p1 = QPointF()
        self._p2 = QPointF()
        self._line_style = style.line_style

    def set_style(self, style):
        super().set_style(style)
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
    tool_id = "polyline"
    style_keys = (*_MarkItemBase.style_keys, "polyline_cap", "polyline_join")

    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self.points: list[QPointF] = []
        self._preview: QPointF | None = None
        self._cap = Qt.RoundCap if style.polyline_cap == "round" else Qt.SquareCap
        self._join = Qt.RoundJoin if style.polyline_join == "round" else Qt.MiterJoin

    def set_style(self, style):
        super().set_style(style)
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
    tool_id = "pencil"

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
    tool_id = "highlight"
    style_keys = (*_MarkItemBase.style_keys, "opacity")

    def __init__(self, style: MarkStyle):
        super().__init__(style)
        self._path = QPainterPath()
        self.point_count = 0
        self._highlight_width = max(14, self._width * 4)
        self._opacity = style.opacity

    def set_style(self, style):
        super().set_style(style)
        self._highlight_width = max(14, style.width * 4)
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
        color.setAlphaF(self._color.alphaF() * self._opacity / 100)
        p.setPen(QPen(color, self._highlight_width, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(self._path)

    def shape(self):
        return self._stroke_shape(self._path, self._highlight_width)


def _mosaic(source: QPixmap, mode, strength) -> QPixmap:
    """像素化或模糊一块截图（物理像素）。"""
    w, h = source.width(), source.height()
    if mode == "blur":
        import numpy as np
        image = source.toImage().convertToFormat(QImage.Format_RGBA8888)
        pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(h, image.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
        padded = np.pad(pixels.astype(np.float64), ((strength, strength), (strength, strength), (0, 0)), mode="edge")
        integral = np.pad(padded, ((1, 0), (1, 0), (0, 0))).cumsum(0).cumsum(1)
        size = strength * 2 + 1
        blurred = ((integral[size:, size:] - integral[:-size, size:] - integral[size:, :-size] + integral[:-size, :-size]) / (size * size)).astype(np.uint8)
        return QPixmap.fromImage(QImage(blurred.data, w, h, w * 4, QImage.Format_RGBA8888).copy())
    small = source.scaled(max(1, w // strength), max(1, h // strength), Qt.IgnoreAspectRatio, Qt.FastTransformation)
    return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)


# 画笔马赛克按 256 物理像素分块、用到哪块算哪块：整张 4K 截图模糊一次要一两秒，按下画笔时会卡住。
_TILE = 256
_tiles: dict = {}


def _mosaic_tile(source: QPixmap, mode, strength, col, row) -> QPixmap:
    key = (source.cacheKey(), mode, strength, col, row)
    if key not in _tiles:
        bounds = QRect(0, 0, source.width(), source.height())
        tile = QRect(col * _TILE, row * _TILE, _TILE, _TILE).intersected(bounds)
        if mode == "blur":
            # 多取一圈原图参与模糊，块与块之间才接得上。
            area = tile.adjusted(-strength, -strength, strength, strength).intersected(bounds)
        else:
            # 格子按整图对齐，相邻块的马赛克格才连成一片。
            left, top = tile.left() // strength * strength, tile.top() // strength * strength
            right = min(bounds.right(), -(-(tile.right() + 1) // strength) * strength - 1)
            bottom = min(bounds.bottom(), -(-(tile.bottom() + 1) // strength) * strength - 1)
            area = QRect(QPoint(left, top), QPoint(right, bottom))
        region = source.copy(area)
        region.setDevicePixelRatio(1)
        if len(_tiles) > 512:
            del _tiles[next(iter(_tiles))]
        _tiles[key] = _mosaic(region, mode, strength).copy(tile.translated(-area.topLeft()))
    return _tiles[key]


class MosaicMarkItem(QGraphicsItem):
    """矩形马赛克处理选区那一块；画笔马赛克只露出涂抹轨迹下的部分。"""

    tool_id = "mosaic"
    style_keys = ("mosaic_mode", "mosaic_strength", "mosaic_size")

    def __init__(self, style=None):
        super().__init__()
        self.style = copy(style) if style is not None else MarkStyle()
        self._source = QPixmap()
        self._pixmap = QPixmap()
        self._rect = QRectF()
        # 画笔轨迹（图元坐标）；None 表示矩形马赛克。
        self._path = None
        self._stroke = QPainterPath()
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

    def set_source(self, pixmap, rect):
        self.prepareGeometryChange()
        self._source = pixmap
        self._pixmap = QPixmap()
        self._rect = rect
        self.set_style(self.style)

    def set_path(self, path: QPainterPath):
        """画笔模式：source 是整张截图，rect 是它在场景里的范围。"""
        self.prepareGeometryChange()
        self._path = QPainterPath(path)
        self._update_stroke()

    def _update_stroke(self):
        stroker = QPainterPathStroker()
        stroker.setWidth(self.style.mosaic_size)
        stroker.setCapStyle(Qt.RoundCap)
        stroker.setJoinStyle(Qt.RoundJoin)
        self._stroke = stroker.createStroke(self._path).simplified()

    def set_style(self, style):
        changed = (style.mosaic_mode, style.mosaic_strength) != (self.style.mosaic_mode, self.style.mosaic_strength)
        self.style = copy(style)
        if self._path is not None:
            self.prepareGeometryChange()
            self._update_stroke()
            self.update()
            return
        # 保留未处理的采样，二次调节强度时不会反复损失图像信息。
        if self._source.isNull() or not (changed or self._pixmap.isNull()):
            return
        self._pixmap = _mosaic(self._source, style.mosaic_mode, style.mosaic_strength)
        self.update()

    def set_preview_rect(self, rect: QRectF):
        self.prepareGeometryChange()
        self._rect = rect

    def boundingRect(self):
        return self._rect if self._path is None else self._stroke.boundingRect()

    def shape(self):
        if self._path is None:
            return super().shape()
        return self._stroke

    def paint(self, p: QPainter, _opt, _w):
        if self._path is not None:
            self._paint_brush(p)
            return
        if self._pixmap.isNull():
            mode = QApplication.instance().property("theme_mode") or "light"
            color = QColor(T.THEMES[mode]["foreground"])
            color.setAlpha(40)
            p.fillRect(self._rect, color)
            return
        p.drawPixmap(self._rect, self._pixmap, QRectF(self._pixmap.rect()))

    def _paint_brush(self, p):
        dpr = self._source.devicePixelRatio()
        area = self._stroke.boundingRect().intersected(self._rect)
        if area.isEmpty():
            return
        p.setClipPath(self._stroke)
        first_col, first_row = int(area.left() * dpr) // _TILE, int(area.top() * dpr) // _TILE
        last_col, last_row = int(area.right() * dpr) // _TILE, int(area.bottom() * dpr) // _TILE
        for row in range(first_row, last_row + 1):
            for col in range(first_col, last_col + 1):
                tile = _mosaic_tile(self._source, self.style.mosaic_mode, self.style.mosaic_strength, col, row)
                target = QRectF(col * _TILE / dpr, row * _TILE / dpr, tile.width() / dpr, tile.height() / dpr)
                p.drawPixmap(target.translated(self._rect.topLeft()), tile, QRectF(tile.rect()))


class SerialMarkItem(_MarkItemBase):
    tool_id = "serial"
    style_keys = ("color", "serial_size", "serial_type")

    def __init__(self, style: MarkStyle, index: int):
        super().__init__(style)
        self.index = index
        self._font_size = style.font_size
        self._radius = style.serial_size / 2
        self._type = style.serial_type

    def set_style(self, style):
        super().set_style(style)
        self._font_size = style.font_size
        self._radius = style.serial_size / 2
        self._type = style.serial_type

    def set_index(self, index: int):
        self.index = index
        self.update()

    def boundingRect(self):
        r = self._radius
        return QRectF(-r, -r, r * 2, r * 2)

    def _label(self):
        if self._type != "letter" or self.index < 1:
            return str(self.index)
        # 字母没有 0 和负数，微调到 1 以下时退回数字；超过 Z 按 AA、AB 续排。
        label, n = "", self.index
        while n:
            n, rest = divmod(n - 1, 26)
            label = chr(ord("A") + rest) + label
        return label

    def paint(self, p: QPainter, _opt, _w):
        rect = self.boundingRect()
        light = QColor(T.LIGHT["background"])
        text_color = light
        if self._type == "circle_outline":
            # 描边宽度随大小变化，内部填白底保证数字在任意截图背景上可读。
            border = max(2.0, self._radius / 6)
            p.setPen(QPen(self._color, border))
            p.setBrush(QBrush(light))
            p.drawEllipse(rect.adjusted(border / 2, border / 2, -border / 2, -border / 2))
            text_color = self._color
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(self._color))
            if self._type == "square":
                p.drawRoundedRect(rect, self._radius / 4, self._radius / 4)
            else:
                p.drawEllipse(rect)
        font = QFont()
        font.setPixelSize(max(8, int(self._radius * 1.1)))
        p.setFont(font)
        p.setPen(text_color)
        p.drawText(rect, Qt.AlignCenter, self._label())


class ArrowMarkItem(_MarkItemBase):
    tool_id = "arrow"
    style_keys = (*_MarkItemBase.style_keys, "arrow_type", "arrow_size", "arrow_start", "arrow_end")

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

    def set_style(self, style):
        super().set_style(style)
        self._arrow_type = style.arrow_type
        self._head = style.arrow_size
        self._start_head = style.arrow_start
        self._end_head = style.arrow_end
        if style.arrow_type == "curve":
            if self._control is None:
                delta = self._p2 - self._p1
                self._control = (self._p1 + self._p2) / 2 + QPointF(-delta.y(), delta.x()) / 4
        else:
            self._control = None
        self.comment_item.setDefaultTextColor(self._color)
        self._position_comment()

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
        self._control = QPointF(point) if point is not None else None
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
    tool_id = "text"
    style_keys = ("color", "font_size", "font_family", "text_alignment", "text_background",
                  "background_color", "text_outline", "outline_color")

    def __init__(self, style: MarkStyle):
        super().__init__()
        self.style = copy(style)
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

    def set_style(self, style):
        self.prepareGeometryChange()
        self.style = copy(style)
        font = QFont(style.font_family, style.font_size)
        self.setFont(font)
        self.setDefaultTextColor(style.color)
        cursor = self.textCursor()
        cursor.select(QTextCursor.Document)
        fmt = QTextCharFormat()
        fmt.setFont(font)
        fmt.setForeground(style.color)
        fmt.setTextOutline(QPen(style.outline_color, 1) if style.text_outline else QPen(Qt.NoPen))
        cursor.mergeCharFormat(fmt)
        alignment = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}[style.text_alignment]
        option = self.document().defaultTextOption()
        option.setAlignment(alignment)
        self.document().setDefaultTextOption(option)
        block = QTextBlockFormat()
        block.setAlignment(alignment)
        cursor.mergeBlockFormat(block)
        self._background = QColor(style.background_color) if style.text_background else None
        self._resize_text()
        self.update()

    def _resize_text(self):
        # 多行共享自然宽度才有对齐空间；每次输入后重算，避免固定宽度造成换行。
        self.setTextWidth(-1)
        self.setTextWidth(self.document().idealWidth())

    def paint(self, painter, option, widget):
        if self._background is not None:
            painter.fillRect(self.boundingRect(), self._background)
        super().paint(painter, option, widget)


class EraseMarkItem(QGraphicsItem):
    tool_id = "eraser"
    style_keys = ("eraser_size", "background_color")

    def __init__(self, style: MarkStyle):
        super().__init__()
        self.style = copy(style)
        self._trail = QPainterPath()
        self._area = QPainterPath()
        self._area.setFillRule(Qt.WindingFill)
        self._last_point = QPointF()
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

    def move_to(self, pos):
        self.prepareGeometryChange()
        self._last_point = QPointF(pos)
        self._trail = QPainterPath(pos)
        self._area = QPainterPath()
        self._area.setFillRule(Qt.WindingFill)
        radius = self.style.eraser_size / 2
        self._area.addEllipse(pos, radius, radius)
        self.update()

    def line_to(self, pos):
        if pos == self._last_point:
            return
        self.prepareGeometryChange()
        segment = QPainterPath(self._last_point)
        segment.lineTo(pos)
        stroker = QPainterPathStroker()
        stroker.setWidth(self.style.eraser_size)
        stroker.setCapStyle(Qt.RoundCap)
        # 补齐两次鼠标事件之间的扫掠区域，快速拖动也不会留空洞。
        self._area.addPath(stroker.createStroke(segment))
        self._trail.lineTo(pos)
        self._last_point = QPointF(pos)
        self.update()

    def set_style(self, style: MarkStyle):
        self.prepareGeometryChange()
        self.style = copy(style)
        stroker = QPainterPathStroker()
        stroker.setWidth(style.eraser_size)
        stroker.setCapStyle(Qt.RoundCap)
        stroker.setJoinStyle(Qt.RoundJoin)
        self._area = stroker.createStroke(self._trail)
        self._area.setFillRule(Qt.WindingFill)
        radius = style.eraser_size / 2
        self._area.addEllipse(self._trail.pointAtPercent(0), radius, radius)
        self.update()

    def boundingRect(self):
        return self._area.boundingRect().adjusted(-1, -1, 1, 1)

    def shape(self):
        return self._area

    def paint(self, painter, _option, _widget):
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.style.background_color)
        painter.drawPath(self._area)
