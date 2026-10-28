# 取色器：在画布上取色，显示放大预览和颜色值，点击复制。
# PixPin 行为：悬停显示取色放大镜，点击复制颜色代码。

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPixmap, QColor, QPainter, QPainterPath, QPen, QFont, QFontMetrics, QGuiApplication
from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QSize
from styles import tokens as T
from settings import get_settings
from .color_dialog import COLOR_FORMATS, format_color

_PAD = 6
_SWATCH = 12
# 同 PixPin 默认放大镜：横向 15 × 纵向 7 个像素，每个像素放大 11 倍，预览铺满卡片顶部。
_COLUMNS, _ROWS, _ZOOM = 15, 7, 11
# 卡片在鼠标右下方的偏移（PixPin 实测 100%/150% 均为 +10, +20 逻辑像素）。
_OFFSET = QPoint(10, 20)


class PipettePopup(QWidget):
    """跟随鼠标的取色放大镜：上面是放大图，下面一行坐标、一行颜色，再是快捷键提示，全部居中。"""

    def __init__(self, pixmap: QPixmap, grid: bool = False, hint: str = "", origin: QPoint = QPoint()):
        """origin 是这张图左上角的全局物理坐标，用来显示鼠标所在像素的屏幕坐标；
        hint 中用 " · " 分隔的每一项单独一行。"""
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._pm = pixmap
        self._image = pixmap.toImage()
        self._grid = grid  # 放大镜模式：画像素网格线
        self._hints = [part for part in hint.split(" · ") if part]
        self._origin = QPoint(origin)
        self._crop = None
        self._center = QPoint()
        self._pixel = QPoint()
        self._color = QColor(T.ANNOTATION_COLORS[1])
        self._format = get_settings().value("color_format", COLOR_FORMATS[0])
        self._font = QFont(self.font())
        self._font.setPixelSize(T.FONT_SM)
        metrics = QFontMetrics(self._font)
        # 按最长的格式定宽：切换格式或颜色时卡片不伸缩、不跳动。
        widest = max(metrics.horizontalAdvance(format_color(QColor(255, 255, 255), f)) for f in COLOR_FORMATS)
        widest = max(widest, metrics.horizontalAdvance(format_color(QColor(0, 255, 255), "HSV")),
                     *(metrics.horizontalAdvance(text) for text in self._hints))
        self._line = metrics.height() + _PAD
        self._preview = QSize(_COLUMNS * _ZOOM, _ROWS * _ZOOM)
        width = max(self._preview.width(), _SWATCH + _PAD + widest + 2 * _PAD)
        self.setFixedSize(width, self._preview.height() + _PAD + (2 + len(self._hints)) * self._line + _PAD)

    def pick(self, scene_pos: QPoint, cursor_global: QPoint):
        """scene_pos 为逻辑坐标。返回 QColor。"""
        from capture import ScreenManager
        pixel = ScreenManager.image_point_to_physical(scene_pos, self._pm)
        img = self._image
        px = max(0, min(img.width() - 1, pixel.x()))
        py = max(0, min(img.height() - 1, pixel.y()))
        self._color = QColor(img.pixel(px, py))
        self._pixel = self._origin + QPoint(px, py)
        x0 = max(0, min(img.width() - _COLUMNS, px - _COLUMNS // 2))
        y0 = max(0, min(img.height() - _ROWS, py - _ROWS // 2))
        self._crop = img.copy(x0, y0, _COLUMNS, _ROWS)
        self._center = QPoint(px - x0, py - y0)
        self.update()

        # 默认在鼠标右下，靠屏幕边缘时翻到另一侧，不盖住鼠标。
        geo = QGuiApplication.screenAt(cursor_global).availableGeometry()
        x = cursor_global.x() + _OFFSET.x()
        if x + self.width() > geo.right():
            x = cursor_global.x() - _OFFSET.x() - self.width()
        y = cursor_global.y() + _OFFSET.y()
        if y + self.height() > geo.bottom():
            y = cursor_global.y() - _OFFSET.y() - self.height()
        self.move(x, y)
        if not self.isVisible():
            self.show()
        return QColor(self._color)

    def paintEvent(self, event):
        if self._crop is None:
            return
        card = QRectF(self.rect())
        target = QRect(QPoint((self.width() - self._preview.width()) // 2, 0), self._preview)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.Antialiasing)
            outline = QPainterPath()
            outline.addRoundedRect(card, T.RADIUS_LG, T.RADIUS_LG)
            p.setClipPath(outline)
            p.fillRect(card, QColor(*T.CAPTURE_TIP))
            # 关掉平滑缩放，放大后每个像素是清晰的方块（高 DPI 下也按设备像素取最近邻）。
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(target, self._crop)
            if self._grid:
                p.setPen(QPen(QColor(*T.PIXEL_GRID), 0))
                for i in range(1, _COLUMNS):
                    x = target.left() + i * _ZOOM
                    p.drawLine(x, target.top(), x, target.bottom())
                for i in range(1, _ROWS):
                    y = target.top() + i * _ZOOM
                    p.drawLine(target.left(), y, target.right(), y)
            cell = QRect(target.left() + self._center.x() * _ZOOM, target.top() + self._center.y() * _ZOOM,
                         _ZOOM, _ZOOM)
            # 中心像素：外白内黑两道框，任何底色上都看得见。
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(T.DARK["foreground"]), 0))
            p.drawRect(cell.adjusted(-1, -1, 0, 0))
            p.setPen(QPen(QColor(T.DARK["background"]), 0))
            p.drawRect(cell.adjusted(0, 0, -1, -1))
            p.setRenderHint(QPainter.Antialiasing)
            p.setFont(self._font)
            row = QRect(0, target.bottom() + 1 + _PAD // 2, self.width(), self._line)
            p.setPen(QColor(T.DARK["foreground"]))
            p.drawText(row, Qt.AlignCenter, f"({self._pixel.x()} , {self._pixel.y()})")
            row.translate(0, self._line)
            text = format_color(self._color, self._format)
            text_width = QFontMetrics(self._font).horizontalAdvance(text)
            left = (self.width() - _SWATCH - _PAD - text_width) // 2
            swatch = QRect(left, row.center().y() - _SWATCH // 2 + 1, _SWATCH, _SWATCH)
            p.setBrush(self._color)
            p.setPen(QPen(QColor(*T.CAPTURE_GUIDE), 1))
            p.drawRoundedRect(swatch, 2, 2)
            p.setPen(QColor(T.DARK["foreground"]))
            p.drawText(row.adjusted(left + _SWATCH + _PAD, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, text)
            p.setPen(QColor(T.DARK["muted_foreground"]))
            for hint in self._hints:
                row.translate(0, self._line)
                p.drawText(row, Qt.AlignCenter, hint)

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
