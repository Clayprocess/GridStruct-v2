# 取色器：在画布上取色，显示放大预览 + HEX，点击复制。
# PixPin 行为：悬停显示取色放大镜，点击复制颜色代码。

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtGui import QPixmap, QColor, QPainter, QPen
from PySide6.QtCore import Qt, QPoint, Signal
from styles import tokens as T


class PipettePopup(QWidget):
    """跟随鼠标的取色放大镜。"""

    ZOOM = 6       # 放大倍数
    CELLS = 9      # 取 9x9 像素

    def __init__(self, pixmap: QPixmap, dpr: float):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._pm = pixmap
        self._dpr = dpr
        self._color = QColor(T.ANNOTATION_COLORS[1])

        self.setFixedSize(150, 190)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._view = QLabel()
        self._view.setFixedSize(134, 134)
        self._hex = QLabel(self._color.name().upper())
        self._hex.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._view)
        layout.addWidget(self._hex)

    def pick(self, scene_pos: QPoint, cursor_global: QPoint):
        """scene_pos 为逻辑坐标。返回 QColor。"""
        px = int(scene_pos.x() * self._dpr)
        py = int(scene_pos.y() * self._dpr)
        img = self._pm.toImage()
        px = max(0, min(img.width() - 1, px))
        py = max(0, min(img.height() - 1, py))
        self._color = QColor(img.pixel(px, py))

        # 放大预览：以 (px,py) 为中心的 9x9 区域
        half = self.CELLS // 2
        x0 = max(0, min(img.width() - self.CELLS, px - half))
        y0 = max(0, min(img.height() - self.CELLS, py - half))
        crop = self._pm.copy(x0, y0, self.CELLS, self.CELLS)
        crop.setDevicePixelRatio(1.0)
        big = crop.scaled(self.CELLS * self.ZOOM, self.CELLS * self.ZOOM,
                          Qt.KeepAspectRatio, Qt.FastTransformation)
        with QPainter(big) as p:
            # 中心十字标记
            cx = int((px - x0 + 0.5) * self.ZOOM)
            cy = int((py - y0 + 0.5) * self.ZOOM)
            p.setPen(QPen(QColor(T.ANNOTATION_COLORS[0]), 1))
            p.drawRect(cx - 3, cy - 3, 6, 6)
        self._view.setPixmap(big)
        self._hex.setText(self._color.name().upper())

        # 跟随鼠标右下方
        from PySide6.QtGui import QGuiApplication
        geo = QGuiApplication.screenAt(cursor_global).availableGeometry()
        self.move(min(cursor_global.x() + 16, geo.right() - self.width()),
                  min(cursor_global.y() + 20, geo.bottom() - self.height()))
        if not self.isVisible():
            self.show()
        return QColor(self._color)

    @property
    def color(self) -> QColor:
        return QColor(self._color)
