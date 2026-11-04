"""识别窗口左侧原图预览的缩放与拖动，文字识别和表格识别共用同一套手感。

滚轮以鼠标为中心缩放；左键按在空白处拖动图片，按在文字、格子上仍是原来的选字、选格，
这些地方按住空格或中键也能拖。打开时适应窗口，手动缩放后保持比例，窗口变大变小不再跟着变，
点右下角“适应窗口”或 Ctrl+0 回到适应。
预览由 target 提供：zoom_scale() / set_zoom_scale(s) / fit_zoom_scale(size)，
to_content(视口点) 与 from_content(内容点) 让缩放前后鼠标下的那一点不动，pans_at(视口点) 判断是不是空白处。
"""

from PySide6.QtCore import QEvent, QMargins, QObject, QPoint, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QToolButton

from styles import tokens as T
from .icons import tinted_icon

MIN_SCALE, MAX_SCALE = 0.1, 8.0
# 适应窗口时小图最多放大两倍，再大字形发虚，反而不如原大清楚。
FIT_MAX = 2.0
STEP = 1.25
_MARGIN = QMargins(T.SPACE_2, T.SPACE_2, T.SPACE_2, T.SPACE_2)


class PreviewZoom(QObject):
    def __init__(self, area, target, window):
        super().__init__(area)
        self._area = area
        self._target = target
        self._fit = True
        self._space = False
        self._press = None  # (上次位置, 控件, 原光标, 按键)
        self._panning = False
        area.installEventFilter(self)
        area.viewport().installEventFilter(self)
        self._bar = _ZoomBar(area, self)
        QShortcut(QKeySequence("Ctrl+0"), window, self.fit)

    def watch(self, widget):
        """视口里的画布自己收鼠标和键盘（如表格网格），要在画布上也接住。"""
        widget.installEventFilter(self)

    def stop_pan(self):
        """换画布前结束拖动，旧画布销毁后不能再恢复它的光标。"""
        if self._press is not None:
            _point, widget, cursor, _button = self._press
            widget.setCursor(cursor)
        self._press, self._panning = None, False

    @property
    def scale(self):
        return self._target.zoom_scale()

    @property
    def fitted(self):
        return self._fit

    def fit(self):
        self._fit = True
        size = self._area.viewport().size().shrunkBy(_MARGIN)
        self._target.set_zoom_scale(max(MIN_SCALE, min(FIT_MAX, self._target.fit_zoom_scale(size))))
        self._bar.update_state()

    def zoom_by(self, factor, anchor=None):
        self.set_scale(self.scale * factor, anchor)

    def set_scale(self, scale, anchor=None):
        self._fit = False
        anchor = self._area.viewport().rect().center() if anchor is None else anchor
        point = self._target.to_content(anchor)
        self._target.set_zoom_scale(max(MIN_SCALE, min(MAX_SCALE, scale)))
        # 缩放后把原来鼠标下的那一点挪回鼠标下。
        moved = self._target.from_content(point) - anchor
        self._scroll(round(moved.x()), round(moved.y()))
        self._bar.update_state()

    def _scroll(self, dx, dy):
        for bar, delta in ((self._area.horizontalScrollBar(), dx), (self._area.verticalScrollBar(), dy)):
            bar.setValue(bar.value() + delta)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Wheel and event.angleDelta().y():
            # 触控板的细分滚动按比例缩放，一格鼠标滚轮正好一档。
            anchor = self._area.viewport().mapFromGlobal(event.globalPosition().toPoint())
            self.zoom_by(STEP ** (event.angleDelta().y() / 120), anchor)
            return True
        if kind in (QEvent.KeyPress, QEvent.KeyRelease) and event.key() == Qt.Key_Space:
            if not event.isAutoRepeat():
                self._space = kind == QEvent.KeyPress
            return True
        if kind == QEvent.FocusOut:
            self._space = False
        if kind == QEvent.MouseButtonPress:
            return self._start_pan(watched, event)
        if kind == QEvent.MouseMove and self._press is not None:
            return self._move_pan(event)
        if kind == QEvent.MouseButtonRelease and self._press is not None and event.button() == self._press[3]:
            panned = self._panning
            self.stop_pan()
            return panned
        if kind == QEvent.Resize and watched is self._area.viewport():
            self._bar.place()
            if self._fit:
                self.fit()
        return False

    def _start_pan(self, widget, event):
        button = event.button()
        point = event.globalPosition().toPoint()
        forced = button == Qt.MiddleButton or (button == Qt.LeftButton and self._space)
        if not forced and not (button == Qt.LeftButton
                               and self._target.pans_at(self._area.viewport().mapFromGlobal(point))):
            return False
        self._press = (point, widget, widget.cursor(), button)
        self._panning = forced
        if forced:
            widget.setCursor(Qt.ClosedHandCursor)
        # 空白处左键先照常交给预览（单击仍能取消选字），拖过一段距离才算拖图。
        return forced

    def _move_pan(self, event):
        point = event.globalPosition().toPoint()
        last, widget, cursor, button = self._press
        delta = point - last
        if not self._panning:
            if delta.manhattanLength() < QApplication.startDragDistance():
                return False
            self._panning = True
            widget.setCursor(Qt.ClosedHandCursor)
        self._press = (point, widget, cursor, button)
        self._scroll(-delta.x(), -delta.y())
        return True


class _ZoomBar(QFrame):
    """预览右下角的缩放条：缩小、当前比例（点一下回到 100%）、放大、适应窗口。"""

    def __init__(self, area, zoom):
        super().__init__(area)
        self.setObjectName("ZoomBar")
        self._area = area
        self._zoom = zoom
        color = T.THEMES[QApplication.instance().property("theme_mode") or "light"]["foreground"]
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(0)

        def button(icon, tip, slot):
            widget = QToolButton()
            widget.setIcon(tinted_icon(icon, color, 14))
            widget.setToolTip(tip)
            widget.setAccessibleName(tip)
            widget.clicked.connect(slot)
            row.addWidget(widget)
            return widget

        button("minus", "缩小", lambda: zoom.zoom_by(1 / STEP))
        self._level = QToolButton()
        self._level.setObjectName("ZoomLevel")
        self._level.setToolTip("原始大小")
        self._level.clicked.connect(lambda: zoom.set_scale(1.0))
        row.addWidget(self._level)
        button("plus", "放大；也可在图上滚动滚轮", lambda: zoom.zoom_by(STEP))
        self._fit = button("maximize", "适应窗口 (Ctrl+0)", zoom.fit)
        self._fit.setCheckable(True)

    def update_state(self):
        self._level.setText(f"{round(self._zoom.scale * 100)}%")
        self._fit.setChecked(self._zoom.fitted)
        self.adjustSize()
        self.place()

    def place(self):
        viewport = self._area.viewport().geometry()
        self.move(viewport.bottomRight() - QPoint(self.width(), self.height()) - QPoint(T.SPACE_2, T.SPACE_2) + QPoint(1, 1))
        self.raise_()
