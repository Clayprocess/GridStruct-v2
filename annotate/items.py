# 标注图元：QGraphicsItem 子类。样式在创建时从 MarkStyle 快照，
# 之后改样式不影响已有图元。

import math
from PySide6.QtWidgets import QGraphicsItem, QGraphicsTextItem
from PySide6.QtGui import QPen, QColor, QPainter, QPolygonF, QFont
from PySide6.QtCore import Qt, QRectF, QPointF


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
