"""界面动效：统一时长与缓动，跟随 Windows“动画效果”开关。"""

import sys

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

from styles import tokens as T


def motion_enabled():
    """Windows 设置里关掉“动画效果”的人通常对动效敏感，这时所有过渡直接跳到终态。"""
    if sys.platform != "win32":
        return True
    import ctypes
    enabled = ctypes.c_int(1)
    # SPI_GETCLIENTAREAANIMATION
    ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
    return bool(enabled.value)


def animation(target, prop, duration=T.MOTION_BASE):
    anim = QPropertyAnimation(target, prop, target)
    anim.setDuration(duration if motion_enabled() else 0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    return anim


def fade_in(widget, duration=T.MOTION_FAST):
    """控件内容整体淡入；结束后拆掉透明效果，避免子控件一直走离屏绘制。"""
    if not motion_enabled():
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = animation(effect, b"opacity", duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeleteWhenStopped)


class SelectionPill(QWidget):
    """选中态底块：在同一排按钮间滑动，代替 QSS :checked 的瞬间跳变。

    被跟踪的按钮要设 pill 属性，让 QSS 去掉它们自己的选中底色（见 styles/theme.py）。
    """

    def __init__(self, parent, fill_key, border_key):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._fill_key, self._border_key = fill_key, border_key
        self._target = None
        self._slide = animation(self, b"geometry")
        self.hide()
        # 父控件重新布局时按钮位置会变，直接贴到新位置，不播动画。
        parent.installEventFilter(self)

    def track(self, button):
        self._target = button
        if button is None:
            self._slide.stop()
            self.hide()
            return
        end = self._rect_of(button)
        if self.isHidden() or not motion_enabled():
            self.setGeometry(end)
            self.show()
        elif end != self.geometry():
            self._slide.stop()
            self._slide.setStartValue(self.geometry())
            self._slide.setEndValue(end)
            self._slide.start()
        self.lower()

    def _rect_of(self, button):
        return QRect(button.mapTo(self.parentWidget(), QPoint()), button.size())

    def eventFilter(self, watched, event):
        if self._target is not None and event.type() in (QEvent.LayoutRequest, QEvent.Resize, QEvent.Show):
            if self._slide.state() != QPropertyAnimation.Running:
                self.setGeometry(self._rect_of(self._target))
        return False

    def paintEvent(self, _event):
        t = T.THEMES[QApplication.instance().property("theme_mode") or "light"]
        with QPainter(self) as p:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(t[self._border_key]), 1))
            p.setBrush(QColor(t[self._fill_key]))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), T.RADIUS_SM, T.RADIUS_SM)
