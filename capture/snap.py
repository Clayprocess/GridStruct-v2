# 智能吸附：鼠标悬停时自动识别窗口/控件矩形。
#
# 逆向依据（PixPin 3.5.5.1, UiRegionDetector.dll）：
#   三级检测：UIA 元素矩形 -> Win32 窗口矩形 -> 有效性校验，
#   后台线程异步，结果经信号回传，带矩形缓存。
#
# 本实现：窗口/控件快照同步点查 + 每窗口一次 QThread UIAutomation 精化。
# 非 Windows 平台返回空（Linux 开发机不断）。

import sys
import time
from PySide6.QtCore import QObject, QThread, QTimer, Signal, QRect, QPoint, Slot

_IS_WIN = sys.platform == "win32"


class _SnapWorker(QObject):
    done = Signal(int, object)

    def __init__(self):
        super().__init__()
        self._uia = None
        self._uia_initialized = False

    @Slot(int, "qulonglong")
    def query(self, request_id: int, hwnd: int):
        started = time.perf_counter()
        rects = []
        if _IS_WIN:
            if not self._uia_initialized:
                self._uia = _try_init_uia()
                self._uia_initialized = True
            if self._uia is not None:
                try:
                    rects = self._uia.window_rectangles(hwnd)
                except OSError as exc:
                    print(f"[GridStruct-V2] UIA 精化失败：{exc}", flush=True)
        detection_ms = (time.perf_counter() - started) * 1000
        print(f"[GridStruct-V2] UIA request={request_id} detectionMs={detection_ms:.2f} "
              f"rectangles={len(rects)}", flush=True)
        self.done.emit(request_id, rects)

    @Slot()
    def cleanup(self):
        if self._uia is not None:
            self._uia.close()
            self._uia = None


class RegionDetector(QObject):
    """同步命中窗口/控件快照，后台只收集 UIA 控件矩形。"""

    regionDetected = Signal(QRect)

    def __init__(self, only_window: bool = True, ignored_hwnd: int = 0):
        super().__init__()
        self._only_window = only_window
        self._cache = {}
        self._requests = {}
        self._request_id = 0
        self._mode_revision = 0
        self._inflight = False
        self._queued = None
        self._stopping = False
        self._ignored_hwnd = ignored_hwnd
        self._windows = []
        self._last_rect = QRect()
        self._last = None
        self._window_api = _win32_api() if _IS_WIN else None
        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.timeout.connect(self._refresh_snapshot)
        if _IS_WIN:
            self._refresh_snapshot()
            self._snapshot_timer.start(2000)
        self._thread = QThread(self)
        self._worker = _SnapWorker()
        self._worker.moveToThread(self._thread)
        self._worker.done.connect(self._on_done)
        self._thread.finished.connect(self._worker.cleanup)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def detect(self, pos: QPoint):
        from PySide6.QtGui import QGuiApplication

        if self._stopping:
            return
        self._last = QPoint(pos)
        screen = QGuiApplication.screenAt(pos)
        if screen is None:
            self._discard_queued()
            self._publish(QRect())
            return
        origin, dpr = screen.geometry().topLeft(), screen.devicePixelRatio()
        # Qt 保留屏幕原生原点，只有屏幕内偏移乘 DPR。
        x = origin.x() + round((pos.x() - origin.x()) * dpr)
        y = origin.y() + round((pos.y() - origin.y()) * dpr)
        hit = self._lookup(x, y)
        if hit is None:
            self._discard_queued()
            self._publish(QRect())
            return
        hwnd, window_rect = hit
        key = (hwnd, window_rect.getRect())
        rect = window_rect
        if not self._only_window:
            for candidate in self._cache.get(key, ()):
                if candidate.contains(x, y):
                    rect = candidate
                    break
        self._publish(_logical_rect(rect, origin, dpr))
        # 冻结截图内按窗口收集一次控件，按钮之间移动也无需再跨 COM。
        if self._queued is not None and self._requests[self._queued[0]][0] != key:
            self._discard_queued()
        if (self._only_window or key in self._cache or
                any(request[0] == key for request in self._requests.values())):
            return
        self._request_id += 1
        self._requests[self._request_id] = (key, self._mode_revision)
        request = (self._request_id, hwnd)
        if self._inflight:
            self._queued = request
        else:
            self._dispatch(*request)

    def _publish(self, rect):
        if rect != self._last_rect:
            self._last_rect = QRect(rect)
            self.regionDetected.emit(QRect(rect))

    def _lookup(self, x, y):
        for hwnd, rect in self._windows:
            if (rect.contains(x, y) and self._window_api.IsWindow(hwnd)
                    and self._window_api.IsWindowVisible(hwnd)):
                return hwnd, rect
        return None

    def _refresh_snapshot(self):
        self._windows = _snapshot_windows(self._ignored_hwnd)
        alive = {(hwnd, rect.getRect()) for hwnd, rect in self._windows}
        self._cache = {key: rects for key, rects in self._cache.items() if key in alive}
        if self._last is not None:
            self.detect(self._last)

    def _dispatch(self, request_id, hwnd):
        from PySide6.QtCore import QMetaObject, Qt, Q_ARG
        self._inflight = True
        # HWND 是指针宽度，不能放进 Qt 的 32 位 int 参数。
        QMetaObject.invokeMethod(
            self._worker, "query", Qt.QueuedConnection,
            Q_ARG(int, request_id), Q_ARG("qulonglong", hwnd))

    def _discard_queued(self):
        if self._queued is not None:
            self._requests.pop(self._queued[0])
            self._queued = None

    @Slot(int, object)
    def _on_done(self, request_id, rects):
        key, revision = self._requests.pop(request_id)
        alive = any((hwnd, rect.getRect()) == key for hwnd, rect in self._windows)
        if not self._stopping and revision == self._mode_revision and alive:
            window_rect = QRect(*key[1])
            unique = {r.intersected(window_rect).getRect() for r in rects}
            self._cache[key] = sorted(
                (QRect(*r) for r in unique if r[2] >= 8 and r[3] >= 8),
                key=lambda r: r.width() * r.height())
        self._inflight = False
        if self._queued is not None and not self._stopping:
            request, self._queued = self._queued, None
            self._dispatch(*request)
        if not self._stopping and self._last is not None:
            self.detect(self._last)

    def setIsOnlyWindow(self, enabled: bool):
        self._only_window = enabled
        self._mode_revision += 1
        self._discard_queued()
        self._cache.clear()
        if self._last is not None:
            self.detect(self._last)

    def shutdown(self):
        self._stopping = True
        self._snapshot_timer.stop()
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


def _logical_rect(rect, origin, dpr):
    return QRect(origin.x() + round((rect.x() - origin.x()) / dpr),
                 origin.y() + round((rect.y() - origin.y()) / dpr),
                 round(rect.width() / dpr), round(rect.height() / dpr))


def _dwm_api():
    import ctypes
    from ctypes import wintypes
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
    dwm.DwmGetWindowAttribute.restype = ctypes.c_long
    return dwm


def _usable_window(user32, dwm, hwnd, ignored_hwnd):
    import ctypes
    from ctypes import wintypes
    if (not hwnd or hwnd == ignored_hwnd or not user32.IsWindow(hwnd)
            or not user32.IsWindowVisible(hwnd) or user32.IsHungAppWindow(hwnd)):
        return False
    exstyle = user32.GetWindowLongW(hwnd, -20)
    if exstyle & 0x80020 == 0x80020:
        return False
    cloaked = wintypes.DWORD()
    result = dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return result != 0 or cloaked.value == 0


def _snapshot_windows(ignored_hwnd):
    """按 Z 序保存物理矩形；查询点和结果统一走 Qt 屏幕原点/DPR 转换。"""
    import ctypes
    from ctypes import wintypes
    user32, dwm = _win32_api(), _dwm_api()
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    windows = []

    @enum_proc
    def visit(hwnd, _):
        if not _usable_window(user32, dwm, hwnd, ignored_hwnd):
            return True
        rc = wintypes.RECT()
        if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rc), ctypes.sizeof(rc)) != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                return True
        rect = QRect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
        if _valid_rect(rect, rc.left, rc.top):
            windows.append((hwnd, rect))
        return True

    user32.EnumWindows(visit, 0)
    return windows


def _window_at(user32, x, y, ignored_hwnd):
    import ctypes
    from ctypes import wintypes
    dwm = _dwm_api()
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    root = user32.GetAncestor(hwnd, 2) if hwnd else None
    covered = root == ignored_hwnd
    target = root if _usable_window(user32, dwm, root, ignored_hwnd) else None
    if target is None:
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL

        @enum_proc
        def visit(candidate, _):
            nonlocal target
            if not _usable_window(user32, dwm, candidate, ignored_hwnd):
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
    # Windows build 26300 x64 实测：IUIAutomation2 61/63/14/6/7、element 43/38、walker 4/6、Release 2。
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

    def window_rectangles(self, hwnd):
        from ctypes import wintypes
        ctypes = self._ctypes
        root = ctypes.c_void_p()
        self._call(self._automation, 6, [wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)],
                   hwnd, ctypes.byref(root))
        if not root:
            return []
        # 广度优先先覆盖工具栏等浅层控件，避免正文深树耗尽预算。
        pending = [root]
        rects = []
        deadline = time.monotonic() + 0.2
        try:
            index = 0
            while index < len(pending) and time.monotonic() < deadline:
                element = pending[index]
                index += 1
                rc = wintypes.RECT()
                self._call(element, 43, [ctypes.POINTER(wintypes.RECT)], ctypes.byref(rc))
                offscreen = wintypes.BOOL()
                self._call(element, 38, [ctypes.POINTER(wintypes.BOOL)], ctypes.byref(offscreen))
                if offscreen.value:
                    continue
                rect = QRect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
                if _valid_rect(rect, rc.left, rc.top):
                    rects.append(rect)
                child = self._relative(element, 4)
                while child:
                    pending.append(child)
                    if time.monotonic() >= deadline:
                        break
                    child = self._relative(child, 6)
            return rects
        finally:
            for element in pending:
                self._release(element)

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
