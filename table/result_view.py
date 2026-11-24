"""结果表的可缩放画布；保留原生表格编辑、选区与委托。"""

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QGraphicsProxyWidget, QGraphicsScene,
    QGraphicsView, QTableWidget, QTableWidgetSelectionRange,
)

from styles import tokens as T
from ui import ZoomBar

_MIN_SCALE, _MAX_SCALE, _FIT_MAX, _STEP = 0.1, 8.0, 2.0, 1.25


def _header_cursor(horizontal):
    # 小箭头只画透明细线，避免遮住列名或行号。
    image = QPixmap(32, 32)
    image.fill(Qt.transparent)
    image.setDevicePixelRatio(2)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(T.THEMES[QApplication.instance().property("theme_mode") or "light"]["foreground"])
    painter.setPen(QPen(color, 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    points = [QPointF(8, 2), QPointF(8, 12), QPointF(4, 8), QPointF(8, 12), QPointF(12, 8)]
    if not horizontal:
        points = [QPointF(point.y(), point.x()) for point in points]
    painter.drawPolyline(QPolygonF(points))
    painter.end()
    return QCursor(image, 8 if horizontal else 12, 12 if horizontal else 8)


class ResultTable(QTableWidget):
    """原生单元格编辑，连续选区不逐格绘制焦点框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ReviewTable")
        self.setFrameShape(QFrame.NoFrame)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAutoScroll(False)
        self.horizontalHeader().setSectionsClickable(True)
        self.verticalHeader().setSectionsClickable(True)
        self.horizontalHeader().setCursor(_header_cursor(True))
        self.verticalHeader().setCursor(_header_cursor(False))
        self.horizontalHeader().setHighlightSections(True)
        self.verticalHeader().setHighlightSections(True)
        self.verticalHeader().setDefaultSectionSize(32)


class _RoundedTable(QGraphicsProxyWidget):
    def paint(self, painter, option, widget=None):
        rect = self.boundingRect()
        path = QPainterPath()
        path.addRoundedRect(rect, T.TABLE_CONTROL_RADIUS, T.TABLE_CONTROL_RADIUS)
        painter.save()
        painter.setClipPath(path)
        super().paint(painter, option, widget)
        painter.restore()
        color = T.THEMES[QApplication.instance().property("theme_mode") or "light"]["table_border"]
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(color), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), T.TABLE_CONTROL_RADIUS, T.TABLE_CONTROL_RADIUS)


class ResultView(QGraphicsView):
    """外层只管理视图变换，不改数据，也不占用表格的撤销历史。"""

    def __init__(self, table, parent=None):
        super().__init__(parent)
        self.setObjectName("TableResultView")
        self.table = table
        self.setScene(QGraphicsScene(self))
        self._proxy = _RoundedTable()
        self.scene().addItem(self._proxy)
        self._proxy.setWidget(table)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignCenter)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self._scale = 1.0
        self._fit = True
        self._space = False
        self._pan = None
        self._selection = None
        self._selection_point = QPoint()
        self._preview_locked = False
        self._bar = ZoomBar(self, self)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(30)
        self._scroll_timer.timeout.connect(self._drag_scroll)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self.table.horizontalHeader().viewport().installEventFilter(self)
        self.table.verticalHeader().viewport().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self._application_state)
        self.table.horizontalHeader().sectionResized.connect(self.refresh_geometry)
        self.table.verticalHeader().sectionResized.connect(self.refresh_geometry)
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._layout_table)
        self.refresh_geometry()

    @property
    def scale(self):
        return self._scale

    @property
    def fitted(self):
        return self._fit

    def focus_table(self):
        # 代理内部焦点不能激活外层视图；两层一起设置才能让真实键盘输入到表格。
        self.setFocus(Qt.OtherFocusReason)
        self.table.setFocus(Qt.OtherFocusReason)
        item = self.table.currentItem()
        if item is not None:
            rect = QRectF(self.table.visualItemRect(item))
            rect.translate(QPointF(self.table.viewport().pos()))
            self.ensureVisible(self._proxy.mapRectToScene(rect), 16, 16)

    def refresh_geometry(self, *_args):
        """行列数、列宽或行高变化后合并刷新；编辑文字不会重置手动缩放和平移。"""
        self._geometry_timer.start(0)

    def _layout_table(self):
        table = self.table
        width = table.verticalHeader().width() + sum(table.columnWidth(c) for c in range(table.columnCount()))
        height = table.horizontalHeader().height() + sum(table.rowHeight(r) for r in range(table.rowCount()))
        table.setFixedSize(max(1, width), max(1, height))
        self._proxy.setVisible(table.rowCount() > 0 and table.columnCount() > 0)
        self._update_scene_rect()
        if self._fit:
            self.fit()

    def _update_scene_rect(self):
        # 视口两倍的空白让小表也能自由平移，不依赖表格自身的滚动条。
        margin_x = self.viewport().width() * 2 / self.scale
        margin_y = self.viewport().height() * 2 / self.scale
        self.setSceneRect(self._proxy.boundingRect().adjusted(-margin_x, -margin_y, margin_x, margin_y))

    def fit(self):
        if self._preview_locked:
            return
        self.stop_interaction()
        self._fit = True
        bounds = self._proxy.boundingRect()
        available = self.viewport().size()
        scale = min((available.width() - 64) / max(1, bounds.width()),
                    (available.height() - 64) / max(1, bounds.height()))
        self._apply_scale(max(_MIN_SCALE, min(_FIT_MAX, scale)))
        self.centerOn(bounds.center())
        self._bar.update_state()

    def _apply_scale(self, scale):
        self._scale = scale
        self.resetTransform()
        # scale 属性供共用缩放栏读取，显式调用基类的变换方法。
        QGraphicsView.scale(self, scale, scale)
        self._update_scene_rect()

    def set_scale(self, scale, anchor=None):
        if self._preview_locked:
            return
        self._fit = False
        anchor = self.viewport().rect().center() if anchor is None else anchor
        point = self.mapToScene(anchor)
        self._apply_scale(max(_MIN_SCALE, min(_MAX_SCALE, scale)))
        moved = self.mapFromScene(point) - anchor
        self._scroll(moved.x(), moved.y())
        self._bar.update_state()

    def zoom_by(self, factor, anchor=None):
        self.set_scale(self.scale * factor, anchor)

    def set_preview_locked(self, locked):
        self.stop_interaction()
        self._preview_locked = locked
        self._bar.setEnabled(not locked)

    def stop_interaction(self):
        self._pan = None
        self._space = False
        self._selection = None
        self._scroll_timer.stop()
        self.viewport().unsetCursor()

    def _application_state(self, state):
        if state != Qt.ApplicationActive:
            self.stop_interaction()

    def _editing(self):
        return self.table.state() == QAbstractItemView.EditingState

    def _space_event(self, event):
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) and event.key() == Qt.Key_Space and not self._editing():
            if not event.isAutoRepeat():
                self._space = event.type() == QEvent.KeyPress
                self.viewport().setCursor(Qt.OpenHandCursor if self._space else Qt.ArrowCursor)
            return True
        return False

    def eventFilter(self, watched, event):
        if self._space_event(event):
            return True
        if event.type() == QEvent.FocusOut and not self._pan:
            self._space = False
        if self._preview_locked and event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick,
                                                     QEvent.MouseMove, QEvent.KeyPress, QEvent.Wheel):
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if self._space_event(event):
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._space_event(event):
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def wheelEvent(self, event):
        if self._preview_locked:
            event.accept()
        elif self._editing():
            super().wheelEvent(event)
        elif event.angleDelta().y():
            self.zoom_by(_STEP ** (event.angleDelta().y() / 120), event.position().toPoint())
            event.accept()
        else:
            super().wheelEvent(event)

    def _table_point(self, viewport_point):
        return self._proxy.mapFromScene(self.mapToScene(viewport_point)).toPoint()

    def mousePressEvent(self, event):
        if self._preview_locked:
            event.accept()
            return
        point = self._table_point(event.position().toPoint())
        blank = not self.table.rect().contains(point)
        forced = event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and self._space and not self._editing())
        if forced or (blank and event.button() == Qt.LeftButton):
            self._pan = (event.position().toPoint(), event.button())
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton and not self._editing():
            self._selection = self._selection_origin(point)
            self._selection_point = event.position().toPoint()
            if self._selection is not None:
                self._scroll_timer.start()

    def _selection_origin(self, point):
        table = self.table
        hp, vp = table.horizontalHeader(), table.verticalHeader()
        if hp.geometry().contains(point):
            column = hp.logicalIndexAt(point - hp.pos())
            return ("column", 0, column) if column >= 0 else None
        if vp.geometry().contains(point):
            row = vp.logicalIndexAt(point - vp.pos())
            return ("row", row, 0) if row >= 0 else None
        index = table.indexAt(point - table.viewport().pos())
        return ("cell", index.row(), index.column()) if index.isValid() else None

    def mouseMoveEvent(self, event):
        if self._pan is not None:
            previous, button = self._pan
            point = event.position().toPoint()
            delta = point - previous
            self._pan = (point, button)
            self._fit = False
            self._scroll(-delta.x(), -delta.y())
            self._bar.update_state()
            event.accept()
            return
        self._selection_point = event.position().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan is not None and event.button() == self._pan[1]:
            self.stop_interaction()
            event.accept()
            return
        self._selection = None
        self._scroll_timer.stop()
        super().mouseReleaseEvent(event)

    def _scroll(self, dx, dy):
        for bar, delta in ((self.horizontalScrollBar(), dx), (self.verticalScrollBar(), dy)):
            bar.setValue(bar.value() + round(delta))

    def _drag_scroll(self):
        if self._selection is None:
            return
        point = self._selection_point
        rect = self.viewport().rect()
        kind, row, column = self._selection
        margin = 28
        dx = min(24, point.x() - rect.right() + margin) if point.x() > rect.right() - margin else max(-24, point.x() - margin) if point.x() < margin else 0
        dy = min(24, point.y() - rect.bottom() + margin) if point.y() > rect.bottom() - margin else max(-24, point.y() - margin) if point.y() < margin else 0
        if kind == "row":
            dx = 0
        elif kind == "column":
            dy = 0
        if not dx and not dy:
            return
        self._fit = False
        self._scroll(dx, dy)
        self._bar.update_state()
        table = self.table
        local = self._table_point(point) - table.viewport().pos()
        target_row = table.rowAt(max(0, min(table.viewport().height() - 1, local.y())))
        target_column = table.columnAt(max(0, min(table.viewport().width() - 1, local.x())))
        if target_row < 0:
            target_row = table.rowCount() - 1
        if target_column < 0:
            target_column = table.columnCount() - 1
        top, bottom = sorted((row, target_row))
        left, right = sorted((column, target_column))
        if kind == "row":
            left, right = 0, table.columnCount() - 1
        elif kind == "column":
            top, bottom = 0, table.rowCount() - 1
        table.clearSelection()
        table.setRangeSelected(QTableWidgetSelectionRange(top, left, bottom, right), True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "_bar"):
            return
        self._update_scene_rect()
        self._bar.place()
        if self.fitted:
            self.fit()

    def focusOutEvent(self, event):
        self.stop_interaction()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.stop_interaction()
        super().hideEvent(event)
