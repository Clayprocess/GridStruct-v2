# 智能吸附：鼠标悬停时自动识别窗口/控件矩形。结构参考 PixPin：
#   1. 截图开始时拍一次窗口快照（画面已冻结，期间不再刷新），鼠标所在窗口同步命中；
#   2. 一条常驻工作线程做 MSAA 点命中：从窗口对象起逐层 accHitTest，最多 31 层，
#      每层 accLocation 得到“窗口 → … → 最内层控件”的路径；COM 只初始化一次，
#      每个窗口的 IAccessible 对象建一次反复用；
#      浏览器的 accHitTest 会跳层（直接返回单元格里的文字），整张表格、行不在路径里。
#      所以分两段：点命中结果先送回界面出框；再沿 accParent 补全祖先（每层一次跨进程调用，
#      Edge 里 9 层约 200ms），新请求一来就中断，补过的元素缓存，鼠标停住时滚轮就能切到整张表格；
#   3. 请求只保留最新一个，结果用 Qt 信号立刻送回界面线程；
#   4. 最内层矩形仍包含鼠标时不再查询；换控件时保留旧框直到新结果到达，
#      避免先退回外层容器再跳到新控件；
#   5. 单次点命中（不含补祖先）超过 300ms 的窗口本次截图只用窗口矩形；
#      卡住超过 2s 的线程丢弃、换新线程，那个窗口冷却 30s 后再试。
# 非 Windows 平台返回空（Linux 开发机不断）。

import ctypes
import sys
from threading import Condition, Lock, Thread
import time
from PySide6.QtCore import QObject, QTimer, Signal, QRect, QPoint, Slot, Qt

_IS_WIN = sys.platform == "win32"


class _Results(QObject):
    """住在界面线程的转发器：工作线程发信号会自动排队到界面线程，不用轮询。"""

    done = Signal(object)


_results = None
# 外部 COM 调用可能不返回：卡住的窗口冷却期内只用窗口吸附；卡住的线程留给它自己结束，
# 最多容忍 _MAX_ABANDONED 条，再多就整体停用点命中，避免无限堆积。
_hung_hwnds = {}  # hwnd -> 判为卡死的时刻


def _hung(hwnd):
    # 偶尔一次卡顿（首次查询、页面正忙）不该让窗口在整个运行期间都只能吸附整窗。
    since = _hung_hwnds.get(hwnd)
    return since is not None and time.monotonic() - since < _HUNG_COOLDOWN_S
_abandoned_lock = Lock()
_abandoned = 0


def _result_channel():
    global _results
    if _results is None:
        _results = _Results()
    return _results


class _Worker:
    """一次截图会话一条线程；只引用结果转发器，不引用 detector，旧会话可以立即销毁。"""

    def __init__(self, results):
        self._cond = Condition()
        self._pending = None
        self._current = None
        self._stopped = False
        self._abandoned = False
        self.busy = None  # (hwnd, 开始时刻)：正在执行的查询
        self._results = results
        Thread(target=self._run, name="GridStruct-snap", daemon=True).start()

    def submit(self, request):
        # 只留最新位置：鼠标划过的中间点没有必要再查。
        with self._cond:
            self._pending = None if request == self._current else request
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._stop_locked()

    def _stop_locked(self):
        global _abandoned
        if self._stopped:
            return
        # 判断在途、登记名额、停止接单必须和查询完成互斥，否则会漏计或永久占住名额。
        if self.busy is not None:
            with _abandoned_lock:
                _abandoned += 1
            self._abandoned = True
        self._stopped = True
        self._pending = None
        self._cond.notify()

    def abandon_if_hung(self, now):
        with self._cond:
            busy = self.busy
            if busy is None or now - busy[1] < _HUNG_S:
                return None
            self._stop_locked()
            return busy[0]

    def _run(self):
        global _abandoned
        provider = _try_init_msaa()
        try:
            while True:
                with self._cond:
                    while self._pending is None and not self._stopped:
                        self._cond.wait()
                    if self._stopped:
                        return
                    request, self._pending = self._pending, None
                    self._current = request
                    self.busy = (request[2], time.monotonic())
                token, key, hwnd, x, y = request
                started = time.perf_counter()
                rects = []
                if provider is not None:
                    try:
                        rects = provider.hit_path(hwnd, x, y)
                    except OSError as error:
                        print(f"[GridStruct-V2] 点命中查询失败：{error}", flush=True)
                elapsed = (time.perf_counter() - started) * 1000
                with self._cond:
                    if self._stopped:
                        return
                self._results.done.emit((token, key, x, y, rects, elapsed))
                # 第二段：补祖先。鼠标移到别处（有新请求）就放弃，不拖慢下一次点命中。
                full = provider.ancestors(lambda: self._pending is not None or self._stopped) if provider else None
                with self._cond:
                    self.busy = None
                    self._current = None
                    if self._stopped:
                        return
                if full is not None and full != rects:
                    # 补全耗时不计入慢窗口判断：点命中本身已经出框了。
                    self._results.done.emit((token, key, x, y, full, 0.0))
        finally:
            # COM 的创建、调用和释放必须在同一线程。
            if provider is not None:
                provider.close()
            if self._abandoned:
                with _abandoned_lock:
                    _abandoned -= 1


class RegionDetector(QObject):
    """同步命中窗口快照，后台用 MSAA 点命中补出鼠标下的控件层级。"""

    regionDetected = Signal(QRect)

    def __init__(self, only_window: bool = True, ignored_hwnd: int = 0, *, ignored_hwnds=()):
        super().__init__()
        self._only_window = only_window
        # key=(hwnd, 窗口矩形) -> (最内层矩形, 由外到内的路径, 查询时刻)
        self._hits = {}
        self._slow = set()
        self._stopping = False
        self._token = object()
        self._worker = None
        self._windows = _snapshot_windows(frozenset(ignored_hwnds) | {ignored_hwnd}) if _IS_WIN else []
        self._window_api = _win32_api() if _IS_WIN else None
        self._last_rect = QRect()
        self._last = None
        self._point = None  # 鼠标的物理坐标
        self._shown_key = None  # 当前高亮属于哪个窗口
        # 滚轮选中的层级：0 是鼠标下最小的元素，越大越接近整个窗口。
        self._chain = []
        self._level = 0
        self._channel = _result_channel()
        self._channel.done.connect(self._on_result)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setTimerType(Qt.PreciseTimer)
        self._refresh_timer.timeout.connect(self._refresh_hover)

    def _refresh_hover(self):
        self.detect(self._last)

    def detect(self, pos: QPoint):
        from .screen import ScreenManager

        if self._stopping:
            return
        self._refresh_timer.stop()
        self._last = QPoint(pos)
        manager = ScreenManager.instance()
        screen = manager.screen_at(pos)
        hit = None
        if screen is not None:
            point = manager.logical_to_physical(pos, screen)
            self._point = (point.x(), point.y())
            hit = self._lookup(*self._point)
        if hit is None:
            self._chain = []
            self._shown_key = None
            self._publish(QRect())
            return
        hwnd, window_rect = hit
        key = (hwnd, window_rect.getRect())
        x, y = self._point
        if self._only_window or key in self._slow or _hung(hwnd):
            self._show(key, x, y)
            return
        hits = self._hits.get(key)
        covered = hits is not None and hits[0].contains(x, y)
        if covered:
            self._show(key, x, y)
        elif self._shown_key != key:
            # 刚进入这个窗口：先框整个窗口，控件结果到了再收紧。
            self._show(key, x, y)
        # 结果只沿用 1 秒：Chrome 等程序首次被查询后才逐步建好无障碍树，停住不动也要能细化。
        remaining = _HIT_REUSE_S - (time.monotonic() - hits[2]) if covered else 0
        if remaining <= 0:
            self._request(key, hwnd, x, y)
            if _hung(hwnd):
                self._show(key, x, y)
                return
            remaining = min(_HIT_REUSE_S, _HUNG_S)
        # 无鼠标事件时也要重查懒加载控件，并检查永久不返回的查询；不恢复窗口枚举或结果轮询。
        self._refresh_timer.start(max(1, int(remaining * 1000) + 1))

    def _show(self, key, x, y):
        from .screen import ScreenManager

        window_rect = QRect(*key[1])
        chain = [] if key in self._slow or _hung(key[0]) else self._element_chain(key, window_rect, x, y)
        chain.append(window_rect)
        # 换到另一个元素上时回到最内层，在同一元素内移动保持滚轮选的层级。
        if not self._chain or chain[0] != self._chain[0]:
            self._level = 0
        self._chain = chain
        self._shown_key = key
        rect = chain[min(self._level, len(chain) - 1)]
        self._publish(ScreenManager.instance().physical_rect_to_logical(rect))

    def _element_chain(self, key, window_rect, x, y):
        """点命中路径里包含该点的控件由内到外，去重后按面积排序。"""
        hits = self._hits.get(key)
        if hits is None:
            return []
        found = {c.intersected(window_rect).getRect() for c in hits[1] if c.contains(x, y)}
        found.discard(window_rect.getRect())
        return sorted((QRect(*r) for r in found if r[2] >= _MIN_ELEMENT and r[3] >= _MIN_ELEMENT),
                      key=lambda r: r.width() * r.height())

    def _request(self, key, hwnd, x, y):
        worker = self._worker
        hung_hwnd = worker.abandon_if_hung(time.monotonic()) if worker is not None else None
        if hung_hwnd is not None:
            _hung_hwnds[hung_hwnd] = time.monotonic()
            print(f"[GridStruct-V2] 点命中超过 {_HUNG_S:.0f}s 未返回，窗口 {hung_hwnd} "
                  f"{_HUNG_COOLDOWN_S:.0f}s 内改用窗口吸附", flush=True)
            worker = self._worker = None
            if _hung(hwnd):
                return
        if worker is None:
            if _abandoned >= _MAX_ABANDONED:
                return
            worker = self._worker = _Worker(self._channel)
        worker.submit((self._token, key, hwnd, x, y))

    @Slot(object)
    def _on_result(self, result):
        token, key, x, y, rects, elapsed_ms = result
        if token is not self._token or self._stopping:
            return
        if elapsed_ms > _SLOW_POINT_MS:
            self._slow.add(key)
            print(f"[GridStruct-V2] 点命中 {elapsed_ms:.0f}ms，该窗口改用窗口快照", flush=True)
        # 窗口不提供无障碍信息时整窗都算命中，1 秒内不再问。
        inner = rects[-1] if rects else QRect(*key[1])
        self._hits[key] = (inner, rects, time.monotonic())
        if self._point is None or self._shown_key != key:
            return
        if not inner.contains(*self._point):
            # 鼠标已经移开：先显示刚查到的控件（只差一次查询的距离），新位置的请求已在排队。
            self._show(key, x, y)
        self.detect(self._last)

    def redetect(self, pos: QPoint):
        """选区取消后重新显示高亮：结果和取消前相同也要再发一次。"""
        self._last_rect = QRect()
        self.detect(pos)

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

    def step_level(self, delta):
        """delta>0 选更外层的父元素，<0 回到更内层的子元素。"""
        from .screen import ScreenManager

        if not self._chain:
            return
        self._level = max(0, min(len(self._chain) - 1, self._level + delta))
        self._publish(ScreenManager.instance().physical_rect_to_logical(self._chain[self._level]))

    def shutdown(self):
        if self._stopping:
            return
        self._stopping = True
        self._refresh_timer.stop()
        self._channel.done.disconnect(self._on_result)
        if self._worker is not None:
            # 还卡在查询里的线程计入丢弃名额，反复截图同一个卡住的程序时不会越堆越多。
            self._worker.stop()
            self._worker = None


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
# 单次点命中（不含补祖先）超过这个耗时的窗口，本次截图不再做点命中（300ms 降级）。
_SLOW_POINT_MS = 300
# 点命中结果的沿用时长（秒）。
_HIT_REUSE_S = 1.0
# 单次点命中超过它视为卡死。
_HUNG_S = 2.0
_HUNG_COOLDOWN_S = 30.0
_MAX_ABANDONED = 2


def _valid_rect(rect, x, y, minimum=8):
    return (minimum <= rect.width() <= 10000 and minimum <= rect.height() <= 10000
            and rect.contains(x, y))


# ---------- MSAA 点命中（oleacc.dll，IAccessible ABI 来自 oleacc.h） ----------


class _VariantValue(ctypes.Union):
    _fields_ = [("lVal", ctypes.c_long), ("pointer", ctypes.c_void_p), ("pad", ctypes.c_byte * 16)]


class _Variant(ctypes.Structure):
    # x64 VARIANT：vt + 3 个保留字，后跟 16 字节联合体，共 24 字节。
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("value", _VariantValue)]


class _Accessibility:
    # IAccessible 虚表：QueryInterface 0、Release 2、get_accParent 7、accLocation 22、accHitTest 24。
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
        # hwnd -> (窗口的 IAccessible, 它的矩形)：跨进程取一次要往返一趟，同一会话内复用。
        self._roots = {}
        # 上次点命中最深的完整对象，留给 ancestors() 补祖先：(对象, x, y, 窗口矩形, 缓存键, 路径)
        self._deep = None
        # (hwnd, 点命中路径) -> 补全后的路径：鼠标停住时的重查不再重新走一遍父链。
        self._walks = {}

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
        self._drop_deep()
        for root, _rect in self._roots.values():
            self._release(root)
        self._roots.clear()
        if self._com_owned:
            self._ole32.CoUninitialize()

    def _root(self, hwnd):
        entry = self._roots.get(hwnd)
        if entry is not None:
            return entry
        ctypes = self._ctypes
        result = ctypes.c_size_t()
        # 跨进程 COM 遇到无响应窗口会一直卡住线程，先用无副作用的 WM_NULL 探一下。
        if not self._user32.SendMessageTimeoutW(hwnd, 0, 0, 0, 2, 100, ctypes.byref(result)):
            return None
        root = ctypes.c_void_p()
        if self._oleacc.AccessibleObjectFromWindow(hwnd, 0, ctypes.byref(self._iid), ctypes.byref(root)) < 0 or not root:
            return None
        try:
            entry = self._roots[hwnd] = (root, self._location(root, 0))
        except OSError:
            self._release(root)
            return None
        return entry

    def _location(self, accessible, child):
        ctypes = self._ctypes
        left, top, width, height = (ctypes.c_long() for _ in range(4))
        variant = _Variant(vt=self._VT_I4)
        variant.value.lVal = child
        self._call(accessible, 22, [ctypes.POINTER(ctypes.c_long)] * 4 + [_Variant],
                   ctypes.byref(left), ctypes.byref(top), ctypes.byref(width), ctypes.byref(height), variant)
        return QRect(left.value, top.value, width.value, height.value)

    def _query(self, dispatch):
        """IDispatch 换成 IAccessible，并释放传入的 IDispatch。"""
        ctypes = self._ctypes
        accessible = ctypes.c_void_p()
        try:
            self._call(dispatch, 0, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                       ctypes.byref(self._iid), ctypes.byref(accessible))
        finally:
            self._release(dispatch)
        return accessible

    def _drop_deep(self):
        if self._deep is not None:
            self._release(self._deep[0])
            self._deep = None

    def ancestors(self, interrupted):
        """沿上次点命中最深对象的 accParent 走到窗口，返回补全后的路径（由外到内）。

        矩形相同不代表是同一个对象（包装层常和子元素一样大），只在到达窗口或越界时停。
        interrupted() 为真时放弃并返回 None；没有可补的对象也返回 None。
        """
        if self._deep is None:
            return None
        node, x, y, stop, key, rects = self._deep
        if key in self._walks:
            self._drop_deep()
            return self._walks[key]
        ctypes = self._ctypes
        found, current = [], node
        try:
            for _ in range(self._MAX_DEPTH):
                if interrupted():
                    return None
                parent = ctypes.c_void_p()
                self._call(current, 7, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(parent))
                if not parent:
                    break
                accessible = self._query(parent)
                if current is not node:
                    self._release(current)
                current = accessible
                if not current:
                    break
                rect = self._location(current, 0)
                if rect == stop or not rect.contains(x, y):
                    break
                found.append(rect)
        except OSError:
            pass  # 某层不支持取父对象：保留已经补到的祖先。
        finally:
            if current is not node:
                self._release(current)
            self._drop_deep()
        unique = dict.fromkeys(r.getRect() for r in found + rects)
        full = sorted((QRect(*r) for r in unique), key=lambda r: r.width() * r.height(), reverse=True)
        self._walks[key] = full
        return full

    def hit_path(self, hwnd, x, y):
        """由外到内返回 accHitTest 逐层命中的控件矩形（物理像素），跳过的层由 ancestors() 补。"""
        ctypes = self._ctypes
        self._drop_deep()
        entry = self._root(hwnd)
        if entry is None:
            return []
        root, root_rect = entry
        accessible = root
        rects = []
        descended = False  # 是否下钻到过完整对象（不只是简单子元素）
        try:
            for _ in range(self._MAX_DEPTH):
                variant = _Variant()
                self._call(accessible, 24, [ctypes.c_long, ctypes.c_long, ctypes.POINTER(_Variant)],
                           x, y, ctypes.byref(variant))
                if variant.vt == self._VT_DISPATCH and variant.value.pointer:
                    child = self._query(variant.value.pointer)
                    if not child:
                        break
                    try:
                        rect = self._location(child, 0)
                    except OSError:
                        self._release(child)
                        raise
                    if not rect.contains(x, y):
                        self._release(child)
                        break
                    if accessible is not root:
                        self._release(accessible)
                    accessible, descended = child, True
                    rects.append(rect)
                elif variant.vt == self._VT_I4 and variant.value.lVal != 0:
                    rect = self._location(accessible, variant.value.lVal)
                    if rect.contains(x, y):
                        rects.append(rect)
                    break
                else:
                    break
            if descended:
                key = (hwnd, tuple(r.getRect() for r in rects))
                self._deep, accessible = (accessible, x, y, root_rect, key, list(rects)), root
        except OSError:
            # 某层不支持命中或定位：保留已经得到的外层路径。窗口对象本身失效（窗口已关）就丢掉缓存。
            if accessible is root and not rects:
                self._release(self._roots.pop(hwnd)[0])
        finally:
            if accessible is not root:
                self._release(accessible)
        return rects


def _try_init_msaa():
    try:
        return _Accessibility()
    except OSError:
        return None

