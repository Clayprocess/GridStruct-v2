# 全屏框选窗口：显示变暗的截图，橡皮筋选区。
#
# 交互：
#   按下拖拽      新建选区
#   选区内拖拽    移动选区
#   边缘/角拖拽   调整选区（8 手柄）
#   双击选区内    确认 -> regionSelected(QRect 逻辑坐标)
#   ESC           取消 -> cancelled
#   拖拽时显示实时尺寸 tooltip

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QCursor
from PySide6.QtCore import Qt, QRect, QPoint, Signal

_HANDLE = 8          # 手柄边长（逻辑像素）
_MIN_SIZE = 4        # 最小选区
_DIM = QColor(0, 0, 0, 110)   # 选区外变暗


class ScreenShotWindow(QWidget):
    regionSelected = Signal(QRect)
    cancelled = Signal()

    def __init__(self, screenshot: QPixmap, virtual_geo: QRect):
        super().__init__()
        self._shot = screenshot
        self._vg = virtual_geo
        self._sel = QRect()          # 选区（窗口逻辑坐标）
        self._drag_mode = None       # 'create' | 'move' | handle_index | None
        self._drag_start = QPoint()
        self._sel_start = QRect()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setGeometry(virtual_geo)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CrossCursor))

        # 智能吸附（异步，Windows 才有真数据）
        from .snap import RegionDetector
        self._snap = RegionDetector()
        self._snap_rect = QRect()
        self._snap.regionDetected.connect(self._on_snap)

    def _on_snap(self, rect: QRect):
        """吸附结果（虚拟桌面坐标）-> 本地坐标。仅在未框选时显示。"""
        if not self._sel.isNull() or self._drag_mode is not None:
            self._snap_rect = QRect()
        else:
            self._snap_rect = self._to_local(rect)
        self.update()

    def closeEvent(self, e):
        self._snap.shutdown()
        super().closeEvent(e)

    # ---------- 选区几何 ----------

    def _to_local(self, r: QRect) -> QRect:
        return r.translated(-self._vg.topLeft())

    def _handles(self):
        """8 手柄：返回 [(index, QRect)]，index 0..7 从左上顺时针。"""
        s = self._sel
        if s.isNull():
            return []
        x, y, w, h = s.x(), s.y(), s.width(), s.height()
        hw = _HANDLE // 2
        pts = [
            (x, y), (x + w // 2, y), (x + w, y),
            (x + w, y + h // 2), (x + w, y + h),
            (x + w // 2, y + h), (x, y + h), (x, y + h // 2),
        ]
        return [(i, QRect(px - hw, py - hw, _HANDLE, _HANDLE))
                for i, (px, py) in enumerate(pts)]

    def _hit_handle(self, p: QPoint):
        for i, r in self._handles():
            if r.contains(p):
                return i
        return None

    # ---------- 绘制 ----------

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._shot)

        # 选区外变暗
        if not self._sel.isNull():
            from PySide6.QtGui import QRegion
            region = QRegion(self.rect())
            region -= QRegion(self._sel)
            p.setClipRegion(region)
            p.fillRect(self.rect(), _DIM)
            p.setClipping(False)

            # 选区边框：白实线 + 外发光描边
            p.setPen(QPen(QColor("#3b82f6"), 2))
            p.drawRect(self._sel.adjusted(1, 1, -1, -1))
            p.setPen(QPen(QColor(255, 255, 255, 200), 1))
            p.drawRect(self._sel.adjusted(2, 2, -2, -2))

            # 手柄
            p.setBrush(QColor("#3b82f6"))
            p.setPen(Qt.NoPen)
            for _, r in self._handles():
                p.drawRect(r)

            # 尺寸提示
            self._draw_size_tip(p)
        else:
            p.fillRect(self.rect(), _DIM)
            # 智能吸附高亮（未框选时）
            if not self._snap_rect.isNull():
                p.setPen(QPen(QColor("#3b82f6"), 2))
                p.drawRect(self._snap_rect.adjusted(1, 1, -1, -1))

    def _draw_size_tip(self, p: QPainter):
        text = f"{self._sel.width()} × {self._sel.height()}"
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text) + 16
        th = fm.height() + 10
        # 跟随鼠标右下方，放不下就换边
        mp = self.mapFromGlobal(QCursor.pos())
        x = mp.x() + 16
        y = mp.y() + 20
        if x + tw > self.width():
            x = mp.x() - tw - 8
        if y + th > self.height():
            y = mp.y() - th - 8
        tip = QRect(x, y, tw, th)
        p.setBrush(QColor(9, 9, 11, 230))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(tip, 6, 6)
        p.setPen(QColor("#fafafa"))
        p.drawText(tip, Qt.AlignCenter, text)

    # ---------- 鼠标 ----------

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.pos()
        h = self._hit_handle(pos)
        if h is not None:
            self._drag_mode = h
        elif not self._sel.isNull() and self._sel.contains(pos):
            self._drag_mode = "move"
        elif (self._sel.isNull() and not self._snap_rect.isNull()
                and self._snap_rect.contains(pos)):
            # 点击吸附到的窗口：直接采用
            self._sel = QRect(self._snap_rect)
            self._snap_rect = QRect()
            self._drag_mode = None
            self.update()
            return
        else:
            self._drag_mode = "create"
            self._sel = QRect(pos, pos)
        self._drag_start = pos
        self._sel_start = QRect(self._sel)
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.pos()
        if self._drag_mode is None:
            # hover：手柄上换光标
            h = self._hit_handle(pos)
            if h is not None:
                cursors = [Qt.SizeFDiagCursor, Qt.SizeVerCursor,
                           Qt.SizeBDiagCursor, Qt.SizeHorCursor]
                self.setCursor(QCursor(cursors[(h // 2) % 4]))
            elif not self._sel.isNull() and self._sel.contains(pos):
                self.setCursor(QCursor(Qt.SizeAllCursor))
            else:
                self.setCursor(QCursor(Qt.CrossCursor))
                # 未框选时请求智能吸附（虚拟桌面坐标）
                self._snap.detect(pos + self._vg.topLeft())
            self.update()  # 尺寸提示跟随
            return

        dx, dy = pos.x() - self._drag_start.x(), pos.y() - self._drag_start.y()
        s = QRect(self._sel_start)

        if self._drag_mode == "create":
            self._sel = QRect(self._drag_start, pos).normalized()
        elif self._drag_mode == "move":
            self._sel = s.translated(dx, dy)
        else:
            self._resize_by_handle(s, self._drag_mode, dx, dy)

        # 钳制在窗口内
        self._sel = self._sel.intersected(self.rect())
        if self._sel.width() < _MIN_SIZE or self._sel.height() < _MIN_SIZE:
            pass
        self.update()

    def _resize_by_handle(self, s: QRect, h: int, dx: int, dy: int):
        x, y, w, hh = s.x(), s.y(), s.width(), s.height()
        # 0 左上 1 上 2 右上 3 右 4 右下 5 下 6 左下 7 左
        if h in (0, 6, 7):
            x += dx; w -= dx
        if h in (2, 3, 4):
            w += dx
        if h in (0, 1, 2):
            y += dy; hh -= dy
        if h in (4, 5, 6):
            hh += dy
        if w < _MIN_SIZE:
            w = _MIN_SIZE
        if hh < _MIN_SIZE:
            hh = _MIN_SIZE
        self._sel = QRect(x, y, w, hh).normalized()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_mode = None
            if self._sel.width() < _MIN_SIZE or self._sel.height() < _MIN_SIZE:
                self._sel = QRect()
            self.update()

    def mouseDoubleClickEvent(self, e):
        if (e.button() == Qt.LeftButton and not self._sel.isNull()
                and self._sel.contains(e.pos())):
            self.regionSelected.emit(QRect(self._sel))
            self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter) and not self._sel.isNull():
            self.regionSelected.emit(QRect(self._sel))
            self.close()
