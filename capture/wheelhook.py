# Windows 全局鼠标滚轮钩子（WH_MOUSE_LL）。非 Windows 下为空实现。

import sys
from threading import Event, Lock
from PySide6.QtCore import QThread, Signal

_IS_WIN = sys.platform == "win32"


class WheelHook(QThread):
    """wheel(delta) 信号，delta 为滚轮增量（120 的倍数）。"""

    wheel = Signal(int)
    horizontalWheel = Signal(int)
    injectedWheel = Signal(int)

    def __init__(self):
        super().__init__()
        self._hook = None
        self._cb = None
        # Windows 反向滚动链路会截掉 extraInfo 高 32 位；标记必须在 DWORD 内。
        self._injection_tag = (id(self) & 0xffffffff) or 1
        self._expected_scroll = None
        self._expected_lock = Lock()
        self._ready = Event()
        self._native_thread_id = 0
        self._user32 = None

    def expect_scroll(self, delta, horizontal):
        with self._expected_lock:
            self._expected_scroll = (delta, horizontal)
        return self._injection_tag

    def cancel_scroll(self):
        with self._expected_lock:
            self._expected_scroll = None

    def start(self) -> bool:
        if not _IS_WIN or self.isRunning():
            return _IS_WIN
        self._ready.clear()
        super().start()
        self._ready.wait()
        if self._hook is None:
            self.wait()
        return self._hook is not None

    def run(self):
        try:
            self._listen()
        finally:
            self._ready.set()

    def _listen(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._user32 = user32

        WH_MOUSE_LL = 14
        WM_MOUSEWHEEL = 0x020A
        WM_MOUSEHWHEEL = 0x020E

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT),
                        ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        CMPFUNC = ctypes.WINFUNCTYPE(
            wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        # Win64 默认 c_int 会截断 HMODULE/HHOOK 和消息指针。
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, CMPFUNC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = wintypes.LPARAM
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = wintypes.LPARAM
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostThreadMessageW.restype = wintypes.BOOL

        def _proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                delta = ctypes.c_short(data.mouseData >> 16).value
                expected_scroll = None
                if data.flags & 1 and data.dwExtraInfo == self._injection_tag:
                    with self._expected_lock:
                        expected_scroll = self._expected_scroll
                        if expected_scroll is not None and wParam == (WM_MOUSEHWHEEL if expected_scroll[1] else WM_MOUSEWHEEL):
                            self._expected_scroll = None
                if expected_scroll is not None:
                    expected, horizontal = expected_scroll
                    if wParam == (WM_MOUSEHWHEEL if horizontal else WM_MOUSEWHEEL):
                        self.injectedWheel.emit(delta)
                        # 系统自然滚动可反转 SendInput；只拦本会话方向错误的注入，避免先回滚页面。
                        if delta != expected:
                            return 1
                        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)
                if wParam == WM_MOUSEWHEEL:
                    self.wheel.emit(delta)
                else:
                    self.horizontalWheel.emit(delta)
            return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        self._cb = CMPFUNC(_proc)
        self._native_thread_id = kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        # 原生消息泵与 GUI 隔离，截图/拼接不能拖延 WH_MOUSE_LL 导致系统静默移除。
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._cb, kernel32.GetModuleHandleW(None), 0)
        self._ready.set()
        if not self._hook:
            self._hook = None
            return
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._cb = None

    def stop(self):
        self.cancel_scroll()
        if self.isRunning():
            if not self._user32.PostThreadMessageW(self._native_thread_id, 0x0012, 0, 0):
                raise OSError("无法停止滚轮监听线程")
            self.wait()
