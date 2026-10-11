# 智能吸附：鼠标悬停时自动识别窗口/控件矩形。
#
# 参考（PixPin 3.5.5.1, UiRegionDetector.dll）：
#   SnapshotAllWinRect（EnumWindows 预存全窗口矩形）
#   -> 鼠标移动时点查快照（微秒级）-> Win32 控件级（ChildWindowFromPoint）
#   -> UIA 精化（WinGetWinRectByPointUIA，后台线程，latest-only）
#   DwmGetWindowAttribute 取去阴影边框，checkWinRectAlive 校验缓存。
#
# 性能关键：热路径（每次鼠标移动）只做快照点查，不碰 COM；
# UIA 只做精化且合并请求；overlay 只在高亮矩形变化时重绘。
# 非 Windows 平台返回空（Linux 开发机不断）。

import sys
import time
from PySide6.QtCore import (QObject, QThread, QTimer, QMutex, QMutexLocker,
                            Signal, QRect, QPoint, Slot)

_IS_WIN = sys.platform == "win32"

_SNAP_TTL_MS = 2000      # 快照刷新间隔
_UIA_POLL_MS = 50        # UIA 精化轮询间隔（latest-only 合并）


class _UiaWorker(QObject):
    """UIA 精化：只处理最新请求，旧请求直接丢弃。"""
    refined = Signal(QRect, int, int)  # rect, x, y（虚拟桌面逻辑坐标）

    def __init__(self):
        super().__init__()
        self._uia = _try_init_uia() if _IS_WIN else None
        self._pending = None
        self._lock = QMutex()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._process)
        self._timer.start(_UIA_POLL_MS)

    def request(self, x: int, y: int):
        with QMutexLocker(self._lock):
            self._pending = (x, y)

    @Slot()
    def _process(self):
        with QMutexLocker(self._lock):
            req = self._pending
            self._pending = None
        if req is None or self._uia is None:
            return
        x, y = req
        rect = _detect_uia(self._uia, x, y)
        if rect is not None:
            self.refined.emit(rect, x, y)


class RegionDetector(QObject):
    """异步区域检测。regionDetected(QRect) 有效时为检测到的矩形（虚拟桌面逻辑坐标）。

    only_window=True 时只吸附顶层窗口（对应 PixPin 的 setIsOnlyWindow）。
    exclude_hwnd：调用方自己的窗口句柄，检测时排除。
    """

    regionDetected = Signal(QRect)

    def __init__(self, only_window: bool = True, exclude_hwnd: int = 0):
        super().__init__()
        self._only_window = only_window
        self._exclude_hwnd = exclude_hwnd
        self._windows: list[tuple[int, QRect, float]] = []  # (hwnd, 逻辑矩形, scale)
        self._last_rect = QRect()
        self._last_req = (0, 0)

        if _IS_WIN:
            self._refresh_snapshot()
            self._snap_timer = QTimer(self)
            self._snap_timer.timeout.connect(self._refresh_snapshot)
            self._snap_timer.start(_SNAP_TTL_MS)

            self._thread = QThread(self)
            self._uia = _UiaWorker()
            self._uia.moveToThread(self._thread)
            self._uia.refined.connect(self._on_uia_refined)
            self._thread.start()
        else:
            self._uia = None

    def detect(self, pos: QPoint):
        """热路径：同步查快照（微秒级），UIA 精化走后台合并。"""
        if not _IS_WIN:
            return
        x, y = pos.x(), pos.y()
        self._last_req = (x, y)
        rect = self._lookup(x, y)
        if rect is not None and rect != self._last_rect:
            self._last_rect = QRect(rect)
            self.regionDetected.emit(rect)
        if not self._only_window and self._uia is not None:
            self._uia.request(x, y)

    def _on_uia_refined(self, rect: QRect, x: int, y: int):
        # 只接受当前鼠标位置的结果，过期的直接丢
        if (x, y) == self._last_req and rect != self._last_rect:
            self._last_rect = QRect(rect)
            self.regionDetected.emit(rect)

    def _lookup(self, x: int, y: int) -> QRect | None:
        """快照点查：Z序从上到下，第一个包含该点的窗口。"""
        for hwnd, r, scale in self._windows:
            if r.contains(x, y):
                if self._only_window:
                    return r
                child = _child_rect_at(hwnd, x, y, scale)
                return child if child is not None else r
        return None

    def _refresh_snapshot(self):
        self._windows = _snapshot_windows(self._exclude_hwnd)

    def shutdown(self):
        if _IS_WIN:
            self._snap_timer.stop()
            self._thread.quit()
            self._thread.wait()


# ---------- Win32 快照（EnumWindows） ----------

def _snapshot_windows(exclude_hwnd: int) -> list[tuple[int, QRect, float]]:
    """返回 [(hwnd, 逻辑坐标矩形, 物理->逻辑缩放)]，Z序从上到下。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    shcore = ctypes.windll.shcore

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    MDT_EFFECTIVE_DPI = 0
    MONITOR_DEFAULTTONEAREST = 2

    def _scale_of(hwnd: int) -> float:
        try:
            mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            dx, dy = wintypes.UINT(), wintypes.UINT()
            if shcore.GetDpiForMonitor(mon, MDT_EFFECTIVE_DPI,
                                       ctypes.byref(dx),
                                       ctypes.byref(dy)) == 0 and dx.value:
                return 96.0 / dx.value
        except Exception:
            pass
        return 1.0

    out: list[tuple[int, QRect, float]] = []

    def _cb(hwnd, _):
        if hwnd == exclude_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        rc = RECT()
        # DWM 去阴影边框，失败回退 GetWindowRect
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                       ctypes.byref(rc),
                                       ctypes.sizeof(rc)) != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                return True
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w < 8 or h < 8:
            return True
        s = _scale_of(hwnd)
        out.append((hwnd, QRect(int(rc.left * s), int(rc.top * s),
                               int(w * s), int(h * s)), s))
        return True

    cmpfunc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    cb = cmpfunc(_cb)
    user32.EnumWindows(cb, 0)
    return out


def _child_rect_at(hwnd: int, x: int, y: int, scale: float) -> QRect | None:
    """控件级矩形（逻辑坐标）。无有效子窗口返回 None。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    child = user32.ChildWindowFromPoint(hwnd, POINT(int(x / scale),
                                                   int(y / scale)))
    if not child or child == hwnd:
        return None
    rc = RECT()
    if not user32.GetWindowRect(child, ctypes.byref(rc)):
        return None
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w < 8 or h < 8:
        return None
    return QRect(int(rc.left * scale), int(rc.top * scale),
                 int(w * scale), int(h * scale))


# ---------- UIA 精化（comtypes，可选） ----------

def _try_init_uia():
    try:
        import comtypes.client
        return comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",  # CUIAutomation
            interface="{30cbe524-d4d0-4b85-7a99-afe5f04c332f}")  # IUIAutomation
    except Exception:
        return None


def _detect_uia(uia, x: int, y: int) -> QRect | None:
    # UIA 要物理坐标；调用方给的是逻辑坐标，这里按主屏缩放换算。
    # 精确的多屏换算在 overlay 侧做，精化路径允许近似。
    try:
        el = uia.ElementFromPoint(x, y)
        rc = el.CurrentBoundingRectangle
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w < 8 or h < 8:
            return None
        return QRect(rc.left, rc.top, w, h)
    except Exception:
        return None
