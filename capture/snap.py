# 智能吸附：鼠标悬停时自动识别窗口/控件矩形。
#
# 逆向依据（PixPin 3.5.5.1, UiRegionDetector.dll）：
#   三级检测：UIA 元素矩形 -> Win32 窗口矩形 -> 有效性校验，
#   后台线程异步，结果经信号回传，带矩形缓存。
#
# 本实现：窗口快照同步点查，两条后台精化并行：
#   1. 每窗口一次 UIAutomation 广度遍历，覆盖工具栏等浅层控件，同窗内移动零调用；
#   2. 鼠标点命中（同 PixPin 2026-10-26 反编译的日常后端）：MSAA 从窗口对象起逐层
#      accHitTest，最多 31 层，每层 accLocation 得到“窗口 → … → 最内层控件”的整条路径，
#      深层和小控件靠它补齐；命中矩形仍包含鼠标时不再查询。单次查询超过 300ms 的窗口
#      之后只用窗口快照和遍历结果（PixPin 同样把慢窗口降级为 HWND 后端）。
# 非 Windows 平台返回空（Linux 开发机不断）。

import ctypes
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


class _PointWorker(QObject):
    done = Signal(int, object, float)

    def __init__(self):
        super().__init__()
        self._msaa = None
        self._msaa_initialized = False

    @Slot(int, "qulonglong", int, int)
    def query(self, request_id: int, hwnd: int, x: int, y: int):
        started = time.perf_counter()
        rects = []
        if _IS_WIN:
            if not self._msaa_initialized:
                self._msaa = _try_init_msaa()
                self._msaa_initialized = True
            if self._msaa is not None:
                rects = self._msaa.hit_path(hwnd, x, y)
        self.done.emit(request_id, rects, (time.perf_counter() - started) * 1000)

    @Slot()
    def cleanup(self):
        if self._msaa is not None:
            self._msaa.close()
            self._msaa = None


class RegionDetector(QObject):
    """同步命中窗口快照，后台用整窗 UIA 遍历和鼠标点 MSAA 命中补控件矩形。"""

    regionDetected = Signal(QRect)

    def __init__(self, only_window: bool = True, ignored_hwnd: int = 0, *, ignored_hwnds=()):
        super().__init__()
        self._only_window = only_window
        self._cache = {}
        # 点命中结果和慢窗口：首次 _refresh_snapshot 会按存活窗口过滤，必须先建好。
        self._hits = {}
        self._slow = set()
        self._requests = {}
        self._request_id = 0
        self._mode_revision = 0
        self._inflight = False
        self._queued = None
        self._stopping = False
        self._ignored_hwnd = frozenset(ignored_hwnds) | {ignored_hwnd}
        self._windows = []
        self._last_rect = QRect()
        self._last = None
        # 滚轮选中的层级：0 是鼠标下最小的元素，越大越接近整个窗口。
        self._chain = []
        self._level = 0
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
        # 点命中单独一条线程：整窗遍历可能占满 200ms，不能挡住鼠标下的精化。
        self._point_inflight = False
        self._point_queued = None
        self._point_id = 0
        self._point_requests = {}
        self._point_thread = QThread(self)
        self._point_worker = _PointWorker()
        self._point_worker.moveToThread(self._point_thread)
        self._point_worker.done.connect(self._on_point_done)
        self._point_thread.finished.connect(self._point_worker.cleanup)
        self._point_thread.finished.connect(self._point_worker.deleteLater)
        self._point_thread.start()

    def detect(self, pos: QPoint):
        from .screen import ScreenManager

        if self._stopping:
            return
        self._last = QPoint(pos)
        manager = ScreenManager.instance()
        screen = manager.screen_at(pos)
        if screen is None:
            self._discard_queued()
            self._chain = []
            self._publish(QRect())
            return
        point = manager.logical_to_physical(pos, screen)
        x, y = point.x(), point.y()
        hit = self._lookup(x, y)
        if hit is None:
            self._discard_queued()
            self._chain = []
            self._publish(QRect())
            return
        hwnd, window_rect = hit
        key = (hwnd, window_rect.getRect())
        chain = [] if self._only_window else self._element_chain(key, window_rect, x, y)
        chain.append(window_rect)
        # 换到另一个元素上时回到最内层，在同一元素内移动保持滚轮选的层级。
        if not self._chain or chain[0] != self._chain[0]:
            self._level = 0
        self._chain = chain
        rect = chain[min(self._level, len(chain) - 1)]
        self._publish(manager.physical_rect_to_logical(rect))
        if not self._only_window:
            self._request_point(key, hwnd, x, y)
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

    def _element_chain(self, key, window_rect, x, y):
        """遍历缓存与点命中路径合并：包含鼠标的控件由内到外，去重后按面积排序。"""
        found = {c.getRect() for c in self._cache.get(key, ()) if c.contains(x, y)}
        hits = self._hits.get(key)
        if hits is not None:
            found |= {c.intersected(window_rect).getRect() for c in hits[1] if c.contains(x, y)}
        found.discard(window_rect.getRect())
        return sorted((QRect(*r) for r in found if r[2] >= _MIN_ELEMENT and r[3] >= _MIN_ELEMENT),
                      key=lambda r: r.width() * r.height())

    def _request_point(self, key, hwnd, x, y):
        hits = self._hits.get(key)
        # 最内层命中仍包含鼠标就沿用（同 PixPin 的矩形包含短路），换控件才再问一次。
        # 结果只沿用 1 秒：Chrome 等程序首次被查询后才逐步建好无障碍树，停住不动也要能细化。
        if key in self._slow or (hits is not None and hits[0].contains(x, y)
                                 and time.monotonic() - hits[2] < _HIT_REUSE_S):
            return
        self._point_id += 1
        self._point_requests[self._point_id] = (key, self._mode_revision)
        request = (self._point_id, hwnd, x, y)
        if self._point_inflight:
            # 只保留最新的位置：鼠标划过的中间点没有必要再查。
            if self._point_queued is not None:
                self._point_requests.pop(self._point_queued[0])
            self._point_queued = request
        else:
            self._dispatch_point(*request)

    def _dispatch_point(self, request_id, hwnd, x, y):
        from PySide6.QtCore import QMetaObject, Qt, Q_ARG
        self._point_inflight = True
        QMetaObject.invokeMethod(
            self._point_worker, "query", Qt.QueuedConnection,
            Q_ARG(int, request_id), Q_ARG("qulonglong", hwnd), Q_ARG(int, x), Q_ARG(int, y))

    @Slot(int, object, float)
    def _on_point_done(self, request_id, rects, elapsed_ms):
        key, revision = self._point_requests.pop(request_id)
        self._point_inflight = False
        if not self._stopping and revision == self._mode_revision:
            if elapsed_ms > _SLOW_POINT_MS:
                self._slow.add(key)
                print(f"[GridStruct-V2] 点命中 {elapsed_ms:.0f}ms，该窗口改用窗口快照", flush=True)
            # 记下最内层矩形用于短路；窗口不提供无障碍信息时整窗都算命中，1 秒内不再问。
            inner = rects[-1] if rects else QRect(*key[1])
            self._hits[key] = (inner, rects, time.monotonic())
        if self._point_queued is not None and not self._stopping:
            request, self._point_queued = self._point_queued, None
            self._dispatch_point(*request)
        if not self._stopping and self._last is not None:
            self.detect(self._last)

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
        # 不随刷新清空点命中路径（否则高亮会退回外层闪一下），由新结果直接替换。
        self._hits = {key: hits for key, hits in self._hits.items() if key in alive}
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
                (QRect(*r) for r in unique if r[2] >= _MIN_ELEMENT and r[3] >= _MIN_ELEMENT),
                key=lambda r: r.width() * r.height())
        self._inflight = False
        if self._queued is not None and not self._stopping:
            request, self._queued = self._queued, None
            self._dispatch(*request)
        if not self._stopping and self._last is not None:
            self.detect(self._last)

    def step_level(self, delta):
        """delta>0 选更外层的父元素，<0 回到更内层的子元素。"""
        if self._last is None or not self._chain:
            return
        self._level = max(0, min(len(self._chain) - 1, self._level + delta))
        self.detect(self._last)

    def setIsOnlyWindow(self, enabled: bool):
        self._only_window = enabled
        self._mode_revision += 1
        self._discard_queued()
        self._cache.clear()
        self._hits.clear()
        if self._last is not None:
            self.detect(self._last)

    def shutdown(self):
        self._stopping = True
        self._snapshot_timer.stop()
        self._discard_queued()
        self._point_queued = None
        self._thread.quit()
        self._point_thread.quit()
        self._thread.wait()
        self._point_thread.wait()


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
    excluded = hwnd in ignored_hwnd if isinstance(ignored_hwnd, (set, frozenset)) else hwnd == ignored_hwnd
    if (not hwnd or excluded or not user32.IsWindow(hwnd)
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


# 控件最小边长（物理像素）：任务栏、托盘、标签页关闭按钮这类小图标也要能框到。
_MIN_ELEMENT = 4
# 单次点命中超过这个耗时的窗口，之后不再做点命中（同 PixPin 的 300ms 降级）。
_SLOW_POINT_MS = 300
# 点命中结果的沿用时长（秒）。
_HIT_REUSE_S = 1.0


def _valid_rect(rect, x, y, minimum=8):
    return (minimum <= rect.width() <= 10000 and minimum <= rect.height() <= 10000
            and rect.contains(x, y))


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

    def _relative(self, element, index):
        ctypes = self._ctypes
        found = ctypes.c_void_p()
        self._call(self._walker, index, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], element, ctypes.byref(found))
        return found

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
                if _valid_rect(rect, rc.left, rc.top, _MIN_ELEMENT):
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

# ---------- MSAA 点命中（oleacc.dll，IAccessible ABI 来自 oleacc.h） ----------


class _VariantValue(ctypes.Union):
    _fields_ = [("lVal", ctypes.c_long), ("pointer", ctypes.c_void_p), ("pad", ctypes.c_byte * 16)]


class _Variant(ctypes.Structure):
    # x64 VARIANT：vt + 3 个保留字，后跟 16 字节联合体，共 24 字节。
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("value", _VariantValue)]


class _Accessibility:
    # IAccessible 虚表：QueryInterface 0、Release 2、accLocation 22、accHitTest 24。
    _IID = "618736e0-3c3d-11cf-810c-00aa00389b71"
    _VT_I4, _VT_DISPATCH = 3, 9
    _MAX_DEPTH = 31

    def __init__(self):
        from ctypes import wintypes
        from uuid import UUID
        self._ctypes = ctypes
        self._ole32 = ctypes.OleDLL("ole32")
        self._ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._ole32.CoInitializeEx.restype = ctypes.c_long
        self._ole32.CoUninitialize.argtypes = []
        self._ole32.CoUninitialize.restype = None
        # 在已初始化为 STA 的线程上会返回 RPC_E_CHANGED_MODE，此时不能多配一次 CoUninitialize。
        self._com_owned = self._ole32.CoInitializeEx(None, 0) >= 0
        self._oleacc = ctypes.WinDLL("oleacc")
        self._oleacc.AccessibleObjectFromWindow.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self._oleacc.AccessibleObjectFromWindow.restype = ctypes.c_long
        self._user32 = _win32_api()
        self._iid = (ctypes.c_byte * 16).from_buffer_copy(UUID(self._IID).bytes_le)

    def _call(self, pointer, index, argtypes, *args):
        ctypes = self._ctypes
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])
        result = function(pointer, *args)
        if result < 0:
            raise OSError(f"IAccessible HRESULT 0x{result & 0xffffffff:08x}")
        return result

    def _release(self, pointer):
        if pointer:
            self._call(pointer, 2, [])

    def close(self):
        if self._com_owned:
            self._ole32.CoUninitialize()

    def _location(self, accessible, child):
        ctypes = self._ctypes
        left, top, width, height = (ctypes.c_long() for _ in range(4))
        variant = _Variant(vt=self._VT_I4)
        variant.value.lVal = child
        self._call(accessible, 22, [ctypes.POINTER(ctypes.c_long)] * 4 + [_Variant],
                   ctypes.byref(left), ctypes.byref(top), ctypes.byref(width), ctypes.byref(height), variant)
        return QRect(left.value, top.value, width.value, height.value)

    def hit_path(self, hwnd, x, y):
        """由外到内返回鼠标所在的各层控件矩形（物理像素）。"""
        ctypes = self._ctypes
        result = ctypes.c_size_t()
        # 跨进程 COM 遇到无响应窗口会一直卡住线程，先用无副作用的 WM_NULL 探一下。
        if not self._user32.SendMessageTimeoutW(hwnd, 0, 0, 0, 2, 100, ctypes.byref(result)):
            return []
        accessible = ctypes.c_void_p()
        if (self._oleacc.AccessibleObjectFromWindow(hwnd, 0, ctypes.byref(self._iid), ctypes.byref(accessible)) < 0
                or not accessible):
            return []
        rects = []
        try:
            for _ in range(self._MAX_DEPTH):
                variant = _Variant()
                self._call(accessible, 24, [ctypes.c_long, ctypes.c_long, ctypes.POINTER(_Variant)],
                           x, y, ctypes.byref(variant))
                if variant.vt == self._VT_DISPATCH and variant.value.pointer:
                    dispatch, child = variant.value.pointer, ctypes.c_void_p()
                    try:
                        self._call(dispatch, 0, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                                   ctypes.byref(self._iid), ctypes.byref(child))
                    finally:
                        self._release(dispatch)
                    self._release(accessible)
                    accessible = child
                    if not accessible:
                        break
                    rect = self._location(accessible, 0)
                elif variant.vt == self._VT_I4 and variant.value.lVal != 0:
                    rect = self._location(accessible, variant.value.lVal)
                else:
                    break
                if not rect.contains(x, y):
                    break
                rects.append(rect)
                if variant.vt == self._VT_I4:
                    break
        except OSError:
            pass  # 某层不支持命中或定位：保留已经得到的外层路径。
        finally:
            self._release(accessible)
        return rects


def _try_init_msaa():
    try:
        return _Accessibility()
    except OSError:
        return None


def _try_init_uia():
    try:
        return _UIAutomation()
    except OSError:
        return None
