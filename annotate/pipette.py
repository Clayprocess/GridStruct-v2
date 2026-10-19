# 取色器：在画布上取色，显示放大预览和颜色值，点击复制。
# PixPin 行为：悬停显示取色放大镜，点击复制颜色代码。

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPixmap, QColor, QPainter, QPen, QFont, QFontMetrics, QGuiApplication
from PySide6.QtCore import Qt, QPoint, QRect, QRectF
from styles import tokens as T
from settings import get_settings
from .color_dialog import COLOR_FORMATS, format_color

_PAD = 6
_SWATCH = 10


class PipettePopup(QWidget):
    """跟随鼠标的取色放大镜：放大图、颜色值和快捷键提示画在同一张紧凑卡片上，没有多余留白。"""

    def __init__(self, pixmap: QPixmap, grid: bool = False, hint: str = ""):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._pm = pixmap
        self._image = pixmap.toImage()
        self._grid = grid  # 放大镜模式：画像素网格线
        self._hint = hint
        # 奇数格保证正中是被取的那个像素。
        self._cells = 11 if grid else 9
        self._crop = None
        self._center = QPoint()
        self._color = QColor(T.ANNOTATION_COLORS[1])
        self._format = get_settings().value("color_format", COLOR_FORMATS[0])
        self._font = QFont(self.font())
        self._font.setPixelSize(T.FONT_SM)
        metrics = QFontMetrics(self._font)
        # 按最长的格式定宽：切换格式或颜色时卡片不伸缩、不跳动。
        widest = max(metrics.horizontalAdvance(format_color(QColor(255, 255, 255), f)) for f in COLOR_FORMATS)
        widest = max(widest, metrics.horizontalAdvance(format_color(QColor(0, 255, 255), "HSV")))
        self._line = metrics.height()
        # 放大图铺满卡片内宽，下面的文字行也正好放下，不留空边。
        self._zoom = max(11, -(-(_SWATCH + _PAD + widest) // self._cells))
        preview = self._cells * self._zoom
        width = preview + 2 * _PAD
        self.setFixedSize(width, _PAD + preview + _PAD + (2 if hint else 1) * self._line + _PAD)

    def pick(self, scene_pos: QPoint, cursor_global: QPoint):
        """scene_pos 为逻辑坐标。返回 QColor。"""
        from capture import ScreenManager
        pixel = ScreenManager.image_point_to_physical(scene_pos, self._pm)
        img = self._image
        px = max(0, min(img.width() - 1, pixel.x()))
        py = max(0, min(img.height() - 1, pixel.y()))
        self._color = QColor(img.pixel(px, py))
        half = self._cells // 2
        x0 = max(0, min(img.width() - self._cells, px - half))
        y0 = max(0, min(img.height() - self._cells, py - half))
        self._crop = img.copy(x0, y0, self._cells, self._cells)
        self._center = QPoint(px - x0, py - y0)
        self.update()

        # 默认在鼠标右下，靠屏幕边缘时翻到另一侧，不盖住鼠标。
        geo = QGuiApplication.screenAt(cursor_global).availableGeometry()
        x = cursor_global.x() + 16
        if x + self.width() > geo.right():
            x = cursor_global.x() - 16 - self.width()
        y = cursor_global.y() + 20
        if y + self.height() > geo.bottom():
            y = cursor_global.y() - 20 - self.height()
        self.move(x, y)
        if not self.isVisible():
            self.show()
        return QColor(self._color)

    def paintEvent(self, event):
        if self._crop is None:
            return
        preview = self._cells * self._zoom
        target = QRect(_PAD, _PAD, preview, preview)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*T.CAPTURE_TIP))
            p.drawRoundedRect(QRectF(self.rect()), T.RADIUS_LG, T.RADIUS_LG)
            # 关掉平滑缩放，放大后每个像素是清晰的方块（高 DPI 下也按设备像素取最近邻）。
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(target, self._crop)
            if self._grid:
                p.setPen(QPen(QColor(*T.PIXEL_GRID), 0))
                for i in range(1, self._cells):
                    offset = i * self._zoom
                    p.drawLine(target.left() + offset, target.top(), target.left() + offset, target.bottom())
                    p.drawLine(target.left(), target.top() + offset, target.right(), target.top() + offset)
            cell = QRect(target.left() + self._center.x() * self._zoom, target.top() + self._center.y() * self._zoom,
                         self._zoom, self._zoom)
            # 中心像素：外白内黑两道框，任何底色上都看得见。
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(T.DARK["foreground"]), 0))
            p.drawRect(cell.adjusted(-1, -1, 0, 0))
            p.setPen(QPen(QColor(T.DARK["background"]), 0))
            p.drawRect(cell.adjusted(0, 0, -1, -1))
            p.setRenderHint(QPainter.Antialiasing)
            p.setFont(self._font)
            row = QRect(_PAD, target.bottom() + 1 + _PAD, self.width() - 2 * _PAD, self._line)
            swatch = QRect(row.left(), row.center().y() - _SWATCH // 2 + 1, _SWATCH, _SWATCH)
            p.setBrush(self._color)
            p.setPen(QPen(QColor(*T.CAPTURE_GUIDE), 1))
            p.drawRoundedRect(swatch, 2, 2)
            p.setPen(QColor(T.DARK["foreground"]))
            p.drawText(row.adjusted(_SWATCH + _PAD, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter,
                       format_color(self._color, self._format))
            if self._hint:
                p.setPen(QColor(T.DARK["muted_foreground"]))
                p.drawText(row.translated(0, self._line), Qt.AlignLeft | Qt.AlignVCenter, self._hint)

    @property
    def color(self) -> QColor:
        return QColor(self._color)

    @property
    def color_text(self) -> str:
        return format_color(self._color, self._format)

    def set_color_format(self, color_format):
        self._format = color_format
        self.update()

    def cycle_color_format(self):
        self.set_color_format(COLOR_FORMATS[(COLOR_FORMATS.index(self._format) + 1) % len(COLOR_FORMATS)])
        get_settings().setValue("color_format", self._format)
        return self._format
