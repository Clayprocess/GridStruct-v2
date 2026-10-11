# 智能吸附：鼠标悬停时自动识别窗口/控件矩形。
#
# 逆向依据（PixPin 3.5.5.1, UiRegionDetector.dll）：
#   三级检测：UIA 元素矩形 -> Win32 窗口矩形 -> 有效性校验，
#   后台线程异步，结果经信号回传，带矩形缓存。
#
# 本实现：QThread 异步 + Windows 自带 UIAutomation COM，无额外包。
# 非 Windows 平台返回空（Linux 开发机不断）。

import sys
import time
from PySide6.QtCore import QObject, QThread, Signal, QRect, QPoint, Slot

_IS_WIN = sys.platform == "win32"


class _SnapWorker(QObject):
    done = Signal(int, QRect, bool)

    def __init__(self, only_window: bool, ignored_hwnd: int):
        super().__init__()
        self._only_window = only_window
        self._ignored_hwnd = ignored_hwnd
        self._uia = None
        self._uia_initialized = False

    @Slot(int, int, int)
    def query(self, request_id: int, x: int, y: int):
        rect = None
        if _IS_WIN:
            if not self._only_window:
                if not self._uia_initialized:
                    self._uia = _try_init_uia()
                    self._uia_initialized = True
                if self._uia is not None:
                    rect = _detect_uia(self._uia, x, y, self._ignored_hwnd)
            if rect is None:
                rect = _detect_win32(x, y, self._only_window, self._ignored_hwnd)
        self.done.emit(request_id, rect if rect is not None else QRect(), rect is not None)

    @Slot(bool)
    def set_only_window(self, enabled):
        self._only_window = enabled

    @Slot()
    def cleanup(self):
        if self._uia is not None:
            self._uia.close()
            self._uia = None


class RegionDetector(QObject):
    """异步检测窗口，regionDetected 返回虚拟桌面逻辑坐标，空矩形表示无目标。

    only_window=True 只吸附顶层窗口；ignored_hwnd 排除截图覆盖层。
    """

    regionDetected = Signal(QRect)

    def __init__(self, only_window: bool = True, ignored_hwnd: int = 0):
        super().__init__()
        self._cache: dict[tuple[int, int], tuple[float, QRect]] = {}
        self._requests = {}
        self._request_id = 0
        self._mode_revision = 0
        self._inflight = False
        self._queued = None
        self._stopping = False
        self._thread = QThread(self)
        self._worker = _SnapWorker(only_window, ignored_hwnd)
        self._worker.moveToThread(self._thread)
        self._worker.done.connect(self._on_done)
        self._thread.finished.connect(self._worker.cleanup)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()
        self._last = None

    def detect(self, pos: QPoint):
        """查询合并为一个在途请求和最后一个位置，不积压鼠标移动事件。"""
        from PySide6.QtGui import QGuiApplication

        key = (pos.x(), pos.y())
        if key == self._last:
            return
        self._last = key
        self._request_id += 1
        self._discard_queued()
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < 2.0:
            self.regionDetected.emit(QRect(hit[1]))
            return
        screen = QGuiApplication.screenAt(pos)
        if screen is None:
            self.regionDetected.emit(QRect())
            return
        origin = screen.geometry().topLeft()
        dpr = screen.devicePixelRatio()
        self._requests[self._request_id] = (key, origin, dpr, self._mode_revision)
        # Qt 的屏幕原点保留原生位置，屏幕内偏移才按 DPR 缩放。
        x = origin.x() + round((pos.x() - origin.x()) * dpr)
        y = origin.y() + round((pos.y() - origin.y()) * dpr)
        if self._inflight:
            self._queued = (self._request_id, x, y)
        else:
            self._dispatch(self._request_id, x, y)

    def _dispatch(self, request_id, x, y):
        from PySide6.QtCore import QMetaObject, Qt, Q_ARG
        self._inflight = True
        QMetaObject.invokeMethod(
            self._worker, "query", Qt.QueuedConnection,
            Q_ARG(int, request_id), Q_ARG(int, x), Q_ARG(int, y))

    def _discard_queued(self):
        if self._queued is not None:
            self._requests.pop(self._queued[0])
            self._queued = None

    @Slot(int, QRect, bool)
    def _on_done(self, request_id: int, rect: QRect, ok: bool):
        key, origin, dpr, revision = self._requests.pop(request_id)
        if not self._stopping and revision == self._mode_revision:
            if ok:
                rect = QRect(
                    origin.x() + round((rect.x() - origin.x()) / dpr),
                    origin.y() + round((rect.y() - origin.y()) / dpr),
                    round(rect.width() / dpr), round(rect.height() / dpr))
                self._cache[key] = (time.monotonic(), QRect(rect))
                if len(self._cache) > 256:
                    self._cache.pop(next(iter(self._cache)))
            if request_id == self._request_id:
                self.regionDetected.emit(rect if ok else QRect())
        self._inflight = False
        if self._queued is not None and not self._stopping:
            request, self._queued = self._queued, None
            self._dispatch(*request)

    def setIsOnlyWindow(self, enabled: bool):
        """切换检测范围并丢弃之前模式的缓存、迟到结果。"""
        from PySide6.QtCore import QMetaObject, Qt, Q_ARG
        self._request_id += 1
        self._mode_revision += 1
        self._discard_queued()
        self._last = None
        self._cache.clear()
        QMetaObject.invokeMethod(
            self._worker, "set_only_window", Qt.QueuedConnection, Q_ARG(bool, enabled))

    def shutdown(self):
        self._stopping = True
        self._discard_queued()
        self._thread.quit()
        self._thread.wait()


# ---------- Windows API 边界 ----------


def _win32_api():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    signatures = {
        "WindowFromPoint": ([wintypes.POINT], wintypes.HWND),
        "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        "IsWindow": ([wintypes.HWND], wintypes.BOOL),
        "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
        "IsHungAppWindow": ([wintypes.HWND], wintypes.BOOL),
        "GetWindowLongW": ([wintypes.HWND, ctypes.c_int], ctypes.c_long),
        "ScreenToClient": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        "ChildWindowFromPointEx": ([wintypes.HWND, wintypes.POINT, wintypes.UINT], wintypes.HWND),
        "SendMessageTimeoutW": ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                ctypes.POINTER(ctypes.c_size_t)], ctypes.c_ssize_t),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(user32, name)
        function.argtypes, function.restype = arguments, result
    return user32


def _window_at(user32, x, y, ignored_hwnd):
    import ctypes
    from ctypes import wintypes
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
    dwm.DwmGetWindowAttribute.restype = ctypes.c_long

    def usable(hwnd):
        if (not hwnd or hwnd == ignored_hwnd or not user32.IsWindow(hwnd)
                or not user32.IsWindowVisible(hwnd) or user32.IsHungAppWindow(hwnd)):
            return False
        exstyle = user32.GetWindowLongW(hwnd, -20)
        if exstyle & 0x80020 == 0x80020:
            return False
        cloaked = wintypes.DWORD()
        result = dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        return result != 0 or cloaked.value == 0

    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    root = user32.GetAncestor(hwnd, 2) if hwnd else None
    covered = root == ignored_hwnd
    target = root if usable(root) else None
    if target is None:
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL

        @enum_proc
        def visit(candidate, _):
            nonlocal target
            if not usable(candidate):
                return True
            rc = wintypes.RECT()
            if (user32.GetWindowRect(candidate, ctypes.byref(rc))
                    and rc.left <= x < rc.right and rc.top <= y < rc.bottom):
                target = candidate
                return False
            return True
        user32.EnumWindows(visit, 0)
    if target:
        result = ctypes.c_size_t()
        # 只发送无副作用的 WM_NULL，提前隔离尚未被系统标记 hung 的窗口。
        if not user32.SendMessageTimeoutW(target, 0, 0, 0, 2, 100, ctypes.byref(result)):
            return None, covered
    return target, covered or target != root


def _valid_rect(rect, x, y):
    return (8 <= rect.width() <= 10000 and 8 <= rect.height() <= 10000
            and rect.contains(x, y))


def _detect_win32(x: int, y: int, only_window: bool = True,
                  ignored_hwnd: int = 0) -> QRect | None:
    import ctypes
    from ctypes import wintypes
    user32 = _win32_api()
    target, _ = _window_at(user32, x, y, ignored_hwnd)
    if not target:
        return None
    if not only_window:
        while True:
            point = wintypes.POINT(x, y)
            if not user32.ScreenToClient(target, ctypes.byref(point)):
                return None
            child = user32.ChildWindowFromPointEx(target, point, 7)
            if not child or child == target:
                break
            if not user32.IsWindow(child) or user32.IsHungAppWindow(child):
                return None
            target = child
    rc = wintypes.RECT()
    if not user32.GetWindowRect(target, ctypes.byref(rc)):
        return None
    rect = QRect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
    return rect if _valid_rect(rect, x, y) else None


# ---------- UIAutomation COM（Windows 自带，ABI 来自 UIAutomationClient.h） ----------


class _UIAutomation:
    def __init__(self):
        import ctypes
        from uuid import UUID
        self._ctypes = ctypes
        self._ole32 = ctypes.OleDLL("ole32")
        self._ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._ole32.CoInitializeEx.restype = ctypes.c_long
        self._ole32.CoUninitialize.argtypes = []
        self._ole32.CoUninitialize.restype = None
        self._ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                                               ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self._ole32.CoCreateInstance.restype = ctypes.c_long
        self._automation = ctypes.c_void_p()
        self._walker = ctypes.c_void_p()
        self._ole32.CoInitializeEx(None, 0)
        clsid = (ctypes.c_byte * 16).from_buffer_copy(UUID("e22ad333-b25f-460c-83d0-0581107395c9").bytes_le)
        iid = (ctypes.c_byte * 16).from_buffer_copy(UUID("34723aff-0c9d-49d0-9896-7ab52df8cd8a").bytes_le)
        try:
            self._ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(self._automation))
            self._call(self._automation, 61, [ctypes.c_uint], 200)
            self._call(self._automation, 63, [ctypes.c_uint], 200)
            self._call(self._automation, 14, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(self._walker))
        except OSError:
            self.close()
            raise

    def _call(self, pointer, index, argtypes, *args):
        ctypes = self._ctypes
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])
        result = function(pointer, *args)
        if result < 0:
            raise OSError(f"UIAutomation HRESULT 0x{result & 0xffffffff:08x}")
        return result

    def _release(self, pointer):
        if pointer:
            self._call(pointer, 2, [])

    def close(self):
        self._release(self._walker)
        self._release(self._automation)
        self._ole32.CoUninitialize()

    def _rectangle(self, element, x, y):
        from ctypes import wintypes
        ctypes = self._ctypes
        rc = wintypes.RECT()
        self._call(element, 43, [ctypes.POINTER(wintypes.RECT)], ctypes.byref(rc))
        rect = QRect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
        if rect.width() >= 0 and rect.height() >= 0 and rect.isEmpty():
            # UIA 结构节点可以没有自己的可绘制区域，不能因此丢掉其子控件。
            return QRect()
        offscreen = wintypes.BOOL()
        self._call(element, 38, [ctypes.POINTER(wintypes.BOOL)], ctypes.byref(offscreen))
        if offscreen.value:
            return None
        return rect if _valid_rect(rect, x, y) else None

    def _relative(self, element, index):
        ctypes = self._ctypes
        found = ctypes.c_void_p()
        self._call(self._walker, index, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], element, ctypes.byref(found))
        return found

    def _descend(self, element, x, y, deadline):
        best = None
        child = self._relative(element, 4)
        while child:
            try:
                if time.monotonic() > deadline:
                    raise TimeoutError("UIAutomation 控件树查询超时")
                rect = self._rectangle(child, x, y)
                if rect is not None:
                    candidate = self._descend(child, x, y, deadline)
                    if candidate is None and not rect.isEmpty():
                        candidate = rect
                    # 兄弟节点的区域可能重叠；前面的整窗容器不能屏蔽后面的按钮。
                    if candidate is not None and (best is None or
                            candidate.width() * candidate.height() < best.width() * best.height()):
                        best = candidate
                next_child = self._relative(child, 6)
            finally:
                self._release(child)
            child = next_child
        return best

    def rectangle_at(self, x, y, target, covered):
        from ctypes import wintypes
        ctypes = self._ctypes
        element = ctypes.c_void_p()
        if covered:
            # 覆盖层遮住 ElementFromPoint，只在底层 HWND 的控件树内命中。
            self._call(self._automation, 6, [wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)], target, ctypes.byref(element))
        else:
            self._call(self._automation, 7, [wintypes.POINT, ctypes.POINTER(ctypes.c_void_p)],
                       wintypes.POINT(x, y), ctypes.byref(element))
        if not element:
            return None
        try:
            rect = self._rectangle(element, x, y)
            if covered and rect is not None:
                candidate = self._descend(element, x, y, time.monotonic() + 0.2)
                if candidate is not None:
                    return candidate
            return rect if rect is not None and not rect.isEmpty() else None
        finally:
            self._release(element)


def _try_init_uia():
    try:
        return _UIAutomation()
    except OSError:
        return None


def _detect_uia(uia, x: int, y: int, ignored_hwnd: int = 0) -> QRect | None:
    user32 = _win32_api()
    target, covered = _window_at(user32, x, y, ignored_hwnd)
    if not target:
        return None
    try:
        return uia.rectangle_at(x, y, target, covered)
    except OSError:
        # Provider 拒绝、消失或超时均回退到经过相同校验的 Win32 路径。
        return None
