# 每屏保留原生截图，控制器共享全局逻辑选区，避免混合 DPI 跨窗重采样。
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPainter, QPen, QColor, QCursor, QRegion
from PySide6.QtCore import (Qt, QRect, QPoint, QSize, Signal, QObject, QTimer, QEvent,
                           Property, QPropertyAnimation, QEasingCurve)
from styles import tokens as T
from ui import floating_window_type, ask_confirmation, show_toast
from settings import get_settings
from annotate import shortcut_tool
from .screen import ScreenManager, capture_virtual_desktop
from .snap import RegionDetector
from .size_editor import SizeEditor

_HANDLE = 8
_MIN_SIZE = 4
_DIM = QColor(*T.CAPTURE_MASK)


class ScreenShotWindow(QObject):
    regionSelected = Signal(QRect)
    selectionChanged = Signal(QRect)
    actionRequested = Signal(str)
    cancelled = Signal()
    pipettePicked = Signal(str)

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
        self._snap_rect = QRect()
        self._drag_mode = None
        self._drag_start = QPoint()
        self._pending_snap = QRect()
        self._selection_progress = 1.0
        self._selection_animation = QPropertyAnimation(self, b"selectionProgress", self)
        self._selection_animation.setDuration(120)
        self._selection_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._sel_start = QRect()
        self._pointer_pos = self._manager.cursor_position()
        self._windows = []
        self._keyboard_windows = []
        self._pipette_mode = False
        self._shift_alone = False
        self._size_editor = None
        self._build_windows()
        QApplication.instance().installEventFilter(self)
        self._manager.changed.connect(self._screens_changed)

    @property
    def capture(self):
        return self._shot

    def _get_selection_progress(self):
        return self._selection_progress

    def _set_selection_progress(self, value):
        self._selection_progress = value
        self.update(QRegion(self._sel))

    selectionProgress = Property(float, _get_selection_progress, _set_selection_progress)

    def _build_windows(self):
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
        self._selection_animation.stop()
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
        self._pending_snap = QRect()
        self._selection_animation.stop()
        self._selection_progress = 1.0
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
            dirty = self._border_region(self._snap_rect) | self._border_region(new_rect)
            self._snap_rect = QRect(new_rect)
            self.update(dirty)

    @staticmethod
    def _border_region(rect):
        if rect.isNull():
            return QRegion()
        return QRegion(rect.adjusted(-5, -5, 5, 5)) - QRegion(rect.adjusted(5, 5, -5, -5))

    def _selection_region(self, previous):
        # 内外遮罩随选区变化，异或区域也必须刷新，否则拖拽后会残留旧遮罩。
        changed = QRegion(previous).xored(QRegion(self._sel))
        return changed | self._border_region(previous) | self._border_region(self._sel)

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
        if self._pipette_mode:
            window = next(window for window in self._windows if window.frame.geometry.contains(pos))
            color = window.loupe.pick(pos - window.frame.geometry.topLeft(), pos)
            self._pipette_mode = False
            self.pipettePicked.emit(color.name())
            return
        previous, dirty = QRect(self._sel), self._hover_region() | self._border_region(self._snap_rect)
        if self._selection_progress < 1.0:
            # 中断淡入时，旧选区内部也要清除尚未褪完的遮罩。
            dirty |= QRegion(previous)
        self._selection_animation.stop()
        self._selection_progress = 1.0
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
        elif self._sel.isNull() and self._snap_rect.contains(pos):
            # 松开才区分单击采用和手工框选，避免按下时选区先跳到整个吸附框。
            self._pending_snap = QRect(self._snap_rect)
            self._drag_mode = None
        else:
            self._drag_mode = "create"
            self._sel = QRect(pos, pos)
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
        if self._pipette_mode:
            return
        if not self._pending_snap.isNull():
            if (pos - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                self.update(dirty | self._hover_region())
                return
            dirty |= self._border_region(self._snap_rect)
            self._pending_snap = QRect()
            self._snap_rect = QRect()
            self._drag_mode = "create"
        if self._drag_mode is None:
            handle = self._hit_handle(pos)
            if not self._sel.isNull() and self.size_tip()[0].contains(pos):
                cursor = Qt.PointingHandCursor
            elif handle is not None:
                cursor = [Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor][handle % 4]
            elif self._sel.contains(pos):
                cursor = Qt.SizeAllCursor
            else:
                cursor = Qt.CrossCursor
                if self._sel.isNull():
                    self._snap.detect(pos)
            for window in self._windows:
                window.setCursor(QCursor(cursor))
            self.update(dirty | self._hover_region())
            return
        dx, dy = pos.x() - self._drag_start.x(), pos.y() - self._drag_start.y()
        square = bool(event.modifiers() & Qt.ShiftModifier)
        if self._drag_mode == "create":
            if square:
                # 同 PixPin：按住 Shift 锁定 1:1，以按下点为锚点。
                side = max(abs(dx), abs(dy))
                pos = self._drag_start + QPoint(side if dx >= 0 else -side, side if dy >= 0 else -side)
            self._sel = QRect(self._drag_start, pos).normalized()
        elif self._drag_mode == "move":
            self._sel = self._sel_start.translated(dx, dy)
            self._clamp_position()
        else:
            self._resize_by_handle(self._sel_start, self._drag_mode, dx, dy)
            if square:
                self._square_by_handle(self._drag_mode)
        self._sel = self._sel.intersected(self._vg)
        self.update(dirty | self._selection_region(previous) | self._hover_region())
        self.selectionChanged.emit(QRect(self._sel))

    def mouse_release(self, event):
        if self._frozen or self._refreshing or event.button() != Qt.LeftButton:
            return
        if not self._pending_snap.isNull():
            pos = event.globalPosition().toPoint()
            if (pos - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                previous, dirty = QRect(self._sel), self._hover_region() | self._border_region(self._snap_rect)
                self._sel = QRect(self._pending_snap)
                self._pending_snap = QRect()
                self._snap_rect = QRect()
                self._pointer_pos = pos
                self.update(dirty | self._selection_region(previous) | self._hover_region())
                self.selectionChanged.emit(QRect(self._sel))
                self._selection_animation.setStartValue(0.0)
                self._selection_animation.setEndValue(1.0)
                self._selection_animation.start()
                return
            self.mouse_move(event)
        if self._drag_mode is None:
            return
        self.mouse_move(event)
        previous, dirty = QRect(self._sel), self._hover_region()
        self._drag_mode = None
        if self._sel.width() < _MIN_SIZE or self._sel.height() < _MIN_SIZE:
            self._sel = QRect()
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
        self._selection_animation.stop()
        self._selection_progress = 1.0
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
        if self._frozen or self._refreshing:
            return
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key_Escape:
            if self._pipette_mode:
                self._pipette_mode = False
                return
            if ask_confirmation(window, "退出截图", "确认退出截图？"):
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
        previous, dirty = QRect(self._sel), self._hover_region() | self._border_region(self._snap_rect)
        self._sel = rect.intersected(self._vg)
        self._snap_rect = QRect()
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
        if self._sel.isNull() and self._drag_mode is None and not self._pipette_mode:
            self._snap.step_level(1 if event.angleDelta().y() > 0 else -1)

    def start_pipette(self):
        self._pipette_mode = True
        for window in self._windows:
            window.setCursor(Qt.CrossCursor)
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


    def _square_by_handle(self, h: int):
        # 以拖动手柄对面的角或边为锚点补成正方形；拖边时以该边的长度为准。
        s = self._sel
        side = s.height() if h in (1, 5) else s.width() if h in (3, 7) else max(s.width(), s.height())
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
        self.loupe = PipettePopup(frame.pixmap, grid=True)

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
        selected = c._sel.translated(-offset)
        with QPainter(self) as painter:
            painter.drawPixmap(0, 0, self.frame.pixmap)
            if not c._sel.isNull():
                painter.setClipRegion(QRegion(self.rect()) - QRegion(selected))
                painter.fillRect(self.rect(), _DIM)
                painter.setClipping(False)
                if c._selection_progress < 1:
                    mask = QColor(_DIM)
                    mask.setAlphaF(mask.alphaF() * (1 - c._selection_progress))
                    painter.fillRect(selected, mask)
                painter.setPen(QPen(QColor(T.ANNOTATION_COLORS[3]), 2))
                painter.drawRect(selected.adjusted(1, 1, -1, -1))
                painter.setPen(QPen(QColor(*T.CAPTURE_GUIDE), 1))
                painter.drawRect(selected.adjusted(2, 2, -2, -2))
                painter.setBrush(QColor(T.ANNOTATION_COLORS[3]))
                painter.setPen(Qt.NoPen)
                if not c._frozen:
                    for _, handle in c._handles():
                        painter.drawRect(handle.translated(-offset))
                    tip, text = c.size_tip()
                    tip.translate(-offset)
                    if tip.intersects(self.rect()):
                        painter.setBrush(QColor(*T.CAPTURE_TIP))
                        painter.drawRoundedRect(tip, 6, 6)
                        painter.setPen(QColor(T.DARK["foreground"]))
                        painter.drawText(tip, Qt.AlignCenter, text)
            else:
                painter.fillRect(self.rect(), _DIM)
                if not c._snap_rect.isNull():
                    painter.setPen(QPen(QColor(T.ANNOTATION_COLORS[3]), 2))
                    painter.drawRect(c._snap_rect.translated(-offset).adjusted(1, 1, -1, -1))
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

    def keyPressEvent(self, event):
        self.controller.key_press(event, self)

    def wheelEvent(self, event):
        self.controller.wheel(event)
