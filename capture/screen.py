# 屏幕坐标与捕获由同一个快照管理，避免混合 DPI 时使用其他屏幕的缩放率。
import ctypes
import sys
import math
from dataclasses import dataclass

from PySide6.QtCore import (QAbstractNativeEventFilter, QMutex, QMutexLocker,
                            QObject, QPoint, QPointF, QRect, QRectF, QSize, QTimer, Signal)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget
from styles import tokens as T


def _monitor_rectangles():
    if sys.platform != "win32":
        return {}
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD),
                    ("device", wintypes.WCHAR * 32)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                                       wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                       wintypes.LPARAM)
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                          callback_type, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    rectangles = {}

    @callback_type
    def collect(handle, dc, rect, data):
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            bounds = info.monitor
            rectangles[info.device] = QRect(bounds.left, bounds.top,
                                            bounds.right - bounds.left,
                                            bounds.bottom - bounds.top)
        return True

    if not user32.EnumDisplayMonitors(None, None, collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return rectangles


class _ExplorerEvents(QAbstractNativeEventFilter):
    def __init__(self, manager):
        super().__init__()
        self._manager = manager
        from ctypes import wintypes
        register = ctypes.windll.user32.RegisterWindowMessageW
        register.argtypes = [wintypes.LPCWSTR]
        register.restype = wintypes.UINT
        self._message = register("TaskbarCreated")
        if not self._message:
            raise ctypes.WinError()

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == self._message:
            print("[GridStruct-V2] Explorer started detected, refresh screen manager", flush=True)
            QTimer.singleShot(0, self._manager.refresh)
        return False, 0


class ScreenManager(QObject):
    changed = Signal()
    _instance = None

    def __init__(self, app):
        super().__init__(app)
        self._mutex = QMutex()
        self.screens = ()
        self._records = {}
        self._signature = ()
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(self._screen_removed)
        for screen in app.screens():
            self._watch(screen)
        if sys.platform == "win32":
            # 广播只投递给顶层 HWND，未显示的接收窗口不会抢占截图焦点。
            self._receiver = QWidget()
            self._receiver.setWindowTitle("GridStruct-V2 screen events")
            self._receiver.winId()
            self._events = _ExplorerEvents(self)
            app.installNativeEventFilter(self._events)
        self.refresh()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls(QApplication.instance())
        return cls._instance

    def _watch(self, screen):
        screen.geometryChanged.connect(self.refresh)
        screen.logicalDotsPerInchChanged.connect(self.refresh)
        screen.physicalDotsPerInchChanged.connect(self.refresh)

    def _screen_added(self, screen):
        self._watch(screen)
        print(f"[GridStruct-V2] Screen added: {screen.name()}", flush=True)
        self.refresh()

    def _screen_removed(self, screen):
        print("[GridStruct-V2] Screen removed", flush=True)
        self.refresh()

    def refresh(self, *args):
        with QMutexLocker(self._mutex):
            screens = tuple(QApplication.screens())
            try:
                native = _monitor_rectangles()
            except OSError as exc:
                print(f"[GridStruct-V2] Native monitor enumeration failed, using Qt: {exc}", flush=True)
                native = {}
            records = {}
            signature = []
            for screen in screens:
                geometry = screen.geometry()
                dpr = screen.devicePixelRatio()
                physical = native.get(screen.name())
                if physical is None:
                    # Qt 6 的 name 可能是显示器型号；Windows 后端保留原生屏幕原点。
                    physical = next((rect for rect in native.values()
                                     if rect.topLeft() == geometry.topLeft()), None)
                if physical is None:
                    physical = QRect(geometry.topLeft(), QSize(round(geometry.width() * dpr),
                                                               round(geometry.height() * dpr)))
                records[screen] = (geometry, physical, dpr)
                signature.append((screen.name(), geometry.getRect(), physical.getRect(), dpr))
            changed = tuple(signature) != self._signature
            self.screens, self._records = screens, records
            self._signature = tuple(signature)
        if changed:
            print(f"[GridStruct-V2] Screen manager refreshed: {self._signature}", flush=True)
            self.changed.emit()

    def whole_rect(self):
        result = QRect()
        for logical, physical, dpr in self._records.values():
            result = result.united(logical)
        return result

    def capture_region(self, rect: QRect) -> QPixmap:
        for attempt in range(3):
            self.refresh()
            if not self.screens:
                raise RuntimeError("没有可用显示器，请连接显示器后重试截图")
            signature = self._signature
            frames = []
            for screen in self.screens:
                logical, physical, dpr = self._records[screen]
                intersection = rect.intersected(logical)
                if intersection.isEmpty():
                    continue
                local = intersection.translated(-logical.topLeft())
                start = QPoint(round(local.x() * dpr), round(local.y() * dpr))
                end = QPoint(round((local.x() + local.width()) * dpr),
                             round((local.y() + local.height()) * dpr))
                if intersection.right() == logical.right():
                    end.setX(physical.width())
                if intersection.bottom() == logical.bottom():
                    end.setY(physical.height())
                # Qt 对采集起点四舍五入，旧裁剪用 Python round；多取一圈以保留相同像素。
                x, y = max(0, local.x() - 1), max(0, local.y() - 1)
                origin = QPoint(math.floor(x * dpr + .5), math.floor(y * dpr + .5))
                width = math.ceil((end.x() - origin.x()) / dpr)
                height = math.ceil((end.y() - origin.y()) / dpr)
                native_width = math.floor(width * dpr + .5)
                native_height = math.floor(height * dpr + .5)
                # Qt 6 在任一尺寸为 -1 时不缩放 QSize；另一维须传物理尺寸。
                # 到屏幕边缘用 -1，避免 225% 等比例丢末列或越过原生屏幕边界。
                right_edge = origin.x() + native_width > physical.width()
                bottom_edge = origin.y() + native_height > physical.height()
                if right_edge or bottom_edge:
                    width = -1 if right_edge else native_width
                    height = -1 if bottom_edge else native_height
                    native_width = physical.width() - origin.x() if right_edge else native_width
                    native_height = physical.height() - origin.y() if bottom_edge else native_height
                pixmap = screen.grabWindow(0, x, y, width, height)
                if (pixmap.isNull() or pixmap.size() != QSize(native_width, native_height)
                        or screen.geometry() != logical or screen.devicePixelRatio() != dpr):
                    break
                pixels = QRect(start - origin, QSize(end.x() - start.x(), end.y() - start.y()))
                frames.append(ScreenFrame(screen, intersection, pixmap.copy(pixels),
                                          physical.topLeft() + start))
            else:
                self.refresh()
                if self._signature == signature:
                    return frames[0].pixmap if len(frames) == 1 else DesktopCapture(frames).crop(rect)
        raise RuntimeError("屏幕捕获尺寸或显示器布局持续变化，请重试截图")

    def screen_at(self, point):
        return next((screen for screen, (logical, physical, dpr) in self._records.items()
                     if logical.contains(point)), None)

    def physical_screen_at(self, point):
        return next((screen for screen, (logical, physical, dpr) in self._records.items()
                     if physical.contains(point)), None)

    def logical_to_physical(self, point, screen):
        logical, physical, dpr = self._records[screen]
        offset = point - logical.topLeft()
        return physical.topLeft() + QPoint(round(offset.x() * dpr), round(offset.y() * dpr))

    def physical_to_logical(self, point, screen):
        logical, physical, dpr = self._records[screen]
        offset = point - physical.topLeft()
        # 最后一列物理像素必须仍在屏幕内，round 会在高 DPI 下越过右/下边界。
        return logical.topLeft() + QPoint(math.floor(offset.x() / dpr), math.floor(offset.y() / dpr))

    def logical_rect_to_physical(self, rect, screen=None):
        if screen is None:
            result = QRect()
            for candidate, (logical, physical, dpr) in self._records.items():
                intersection = rect.intersected(logical)
                if not intersection.isEmpty():
                    result = result.united(self.logical_rect_to_physical(intersection, candidate))
            return result
        start = self.logical_to_physical(rect.topLeft(), screen)
        end = self.logical_to_physical(rect.topLeft() + QPoint(rect.width(), rect.height()), screen)
        return QRect(start, QSize(end.x() - start.x(), end.y() - start.y()))

    def physical_rect_to_logical(self, rect, screen=None):
        if screen is None:
            result = QRect()
            for candidate, (logical, physical, dpr) in self._records.items():
                intersection = rect.intersected(physical)
                if not intersection.isEmpty():
                    result = result.united(self.physical_rect_to_logical(intersection, candidate))
            return result
        # 矩形使用排他端点，不能复用光标点向下取整的规则。
        logical, physical, dpr = self._records[screen]
        offset = rect.topLeft() - physical.topLeft()
        start = logical.topLeft() + QPoint(round(offset.x() / dpr), round(offset.y() / dpr))
        end = logical.topLeft() + QPoint(round((offset.x() + rect.width()) / dpr),
                                        round((offset.y() + rect.height()) / dpr))
        return QRect(start, QSize(end.x() - start.x(), end.y() - start.y()))

    def cursor_position(self):
        from PySide6.QtGui import QCursor
        if sys.platform != "win32":
            return QCursor.pos()
        from ctypes import wintypes
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError()
        physical = QPoint(point.x, point.y)
        screen = self.physical_screen_at(physical)
        return self.physical_to_logical(physical, screen) if screen else QCursor.pos()

    @staticmethod
    def image_point_to_physical(point, pixmap):
        dpr = pixmap.devicePixelRatio()
        return QPoint(round(point.x() * dpr), round(point.y() * dpr))

    @staticmethod
    def image_rect_to_physical(rect, pixmap):
        start = ScreenManager.image_point_to_physical(rect.topLeft(), pixmap)
        end = ScreenManager.image_point_to_physical(QPointF(rect.topLeft()) + QPointF(rect.width(), rect.height()), pixmap)
        return QRect(start, QSize(end.x() - start.x(), end.y() - start.y()))


    @staticmethod
    def image_point_to_logical(point, pixmap):
        dpr = pixmap.devicePixelRatio()
        return QPointF(point.x() / dpr, point.y() / dpr)

    @staticmethod
    def image_rect_to_logical(rect, pixmap):
        start = ScreenManager.image_point_to_logical(rect.topLeft(), pixmap)
        end = ScreenManager.image_point_to_logical(QPointF(rect.topLeft()) + QPointF(rect.width(), rect.height()), pixmap)
        return QRectF(start, end)


@dataclass
class ScreenFrame:
    screen: object
    geometry: QRect
    pixmap: QPixmap
    physical_origin: QPoint


class DesktopCapture:
    def __init__(self, frames):
        self.frames = frames
        self.geometry = QRect()
        for frame in frames:
            self.geometry = self.geometry.united(frame.geometry)

    def _parts(self, rect):
        parts = []
        for frame in self.frames:
            intersection = rect.intersected(frame.geometry)
            if not intersection.isEmpty():
                local = intersection.translated(-frame.geometry.topLeft())
                pixels = ScreenManager.image_rect_to_physical(local, frame.pixmap)
                # Qt 的逻辑屏幕尺寸经过取整，整屏边界仍需保留原生最后一行/列。
                if intersection.right() == frame.geometry.right():
                    pixels.setRight(frame.pixmap.width() - 1)
                if intersection.bottom() == frame.geometry.bottom():
                    pixels.setBottom(frame.pixmap.height() - 1)
                parts.append((frame, pixels, frame.physical_origin + pixels.topLeft()))
        return parts

    def pixel_size(self, rect):
        parts = self._parts(rect)
        bounds = QRect()
        for frame, pixels, position in parts:
            bounds = bounds.united(QRect(position, pixels.size()))
        return bounds.size()

    def crop(self, rect):
        parts = self._parts(rect)
        if len(parts) == 1:
            frame, pixels, position = parts[0]
            return frame.pixmap.copy(pixels)
        bounds = QRect()
        for frame, pixels, position in parts:
            bounds = bounds.united(QRect(position, pixels.size()))
        result = QPixmap(bounds.size())
        result.fill(QColor(*T.CAPTURE_BACKGROUND))
        if not result.isNull():
            with QPainter(result) as painter:
                for frame, pixels, position in parts:
                    piece = frame.pixmap.copy(pixels)
                    # 跨屏输出没有单一 DPR；逐屏按原生物理像素拼接，留白优于重采样。
                    piece.setDevicePixelRatio(1.0)
                    painter.drawPixmap(position - bounds.topLeft(), piece)
        return result


def virtual_geometry():
    return ScreenManager.instance().whole_rect()


def capture_virtual_desktop():
    manager = ScreenManager.instance()
    for desktop_attempt in range(3):
        manager.refresh()
        if not manager.screens:
            raise RuntimeError("没有可用显示器，请连接显示器后重试截图")
        signature = manager._signature
        frames = []
        layout_changed = False
        for screen in manager.screens:
            for attempt in range(3):
                manager.refresh()
                if screen not in manager.screens:
                    layout_changed = True
                    break
                geometry = screen.geometry()
                dpr = screen.devicePixelRatio()
                pixmap = screen.grabWindow(0)
                # 例如 1920px / 225% 的逻辑宽取整为 853，反乘无法还原原生宽度。
                expected = manager._records[screen][1].size()
                if (not pixmap.isNull() and pixmap.size() == expected
                        and screen.geometry() == geometry and screen.devicePixelRatio() == dpr):
                    break
                if attempt < 2:
                    print(f"[GridStruct-V2] Capture size changed, retrying: {screen.name()} "
                          f"attempt={attempt + 1} actual={pixmap.size()} expected={expected}", flush=True)
            else:
                raise RuntimeError(f"屏幕 {screen.name()} 捕获尺寸持续变化，请重试截图")
            if layout_changed:
                break
            frames.append(ScreenFrame(screen, geometry, pixmap,
                          manager.logical_to_physical(geometry.topLeft(), screen)))
        manager.refresh()
        if not layout_changed and manager._signature == signature:
            return DesktopCapture(frames)
        print("[GridStruct-V2] Screen layout changed, retrying desktop capture", flush=True)
    raise RuntimeError("显示器布局持续变化，请重试截图")
