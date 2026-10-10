# 贴图窗口：无边框置顶，显示一张图。
# 交互（对标 PixPin）：左键拖拽移动，滚轮缩放，双击关闭，右键菜单。

from PySide6.QtWidgets import QWidget, QLabel, QMenu, QFileDialog
from PySide6.QtGui import QPixmap, QGuiApplication
from PySide6.QtCore import Qt

from styles import tokens as T
from ui.icons import tinted_icon

_MIN_SCALE = 0.2
_MAX_SCALE = 5.0


def _fg_icon(name: str):
    """按当前系统主题前景色染色的菜单图标。"""
    try:
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        dark = False
    color = T.THEMES["dark" if dark else "light"]["foreground"]
    return tinted_icon(name, color, 16)


class PinWindow(QWidget):
    def __init__(self, pixmap: QPixmap):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._base = pixmap
        self._scale = 1.0
        self._label = QLabel(self)
        self._label.setPixmap(pixmap)
        self._drag_off = None
        self.resize(pixmap.size())

    # ---------- 缩放 ----------

    def zoom(self, factor: float):
        self._scale = min(_MAX_SCALE, max(_MIN_SCALE, self._scale * factor))
        pm = self._base.scaled(
            self._base.size() * self._scale,
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._label.setPixmap(pm)
        self._label.resize(pm.size())
        self.resize(pm.size())

    def wheelEvent(self, e):
        self.zoom(1.1 if e.angleDelta().y() > 0 else 1 / 1.1)

    # ---------- 拖拽 / 双击 ----------

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_off = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.close()

    # ---------- 右键菜单 ----------

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        copy_a = menu.addAction(_fg_icon("copy"), "复制图片")
        save_a = menu.addAction(_fg_icon("save"), "保存图片")
        op_menu = menu.addMenu("透明度")
        op_group = {}
        for text, v in [("100%", 1.0), ("70%", 0.7), ("40%", 0.4)]:
            a = op_menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(abs(self.windowOpacity() - v) < 0.01)
            op_group[a] = v
        menu.addSeparator()
        close_a = menu.addAction(_fg_icon("x"), "关闭")

        chosen = menu.exec(e.globalPos())
        if chosen == copy_a:
            QGuiApplication.clipboard().setPixmap(self._base)
        elif chosen == save_a:
            self._save()
        elif chosen == close_a:
            self.close()
        elif chosen in op_group:
            self.setWindowOpacity(op_group[chosen])

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存贴图", "", "PNG (*.png);;JPG (*.jpg);;BMP (*.bmp)")
        if path:
            self._base.save(path)
