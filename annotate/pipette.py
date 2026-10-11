# 取色器：在画布上取色，显示放大预览 + HEX，点击复制。
# PixPin 行为：悬停显示取色放大镜，点击复制颜色代码。

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtGui import QPixmap, QColor, QPainter, QPen
from PySide6.QtCore import Qt, QPoint, Signal


class PipettePopup(QWidget):
    """跟随鼠标的取色放大镜。"""

    ZOOM = 6       # 放大倍数
    CELLS = 9      # 取 9x9 像素

    def __init__(self, pixmap: QPixmap, dpr: float, grid: bool = False):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._pm = pixmap
        self._dpr = dpr
        self._grid = grid  # 放大镜模式：画像素网格线
        self._color = QColor("#000000")

        self.setFixedSize(150, 190)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._view = QLabel()
        self._view.setFixedSize(134, 134)
        self._hex = QLabel("#000000")
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
        big = crop.scaled(self.CELLS * self.ZOOM, self.CELLS * self.ZOOM,
                          Qt.KeepAspectRatio, Qt.FastTransformation)
        with QPainter(big) as p:
            if self._grid:
                # 像素网格线（放大镜模式）
                p.setPen(QPen(QColor(0, 0, 0, 60), 1))
                for i in range(1, self.CELLS):
                    x = i * self.ZOOM
                    p.drawLine(x, 0, x, big.width())
                    p.drawLine(0, x, big.width(), x)
            # 中心十字标记
            c = big.width() // 2
            p.setPen(QPen(QColor("#ff0000"), 1))
            p.drawLine(c - 6, c, c + 6, c)
            p.drawLine(c, c - 6, c, c + 6)
        self._view.setPixmap(big)
        self._hex.setText(self._color.name().upper())

        # 跟随鼠标右下方
        self.move(cursor_global + QPoint(16, 20))
        if not self.isVisible():
            self.show()
        return QColor(self._color)

    @property
    def color(self) -> QColor:
        return QColor(self._color)
