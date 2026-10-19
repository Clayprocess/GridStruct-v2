# Windows 全局热键：ctypes 直调 user32.RegisterHotKey，无第三方依赖。
# 实现方式：RegisterHotKey(hwnd=NULL) + QAbstractNativeEventFilter。
# hwnd 为 NULL 时 WM_HOTKEY 投递到调用线程（即 GUI 线程）的消息队列，
# Qt 的 Windows 事件分发器会把线程消息送进 nativeEventFilter。
# 只支持 Windows；其他平台 register() 直接返回 None。

import sys
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QObject, QAbstractNativeEventFilter, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication
from ui import show_message

if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _user32.RegisterHotKey.argtypes = (
        wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint
    )
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.UnregisterHotKey.restype = wintypes.BOOL
else:
    _user32 = None

# 修饰键（与 WinAPI MOD_* 一致）
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

WM_HOTKEY = 0x0312

# 默认热键，存于设置 hotkey/<名称>，格式为 QKeySequence 的 PortableText。
HOTKEY_DEFAULTS = {"screenshot": "Ctrl+Alt+S", "longshot": "Ctrl+Alt+L"}

_QT_MODIFIERS = (
    (Qt.ControlModifier, MOD_CONTROL),
    (Qt.AltModifier, MOD_ALT),
    (Qt.ShiftModifier, MOD_SHIFT),
    (Qt.MetaModifier, MOD_WIN),
)


def parse_hotkey(text: str) -> tuple[int, int]:
    """把 'Ctrl+Alt+S' 这类组合转成 (MOD_*, 虚拟键码)；不能做全局热键时抛 ValueError 说明原因。"""
    sequence = QKeySequence(text, QKeySequence.PortableText)
    if sequence.isEmpty():
        raise ValueError("请按下新的组合键")
    if sequence.count() > 1:
        raise ValueError("全局热键只支持一组按键")
    combination = sequence[0]
    key, held = combination.key(), combination.keyboardModifiers()
    modifiers = sum(mod for qt_mod, mod in _QT_MODIFIERS if held & qt_mod)
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        vk = int(key)
        # 同 PixPin：字母和数字单按或只加 Shift 会吞掉正常打字，必须带 Ctrl、Alt 或 Win。
        if not modifiers & (MOD_CONTROL | MOD_ALT | MOD_WIN):
            raise ValueError("字母和数字需要搭配 Ctrl、Alt 或 Win")
    elif Qt.Key_F1 <= key <= Qt.Key_F24:
        vk = 0x70 + int(key) - int(Qt.Key_F1)
    elif key == Qt.Key_Print:
        vk = 0x2C
    else:
        raise ValueError("请使用字母、数字、F1–F24 或 PrintScreen 作为主键")
    return modifiers, vk


class _HotkeyNativeFilter(QAbstractNativeEventFilter):
    """把 WM_HOTKEY 转交给 GlobalHotkey。"""

    def __init__(self, manager: "GlobalHotkey"):
        super().__init__()
        self._manager = manager

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG" and _user32 is not None:
            # PySide6 传入 VoidPtr，ctypes 需要先取得整数地址。
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == WM_HOTKEY:
                self._manager._on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    """全局热键管理器（单例使用）：每个组合单独注册，返回 id 供替换和释放。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callbacks: dict[int, object] = {}
        self._filter = _HotkeyNativeFilter(self)
        self._filter_installed = False
        self._next_id = 1

    def register(self, text: str, callback, *, warn: bool = True) -> int | None:
        """注册组合键，成功返回 id；被占用时按 warn 决定是否弹窗说明。"""
        if _user32 is None:
            print("[热键] 非 Windows 平台，跳过注册")
            return None
        if not self._filter_installed:
            QApplication.instance().installNativeEventFilter(self._filter)
            self._filter_installed = True
        modifiers, vk = parse_hotkey(text)
        hid = self._next_id
        self._next_id += 1
        if not _user32.RegisterHotKey(None, hid, modifiers, vk):
            print(f"[热键] 注册失败：{text}")
            if warn:
                show_message(None, "快捷键冲突",
                             f"快捷键 [{text}] 无法注册。\n\n"
                             "可能已被其他程序使用，或系统拒绝注册；Windows 不提供占用程序名称。\n\n"
                             "可以在设置的“热键”页换一个组合；当前仍可通过托盘使用对应功能。")
            return None
        self._callbacks[hid] = callback
        print(f"[热键] 已注册：{text}（id={hid}）")
        return hid

    def release(self, hid: int) -> None:
        _user32.UnregisterHotKey(None, hid)
        del self._callbacks[hid]

    def unregister(self) -> None:
        """注销本管理器注册的全部热键。"""
        if _user32 is None:
            return
        for hid in list(self._callbacks):
            self.release(hid)
        if self._filter_installed:
            QApplication.instance().removeNativeEventFilter(self._filter)
            self._filter_installed = False
        print("[热键] 已注销全部热键")

    def _on_hotkey(self, hid: int) -> None:
        cb = self._callbacks.get(hid)
        if cb is not None:
            cb()
