# 智能吸附：鼠标悬停时自动识别窗口/控件矩形。
#
# 参考（PixPin 3.5.5.1, UiRegionDetector.dll）：
#   三级检测：Win32（WindowFromPoint/ChildWindowFromPoint + GetWindowRect）
#   -> UIA（CUIAutomation::ElementFromPoint + BoundingRectangle），
#   后台线程异步，结果经信号回传，带矩形缓存。
#
# 本实现：QThread 异步 + Win32 主路径 + UIA 可选增强（comtypes）。
# 非 Windows 平台返回空（Linux 开发机不断）。

import sys
import time
from PySide6.QtCore import QObject, QThread, Signal, QRect, QPoint, Slot

_IS_WIN = sys.platform == "win32"


class _SnapWorker(QObject):
    done = Signal(QRect, bool)  # rect, 是否有效

    def __init__(self):
        super().__init__()
        self._uia = None
        if _IS_WIN:
            self._uia = _try_init_uia()

    @Slot(int, int)
    def query(self, x: int, y: int):
        rect = _detect_uia(self._uia, x, y) if self._uia else None
        if rect is None and _IS_WIN:
            rect = _detect_win32(x, y)
        if rect is not None:
            self.done.emit(rect, True)
        else:
            self.done.emit(QRect(), False)


class RegionDetector(QObject):
    """异步区域检测。regionDetected(QRect) 有效时为检测到的矩形（虚拟桌面逻辑坐标）。

    only_window=True 时只吸附顶层窗口（对应 PixPin 的 setIsOnlyWindow）。
    """

    regionDetected = Signal(QRect)

    def __init__(self, only_window: bool = True):
        super().__init__()
        self._only_window = only_window
        self._cache: dict[tuple[int, int], tuple[float, QRect]] = {}
        self._thread = QThread(self)
        self._worker = _SnapWorker()
        self._worker.moveToThread(self._thread)
        self._worker.done.connect(self._on_done)
        self._thread.start()
        self._last = (0, 0)

    def detect(self, pos: QPoint):
        """请求检测（调用方做节流）。"""
        x, y = pos.x(), pos.y()
        if abs(x - self._last[0]) + abs(y - self._last[1]) < 4:
            return
        self._last = (x, y)
        key = (x // 16, y // 16)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < 2.0:
            self.regionDetected.emit(hit[1])
            return
        # 跨线程调用 worker.query
        from PySide6.QtCore import QMetaObject, Qt, Q_ARG
        QMetaObject.invokeMethod(
            self._worker, "query", Qt.QueuedConnection,
            Q_ARG(int, x), Q_ARG(int, y))

    def _on_done(self, rect: QRect, ok: bool):
        if ok:
            key = (self._last[0] // 16, self._last[1] // 16)
            self._cache[key] = (time.monotonic(), QRect(rect))
            if len(self._cache) > 256:
                self._cache.pop(next(iter(self._cache)))
            self.regionDetected.emit(rect)

    def shutdown(self):
        self._thread.quit()
        self._thread.wait()


# ---------- Win32 路径（ctypes，Windows only） ----------

def _detect_win32(x: int, y: int) -> QRect | None:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    pt = POINT(x, y)
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return None
    # 取子窗口（控件级）
    child = user32.ChildWindowFromPoint(hwnd, pt)
    target = child or hwnd
    rc = RECT()
    if not user32.GetWindowRect(target, ctypes.byref(rc)):
        return None
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w < 8 or h < 8 or w > 10000 or h > 10000:
        return None
    return QRect(rc.left, rc.top, w, h)


# ---------- UIA 路径（comtypes，可选） ----------

def _try_init_uia():
    try:
        import comtypes.client
        return comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",  # CUIAutomation
            interface="{30cbe524-d4d0-4b85-7a99-afe5f04c332f}")  # IUIAutomation
    except Exception:
        return None


def _detect_uia(uia, x: int, y: int) -> QRect | None:
    try:
        el = uia.ElementFromPoint(x, y)
        rc = el.CurrentBoundingRectangle
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w < 8 or h < 8:
            return None
        return QRect(rc.left, rc.top, w, h)
    except Exception:
        return None
