# 取色器：在画布上取色，显示放大预览 + HEX，点击复制。
# PixPin 行为：悬停显示取色放大镜，点击复制颜色代码。

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtGui import QPixmap, QColor, QPainter, QPen
from PySide6.QtCore import Qt, QPoint
from styles import tokens as T
from settings import get_settings
from .color_dialog import format_color


class PipettePopup(QWidget):
    """跟随鼠标的取色放大镜。"""

    ZOOM = 6       # 放大倍数
    CELLS = 9      # 取 9x9 像素

    def __init__(self, pixmap: QPixmap, grid: bool = False):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._pm = pixmap
        self._grid = grid  # 放大镜模式：画像素网格线
        # 放大镜按承诺规格 11x11 @8x；取色器保持 9x9 @6x
        self._cells = 11 if grid else self.CELLS
        self._zoom = 8 if grid else self.ZOOM
        self._color = QColor(T.ANNOTATION_COLORS[1])
        self._format = get_settings().value("color_format", "Hex")

        self.setFixedSize(150, 190)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._view = QLabel()
        self._view.setFixedSize(134, 134)
        if grid:
            self._view.setAlignment(Qt.AlignCenter)
        self._hex = QLabel(self._color.name().upper())
        self._hex.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._view)
        layout.addWidget(self._hex)

    def pick(self, scene_pos: QPoint, cursor_global: QPoint):
        """scene_pos 为逻辑坐标。返回 QColor。"""
        from capture import ScreenManager
        pixel = ScreenManager.image_point_to_physical(scene_pos, self._pm)
        px, py = pixel.x(), pixel.y()
        img = self._pm.toImage()
        px = max(0, min(img.width() - 1, px))
        py = max(0, min(img.height() - 1, py))
        self._color = QColor(img.pixel(px, py))

        # 放大预览：以 (px,py) 为中心的取样区域
        half = self._cells // 2
        x0 = max(0, min(img.width() - self._cells, px - half))
        y0 = max(0, min(img.height() - self._cells, py - half))
        crop = self._pm.copy(x0, y0, self._cells, self._cells)
        crop.setDevicePixelRatio(1.0)
        big = crop.scaled(self._cells * self._zoom, self._cells * self._zoom,
                          Qt.KeepAspectRatio, Qt.FastTransformation)
        with QPainter(big) as p:
            if self._grid:
                # 像素网格线（放大镜模式）
                p.setPen(QPen(QColor(*T.PIXEL_GRID), 1))
                for i in range(1, self._cells):
                    x = i * self._zoom
                    p.drawLine(x, 0, x, big.width())
                    p.drawLine(0, x, big.width(), x)
            # 中心十字标记
            cx = int((px - x0 + 0.5) * self._zoom)
            cy = int((py - y0 + 0.5) * self._zoom)
            p.setPen(QPen(QColor(T.ANNOTATION_COLORS[0]), 1))
            p.drawRect(cx - 3, cy - 3, 6, 6)
        self._view.setPixmap(big)
        self._hex.setText(format_color(self._color, self._format))

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

    def set_color_format(self, color_format):
        self._format = color_format
        self._hex.setText(format_color(self._color, color_format))

    def cycle_color_format(self):
        formats = ("Hex", "RGB", "HSV")
        self.set_color_format(formats[(formats.index(self._format) + 1) % len(formats)])
        get_settings().setValue("color_format", self._format)
        return self._format
