# Windows 全局鼠标滚轮钩子（WH_MOUSE_LL）。非 Windows 下为空实现。

import sys
from PySide6.QtCore import QObject, Signal

_IS_WIN = sys.platform == "win32"


class WheelHook(QObject):
    """wheel(delta) 信号，delta 为滚轮增量（120 的倍数）。"""

    wheel = Signal(int)

    def __init__(self):
        super().__init__()
        self._hook = None
        self._cb = None

    def start(self) -> bool:
        if not _IS_WIN or self._hook is not None:
            return _IS_WIN
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        WH_MOUSE_LL = 14
        WM_MOUSEWHEEL = 0x020A

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT),
                        ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        CMPFUNC = ctypes.WINFUNCTYPE(
            wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        def _proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam == WM_MOUSEWHEEL:
                data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                delta = ctypes.c_short(data.mouseData >> 16).value
                self.wheel.emit(delta)
            return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        self._cb = CMPFUNC(_proc)
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._cb, kernel32.GetModuleHandleW(None), 0)
        return self._hook is not None

    def stop(self):
        if self._hook is not None:
            import ctypes
            ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._cb = None
