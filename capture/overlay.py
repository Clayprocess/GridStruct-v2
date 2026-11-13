# 每屏保留原生截图，控制器共享全局逻辑选区，避免混合 DPI 跨窗重采样。
import time
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPainter, QPen, QColor, QCursor, QRegion
from PySide6.QtCore import Qt, QRect, QRectF, QPoint, QSize, Signal, QObject, QTimer, QEvent
from styles import tokens as T
from ui import floating_window_type, show_toast
from ui.motion import tween
from settings import get_settings
from annotate import shortcut_tool
from .screen import ScreenManager, capture_virtual_desktop
from .snap import RegionDetector
from .size_editor import SizeEditor

_HANDLE = T.CAPTURE_HANDLE_SIZE
# 手柄比画出来的圆点多 3 px 可点范围，和 V1 一致。
_HANDLE_SLOP = 3
_RESIZE_CURSORS = (Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor)
_MIN_SIZE = 4
# 滚轮切层级的防抖间隔（同 PixPin 50ms）。
_WHEEL_GAP_S = 0.05
_DIM = QColor(*T.CAPTURE_MASK)


class ScreenShotWindow(QObject):
    regionSelected = Signal(QRect)
    selectionChanged = Signal(QRect)
    actionRequested = Signal(str)
    cancelled = Signal()

    def __init__(self, screenshot, virtual_geo=None, keep_open=False):
        super().__init__()
        self._manager = ScreenManager.instance()
        self._shot = screenshot
        self._vg = screenshot.geometry
        self._keep_open = keep_open
        self._ended = False
        self._closed = False
        self._refreshing = False
        self._frozen = False
        self._guides = get_settings().value("capture_guides", True, type=bool)
        self._sel = QRect()
        # _snap_rect 是吸附目标，_snap_shown 是正在画的框：切换元素时由旧框滑到新框。
        self._snap_rect = QRect()
        self._snap_shown = QRect()
        self._snap_from = QRect()
        self._snap_anim = tween(self, T.MOTION_FAST)
        self._snap_anim.setStartValue(0.0)
        self._snap_anim.setEndValue(1.0)
        self._snap_anim.valueChanged.connect(self._snap_step)
        self._drag_mode = None
        self._drag_start = QPoint()
        self._wheel_at = 0.0  # 上一个滚轮事件的时刻，用于层级切换防抖
        self._pending_snap = QRect()
        self._sel_start = QRect()
        # Excel 按格拖选：表格区当前屏幕上的所有单元格（逻辑坐标）和按下时的那一格。
        # _grid 非空时选区跟着单元格网格走（拖选、拉边角），按住 Alt 自由框选。
        self._grid = []
        self._cell_anchor = QRect()
        self._pointer_pos = self._manager.cursor_position()
        self._windows = []
        self._keyboard_windows = []
        self._shift_alone = False
        self._size_editor = None
        self._build_windows()
        QApplication.instance().installEventFilter(self)
        self._manager.changed.connect(self._screens_changed)

    @property
    def capture(self):
        return self._shot

    def dragging(self):
        """正在拖出、移动或调整选区（按下后选区已经变化）。同 PixPin，这时不显示工具条。"""
        return self._drag_mode is not None and self._sel != self._sel_start

    def _build_windows(self):
        self._screen_region = QRegion()
        for frame in self._shot.frames:
            self._screen_region |= QRegion(frame.geometry)
        self._windows = [_ScreenOverlay(self, frame) for frame in self._shot.frames]
        self._snap = RegionDetector(
            ignored_hwnds={int(widget.winId()) for window in self._windows
                           for widget in (window, window.loupe)},
            only_window=get_settings().value("snap_only_window", False, type=bool))
        self._snap.regionDetected.connect(self._on_snap)

    def show(self):
        for window in self._windows:
            window.show()
        active = next((w for w in self._windows if w.frame.geometry.contains(self._pointer_pos)), self._windows[0])
        active.activateWindow()
        active.setFocus()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._snap_anim.stop()
        QApplication.instance().removeEventFilter(self)
        self._manager.changed.disconnect(self._screens_changed)
        self._snap.shutdown()
        for window in self._windows:
            window.loupe.close()
            window.close()
        self._windows.clear()
        if not self._ended:
            self._ended = True
            self.cancelled.emit()

    def _screens_changed(self):
        if self._closed or self._refreshing:
            return
        if self._frozen:
            self.close()
            self.cancelled.emit()
            return
        self._refreshing = True
        self._sel = QRect()
        self._grid = []
        self._pending_snap = QRect()
        self._snap_anim.stop()
        self.selectionChanged.emit(QRect())
        self._snap.shutdown()
        for window in self._windows:
            window.loupe.hide()
            window.hide()
        # 等窗口隐藏消息送达桌面合成器，下一次抓图才不会包含旧遮罩。
        QTimer.singleShot(100, self._refresh_capture)

    def _refresh_capture(self):
        if self._closed:
            return
        try:
            shot = capture_virtual_desktop()
        except (RuntimeError, OSError) as error:
            print(f"[capture] screen refresh failed: {error}")
            self.close()
            return
        for window in self._windows:
            window.loupe.close()
            window.close()
        self._shot = shot
        self._vg = shot.geometry
        self._sel = QRect()
        self._snap_rect = QRect()
        self._snap_shown = QRect()
        self._drag_mode = None
        self._frozen = False
        self._pointer_pos = self._manager.cursor_position()
        self._build_windows()
        self._refreshing = False
        self.selectionChanged.emit(QRect())
        self.show()

    def update(self, region=None):
        for window in self._windows:
            if region is None:
                window.update()
            else:
                local = region.translated(-window.frame.geometry.topLeft())
                window.update(local.intersected(QRegion(window.rect())))

    def route_keyboard(self, window):
        self._keyboard_windows.append(window)

    def eventFilter(self, watched, event):
        if (not self._frozen and not self._closed and isinstance(watched, QWidget)
                and watched.window() in self._windows + self._keyboard_windows):
            if event.type() == QEvent.KeyPress:
                # 同 PixPin 按 Shift 切换颜色格式；但 Shift 也用于 1:1、收边和切工具，
                # 所以只在单独按下又松开、中间没有别的键或拖拽时才切换。
                if event.key() == Qt.Key_Shift:
                    self._shift_alone = self._shift_alone or not event.isAutoRepeat()
                    return True
                self._shift_alone = False
                # Tab 在 QWidget.event 中先于 keyPressEvent 遍历焦点，必须在这里拦截。
                if event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
                    return True
                if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter,
                                   Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                                   Qt.Key_A, Qt.Key_C, Qt.Key_R, Qt.Key_S, Qt.Key_O) or event.modifiers() == Qt.ShiftModifier:
                    self.key_press(event, watched.window())
                    return True
            elif event.type() == QEvent.KeyRelease and event.key() == Qt.Key_Shift and not event.isAutoRepeat():
                if self._shift_alone:
                    self._shift_alone = False
                    color_format = self._windows[0].loupe.cycle_color_format()
                    for window in self._windows[1:]:
                        window.loupe.set_color_format(color_format)
                return True
        return super().eventFilter(watched, event)

    def _on_snap(self, rect):
        new_rect = QRect() if not self._sel.isNull() or self._drag_mode is not None else rect
        if new_rect != self._snap_rect:
            self._show_snap(new_rect, animate=True)

    def _show_snap(self, rect, animate=False):
        """同 PixPin 的选框切换动画：换元素时旧框滑到新框；出现、消失和按下时直接到位。"""
        self._snap_rect = QRect(rect)
        self._snap_anim.stop()
        if animate and not self._snap_shown.isNull() and not rect.isNull():
            self._snap_from = QRect(self._snap_shown)
            self._snap_anim.start()
        else:
            self._move_snap(rect)

    def _snap_step(self, t):
        a, b = self._snap_from, self._snap_rect
        self._move_snap(QRect(round(a.x() + (b.x() - a.x()) * t), round(a.y() + (b.y() - a.y()) * t),
                              round(a.width() + (b.width() - a.width()) * t),
                              round(a.height() + (b.height() - a.height()) * t)))

    def _move_snap(self, rect):
        # 吸附框内不压暗，框内外交换的区域都要重画。
        old, self._snap_shown = self._snap_shown, QRect(rect)
        self.update(QRegion(old).xored(QRegion(rect)) | self._border_region(old) | self._border_region(rect))

    @staticmethod
    def _border_region(rect):
        if rect.isNull():
            return QRegion()
        margin = _HANDLE // 2 + _HANDLE_SLOP + 2
        return QRegion(rect.adjusted(-margin, -margin, margin, margin)) - QRegion(rect.adjusted(margin, margin, -margin, -margin))

    def _selection_region(self, previous):
        # 内外遮罩随选区变化，异或区域也必须刷新，否则拖拽后会残留旧遮罩。
        changed = QRegion(previous).xored(QRegion(self._sel))
        return changed | self._border_region(previous) | self._border_region(self._sel)

    def _preview_border_region(self, rect):
        width = round(T.CAPTURE_PREVIEW_LINE_WIDTH)
        inner = QRegion(rect)
        border = QRegion(rect.adjusted(-width, -width, width, width)) - inner
        x, y, w, h = rect.getRect()
        # 只把落在真实屏幕覆盖之外的线段收回选区内；不能逐屏裁成矩形，
        # 否则跨屏选区会多出接缝，虚拟包围框则会漏掉错位屏幕和逻辑空洞的边。
        for strip, dx, dy in (
            (QRect(x - width, y - width, width, h + 2 * width), width, 0),
            (QRect(x + w, y - width, width, h + 2 * width), -width, 0),
            (QRect(x - width, y - width, w + 2 * width, width), 0, width),
            (QRect(x - width, y + h, w + 2 * width, width), 0, -width),
        ):
            border |= (QRegion(strip) - self._screen_region).translated(dx, dy) & inner
        return border & self._screen_region

    def _hover_region(self):
        region = QRegion()
        for window in self._windows:
            region |= window.hover_region()
        return region

    def _clamp_position(self):
        self._sel.moveLeft(max(self._vg.left(), min(self._sel.x(), self._vg.right() - self._sel.width() + 1)))
        self._sel.moveTop(max(self._vg.top(), min(self._sel.y(), self._vg.bottom() - self._sel.height() + 1)))

    def mouse_press(self, event):
        if self._frozen or self._refreshing or event.button() != Qt.LeftButton:
            return
        # 按下尚未跨屏抓取鼠标，事件坐标可保留队列中的起点而不会被最新光标覆盖。
        pos = event.globalPosition().toPoint()
        self._shift_alone = False
        previous, dirty = QRect(self._sel), self._hover_region()
        self._pending_snap = QRect()
        self._pointer_pos = pos
        if not self._sel.isNull() and self.size_tip()[0].contains(pos):
            self._edit_size()
            return
        handle = self._hit_handle(pos)
        if handle is not None:
            self._drag_mode = handle
        elif not self._sel.isNull() and self._sel.contains(pos):
            self._drag_mode = "move"
            self._set_cursor(Qt.ClosedHandCursor)
        elif self._sel.isNull() and self._snap_rect.contains(pos):
            # 松开才区分单击采用和手工框选，避免按下时选区先跳到整个吸附框。
            self._pending_snap = QRect(self._snap_rect)
            self._show_snap(self._snap_rect)
            self._drag_mode = None
            # 在 Excel 单元格上按下：拖动时按格选（同 Excel 拖选），按住 Alt 才自由框选。
            self._grid = [] if event.modifiers() & Qt.AltModifier else self._snap.cells_at(pos)
            self._cell_anchor = _nearest_cell(self._grid, pos) if self._grid else QRect()
        else:
            self._drag_mode = "create"
            self._grid = []
            self._sel = QRect(pos, pos)
            self._show_snap(QRect())
        self._drag_start = pos
        self._sel_start = QRect(self._sel)
        self.update(dirty | self._selection_region(previous) | self._hover_region())

    def mouse_move(self, event):
        if self._frozen or self._refreshing:
            return
        # Qt 跨屏 grab 仍可能沿用源屏 DPR；单屏优先保留每个事件的历史位置。
        pos = event.globalPosition().toPoint() if len(self._windows) == 1 else self._manager.cursor_position()
        previous = QRect(self._sel)
        dirty = self._hover_region()
        self._pointer_pos = pos
        for window in self._windows:
            if window.frame.geometry.contains(pos):
                window.loupe.pick(pos - window.frame.geometry.topLeft(), pos)
            else:
                window.loupe.hide()
        if not self._pending_snap.isNull():
            if (pos - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                self.update(dirty | self._hover_region())
                return
            self._pending_snap = QRect()
            self._show_snap(QRect())
            self._drag_mode = "cells" if self._grid else "create"
        if self._drag_mode is None:
            cursor = self._hover_cursor(pos)
            if cursor == Qt.CrossCursor and self._sel.isNull():
                self._snap.detect(pos)
            self._set_cursor(cursor)
            self.update(dirty | self._hover_region())
            return
        dx, dy = pos.x() - self._drag_start.x(), pos.y() - self._drag_start.y()
        square = bool(event.modifiers() & Qt.ShiftModifier)
        free = bool(event.modifiers() & Qt.AltModifier)
        if self._drag_mode == "cells" and not free:
            self._sel = _cell_range(self._grid, self._cell_anchor, _nearest_cell(self._grid, pos))
        elif self._drag_mode in ("create", "cells"):
            if square:
                # 同 PixPin：按住 Shift 锁定 1:1，以按下点为锚点。边长先按锚点到屏幕边缘的
                # 距离收住，否则末尾的 intersected 会把正方形裁成长方形。
                room_x = self._vg.right() - self._drag_start.x() if dx >= 0 else self._drag_start.x() - self._vg.left()
                room_y = self._vg.bottom() - self._drag_start.y() if dy >= 0 else self._drag_start.y() - self._vg.top()
                side = min(max(abs(dx), abs(dy)), room_x, room_y)
                pos = self._drag_start + QPoint(side if dx >= 0 else -side, side if dy >= 0 else -side)
            self._sel = QRect(self._drag_start, pos).normalized()
        elif self._drag_mode == "move":
            self._sel = self._sel_start.translated(dx, dy)
            self._clamp_position()
        else:
            self._resize_by_handle(self._sel_start, self._drag_mode, dx, dy)
            if square:
                self._square_by_handle(self._drag_mode)
            elif self._grid and not free:
                self._sel = _snap_edges(self._sel, self._grid, self._drag_mode)
        self._sel = self._sel.intersected(self._vg)
        self.update(dirty | self._selection_region(previous) | self._hover_region())
        self.selectionChanged.emit(QRect(self._sel))

    def mouse_release(self, event):
        if self._frozen or self._refreshing or event.button() != Qt.LeftButton:
            return
        if not self._pending_snap.isNull():
            pos = event.globalPosition().toPoint()
            if (pos - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                previous, dirty = QRect(self._sel), self._hover_region()
                self._sel = QRect(self._pending_snap)
                self._pending_snap = QRect()
                self._show_snap(QRect())
                self._pointer_pos = pos
                self.update(dirty | self._selection_region(previous) | self._hover_region())
                self.selectionChanged.emit(QRect(self._sel))
                return
            self.mouse_move(event)
        if self._drag_mode is None:
            return
        self.mouse_move(event)
        previous, dirty = QRect(self._sel), self._hover_region()
        if self._drag_mode == "cells" and event.modifiers() & Qt.AltModifier:
            self._grid = []  # 按住 Alt 松手的是自由选区，之后拉边角也不再对齐单元格
        self._drag_mode = None
        pos = event.globalPosition().toPoint() if len(self._windows) == 1 else self._manager.cursor_position()
        if self._sel.width() < _MIN_SIZE or self._sel.height() < _MIN_SIZE:
            self._sel = QRect()
            self._grid = []
            self._snap.redetect(pos)
        self._set_cursor(self._hover_cursor(pos))
        self.update(dirty | self._selection_region(previous) | self._hover_region())
        self.selectionChanged.emit(QRect(self._sel))

    def mouse_double_click(self, event):
        if not self._frozen and not self._refreshing and event.button() == Qt.LeftButton and self._sel.contains(self._manager.cursor_position()):
            self._accept_selection()

    def _accept_selection(self):
        if self._keep_open:
            self.actionRequested.emit("confirm")
        else:
            self.confirm()

    def confirm(self):
        if self._ended or self._closed or self._refreshing or self._sel.isEmpty():
            return False
        self._ended = True
        self._frozen = self._keep_open
        self._snap.shutdown()
        for window in self._windows:
            window.loupe.hide()
        self.update()
        get_settings().setValue("capture/last_region", QRect(self._sel))
        self.regionSelected.emit(QRect(self._sel))
        if not self._keep_open:
            self.close()
        return True

    def key_press(self, event, window):
        if self._frozen:
            # 标注时选区外遮罩仍能获得焦点，Esc 必须交给标注窗口走退出确认。
            if event.key() == Qt.Key_Escape:
                self.actionRequested.emit("escape")
            return
        if self._refreshing:
            return
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key_Escape:
            # 框选阶段还没有标注，退出不会丢东西，不打断。
            self.close()
        elif key in (Qt.Key_Return, Qt.Key_Enter) and not self._sel.isNull():
            self._accept_selection()
        elif self._keep_open and not self._sel.isNull() and modifiers & Qt.ControlModifier and key in (Qt.Key_C, Qt.Key_S):
            self.actionRequested.emit("copy" if key == Qt.Key_C else "save")
        elif key == Qt.Key_C and not modifiers:
            self._copy_color()
        elif key == Qt.Key_R and not modifiers:
            # 同 PixPin：R 复用上一次截图的选区。
            last = get_settings().value("capture/last_region", QRect())
            if last.intersects(self._vg):
                self._set_selection(last)
        elif key == Qt.Key_A and modifiers == Qt.ControlModifier:
            # 同 PixPin：Ctrl+A 选中鼠标所在屏幕，再按一次选中全部屏幕。
            screen = next(w.frame.geometry for w in self._windows if w.frame.geometry.contains(self._pointer_pos))
            self._set_selection(self._vg if self._sel == screen else screen)
        elif self._keep_open and not self._sel.isNull() and modifiers & Qt.ShiftModifier and key == Qt.Key_O:
            self.actionRequested.emit("ocr")
        elif self._keep_open and not self._sel.isNull() and shortcut_tool(event):
            self.actionRequested.emit(f"tool:{shortcut_tool(event)}")
        elif not self._sel.isNull() and key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            previous, dirty = QRect(self._sel), self._hover_region()
            # 同 PixPin：方向键移动 1px；Shift+方向键把对应那条边向内收 1px，Ctrl+方向键向外扩 1px。
            dx, dy = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}[key]
            if modifiers & (Qt.ShiftModifier | Qt.ControlModifier):
                sign = -1 if modifiers & Qt.ShiftModifier else 1
                edges = self._sel.adjusted(min(dx, 0) * sign, min(dy, 0) * sign, max(dx, 0) * sign, max(dy, 0) * sign)
                if edges.width() >= _MIN_SIZE and edges.height() >= _MIN_SIZE:
                    self._sel = edges.intersected(self._vg)
            else:
                self._sel.translate(dx, dy)
                self._clamp_position()
            self.selectionChanged.emit(QRect(self._sel))
            self.update(dirty | self._selection_region(previous) | self._hover_region())

    def _set_selection(self, rect):
        previous, dirty = QRect(self._sel), self._hover_region()
        self._sel = rect.intersected(self._vg)
        self._show_snap(QRect())
        self.update(dirty | self._selection_region(previous) | self._hover_region())
        self.selectionChanged.emit(QRect(self._sel))

    def size_tip(self):
        """尺寸提示的全局矩形和文字。"""
        size = self._shot.pixel_size(self._sel)
        text = f"{size.width()} × {size.height()} px"
        fm = self._windows[0].fontMetrics()
        width, height = fm.horizontalAdvance(text) + 16, fm.height() + 10
        # 同 PixPin：尺寸固定在选区左上角上方，才点得到；上方放不下就放进选区内侧。
        area = next((w.frame.geometry for w in self._windows if w.frame.geometry.contains(self._sel.topLeft())), self._vg)
        top = self._sel.top() - height - 6
        if top < area.top():
            top = self._sel.top() + 6
        left = min(self._sel.left(), area.right() - width + 1)
        return QRect(left, top, width, height), text

    def _edit_size(self):
        tip = self.size_tip()[0]
        self._size_editor = SizeEditor(self._shot.pixel_size(self._sel), self._resize_to)
        self._size_editor.move(tip.topLeft())
        self._size_editor.show()

    def _resize_to(self, pixels):
        # 输入的是物理像素，换回选区左上角所在屏幕的逻辑像素；左上角不动。
        frame = next(w.frame for w in self._windows if w.frame.geometry.contains(self._sel.topLeft()))
        dpr = frame.pixmap.width() / frame.geometry.width()
        size = QSize(max(_MIN_SIZE, round(pixels.width() / dpr)), max(_MIN_SIZE, round(pixels.height() / dpr)))
        self._set_selection(QRect(self._sel.topLeft(), size))

    def _copy_color(self):
        # 同 PixPin：按 C 按当前格式复制鼠标处的颜色并结束截图。
        window = next(w for w in self._windows if w.frame.geometry.contains(self._pointer_pos))
        window.loupe.pick(self._pointer_pos - window.frame.geometry.topLeft(), self._pointer_pos)
        text = window.loupe.color_text
        QApplication.clipboard().setText(text)
        self.close()
        show_toast(f"已复制 {text}")

    def wheel(self, event):
        # 同 PixPin：未框选时滚轮在吸附到的元素和它的父/子元素之间切换。
        # 触控板、无级滚轮滑一下会连发很多事件：距上一个滚轮事件不到 50ms 的不再切层，一次滑动只切一层。
        now = time.monotonic()
        quiet, self._wheel_at = now - self._wheel_at >= _WHEEL_GAP_S, now
        delta = event.angleDelta().y()
        if quiet and delta and self._sel.isNull() and self._drag_mode is None:
            self._snap.step_level(1 if delta > 0 else -1)

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
        skip = set()
        if w < T.CAPTURE_EDGE_HANDLE_MIN:
            skip |= {1, 5}
        if h < T.CAPTURE_EDGE_HANDLE_MIN:
            skip |= {3, 7}
        return [(i, QRect(px - hw, py - hw, _HANDLE, _HANDLE))
                for i, (px, py) in enumerate(pts) if i not in skip]

    def _hit_handle(self, p: QPoint):
        for i, r in self._handles():
            if r.adjusted(-_HANDLE_SLOP, -_HANDLE_SLOP, _HANDLE_SLOP, _HANDLE_SLOP).contains(p):
                return i
        return None

    def _set_cursor(self, cursor):
        for window in self._windows:
            window.setCursor(QCursor(cursor))

    def _hover_cursor(self, pos):
        handle = self._hit_handle(pos)
        if not self._sel.isNull() and self.size_tip()[0].contains(pos):
            return Qt.PointingHandCursor
        if handle is not None:
            return _RESIZE_CURSORS[handle % 4]
        if self._sel.contains(pos):
            # 同 V1：选区内是张开的手，按住拖动时握拳。
            return Qt.OpenHandCursor
        return Qt.CrossCursor


    def _square_by_handle(self, h: int):
        # 以拖动手柄对面的角或边为锚点补成正方形；拖边时以该边的长度为准。
        s = self._sel
        side = s.height() if h in (1, 5) else s.width() if h in (3, 7) else max(s.width(), s.height())
        # 同框选：按锚点到屏幕边缘的距离限边长，免得随后的裁剪破坏 1:1。
        side = min(side,
                   s.right() - self._vg.left() + 1 if h in (0, 6, 7) else self._vg.right() - s.left() + 1,
                   s.bottom() - self._vg.top() + 1 if h in (0, 1, 2) else self._vg.bottom() - s.top() + 1)
        left = s.right() - side + 1 if h in (0, 6, 7) else s.left()
        top = s.bottom() - side + 1 if h in (0, 1, 2) else s.top()
        self._sel = QRect(left, top, side, side)

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


def _nearest_cell(cells, pos):
    """含 pos 的单元格；拖出表格区时取离鼠标最近的那格（截图时画面冻结，不能滚动）。"""
    def distance(cell):
        dx = max(cell.left() - pos.x(), 0, pos.x() - cell.right())
        dy = max(cell.top() - pos.y(), 0, pos.y() - cell.bottom())
        return dx * dx + dy * dy
    return min(cells, key=distance)


def _cell_range(cells, first, last):
    """两格围成的整块区域；压到合并单元格的一部分时把整个合并格包进来（同 Excel）。"""
    rect, grown = first.united(last), True
    while grown:
        grown = False
        for cell in cells:
            part = cell.intersected(rect)
            # 只算真正压住的：相邻单元格的矩形可能共用一像素的表格线。
            if part.width() > 2 and part.height() > 2 and not rect.contains(cell):
                rect, grown = rect.united(cell), True
    return rect


def _snap_edges(sel, cells, handle):
    """拉边角时，被拉动的边落到最近的单元格边界上；拉出表格区范围的边不对齐。"""
    bounds = cells[0]
    for cell in cells[1:]:
        bounds = bounds.united(cell)

    def nearest(value, edges, low, high):
        return min(edges, key=lambda edge: abs(edge - value)) if low <= value <= high else value

    left, top, right, bottom = sel.left(), sel.top(), sel.right(), sel.bottom()
    # 0 左上 1 上 2 右上 3 右 4 右下 5 下 6 左下 7 左
    if handle in (0, 6, 7):
        left = nearest(left, {c.left() for c in cells}, bounds.left(), bounds.right())
    if handle in (2, 3, 4):
        right = nearest(right, {c.right() for c in cells}, bounds.left(), bounds.right())
    if handle in (0, 1, 2):
        top = nearest(top, {c.top() for c in cells}, bounds.top(), bounds.bottom())
    if handle in (4, 5, 6):
        bottom = nearest(bottom, {c.bottom() for c in cells}, bounds.top(), bounds.bottom())
    rect = QRect(QPoint(left, top), QPoint(right, bottom)).normalized()
    return _cell_range(cells, rect, rect) if rect.width() >= _MIN_SIZE and rect.height() >= _MIN_SIZE else sel


class _ScreenOverlay(QWidget):
    def __init__(self, controller, frame):
        super().__init__()
        self.controller = controller
        self.frame = frame
        self.setWindowTitle("GridStruct-V2 截图选区")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | floating_window_type())
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(QCursor(Qt.CrossCursor))
        self.setScreen(frame.screen)
        self.setGeometry(frame.geometry)
        from annotate import PipettePopup
        self.loupe = PipettePopup(frame.pixmap, hint="Shift: 切换颜色格式 · C: 复制颜色值",
                                  origin=frame.physical_origin)

    def hover_region(self):
        c = self.controller
        region = QRegion()
        if not c._sel.isNull():
            region |= QRegion(c.size_tip()[0].adjusted(-1, -1, 1, 1)).intersected(QRegion(self.frame.geometry))
        if not c._guides:
            return region
        geo = self.frame.geometry
        center = c._sel.center() if not c._sel.isNull() else c._pointer_pos
        x, y = center.x(), center.y()
        return region | (QRegion(QRect(x - 1, geo.top(), 3, geo.height())) | QRegion(QRect(geo.left(), y - 1, geo.width(), 3))).intersected(QRegion(geo))

    def paintEvent(self, event):
        c = self.controller
        offset = self.frame.geometry.topLeft()
        with QPainter(self) as painter:
            painter.drawPixmap(0, 0, self.frame.pixmap)
            # 同 V1/PixPin：选区和悬停吸附到的元素都不压暗。
            lit = (c._sel if not c._sel.isNull() else c._snap_shown).translated(-offset)
            painter.setClipRegion(QRegion(self.rect()) - QRegion(lit))
            painter.fillRect(self.rect(), _DIM)
            painter.setClipping(False)
            if not lit.isNull():
                # 同 V1：悬停吸附和拖出新选区时粗线画在外侧，不压住要截的内容；定下后细线收回内侧。
                outside = c._sel.isNull() or c._drag_mode == "create"
                if outside:
                    for part in c._preview_border_region(lit.translated(offset)).translated(-offset):
                        painter.fillRect(part, QColor(T.CAPTURE_ACCENT))
                else:
                    half = T.CAPTURE_LINE_WIDTH / 2
                    border = QPen(QColor(T.CAPTURE_ACCENT), T.CAPTURE_LINE_WIDTH)
                    border.setJoinStyle(Qt.MiterJoin)
                    painter.setRenderHint(QPainter.Antialiasing)
                    painter.setPen(border)
                    painter.drawRect(QRectF(lit).adjusted(half, half, -half, -half))
            if not c._sel.isNull() and not c._frozen:
                # 同 V1：拖动中隐藏手柄；尺寸提示照常显示，拖的时候正要看尺寸。
                if c._drag_mode is None:
                    painter.setPen(QPen(QColor(T.CAPTURE_HANDLE_RING), 2))
                    painter.setBrush(QColor(T.CAPTURE_ACCENT))
                    for _, handle in c._handles():
                        painter.drawEllipse(QRectF(handle.translated(-offset)).adjusted(1, 1, -1, -1))
                painter.setPen(Qt.NoPen)
                painter.setRenderHint(QPainter.Antialiasing, False)
                tip, text = c.size_tip()
                tip.translate(-offset)
                if tip.intersects(self.rect()):
                    painter.setBrush(QColor(*T.CAPTURE_TIP))
                    painter.drawRoundedRect(tip, 6, 6)
                    painter.setPen(QColor(T.DARK["foreground"]))
                    painter.drawText(tip, Qt.AlignCenter, text)
            if c._guides and not c._frozen:
                center = (c._sel.center() if not c._sel.isNull() else c._pointer_pos) - offset
                painter.setPen(QPen(QColor(*T.CAPTURE_GUIDE), 1, Qt.DashLine))
                painter.drawLine(center.x(), 0, center.x(), self.height())
                painter.drawLine(0, center.y(), self.width(), center.y())

    def closeEvent(self, event):
        if not self.controller._closed and not self.controller._refreshing:
            self.controller.close()
        super().closeEvent(event)

    def mousePressEvent(self, event):
        self.controller.mouse_press(event)

    def mouseMoveEvent(self, event):
        self.controller.mouse_move(event)

    def mouseReleaseEvent(self, event):
        self.controller.mouse_release(event)

    def mouseDoubleClickEvent(self, event):
        self.controller.mouse_double_click(event)

    def leaveEvent(self, event):
        # 鼠标移到工具条、工具菜单、选项面板等其他窗口上时，遮罩收不到移动事件，
        # 放大镜会停在离开前的位置；同 PixPin，离开遮罩就收起，回来时由 mouse_move 再显示。
        self.loupe.hide()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        self.controller.key_press(event, self)

    def wheelEvent(self, event):
        self.controller.wheel(event)
