# Windows 全局热键：ctypes 直调 user32.RegisterHotKey，无第三方依赖。
# 实现方式：RegisterHotKey(hwnd=NULL) + QAbstractNativeEventFilter。
# hwnd 为 NULL 时 WM_HOTKEY 投递到调用线程（即 GUI 线程）的消息队列，
# Qt 的 Windows 事件分发器会把线程消息送进 nativeEventFilter。
# 只支持 Windows；其他平台 register() 直接返回 False，不抛异常。

import sys
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QObject, QAbstractNativeEventFilter
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
VK_S = 0x53  # 'S'

# 默认热键：Ctrl+Alt+S（硬编码；改配置走 set_hotkey()）
_DEFAULT_MODIFIERS = MOD_CONTROL | MOD_ALT
_DEFAULT_VK = VK_S

_MOD_NAMES = (
    (MOD_CONTROL, "Ctrl"),
    (MOD_ALT, "Alt"),
    (MOD_SHIFT, "Shift"),
    (MOD_WIN, "Win"),
)


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
    """全局热键管理器（建议单例使用）。

    用法：
        hotkey = GlobalHotkey(app)
        hotkey.register(on_screenshot)   # 注册默认 Ctrl+Alt+S
        ...
        hotkey.unregister()              # 退出前注销
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callbacks: dict[int, object] = {}
        self._filter = _HotkeyNativeFilter(self)
        self._filter_installed = False
        self._modifiers = _DEFAULT_MODIFIERS
        self._vk = _DEFAULT_VK
        self._next_id = 1

    # ---------- 配置接口（必须在 register() 之前调用） ----------

    def set_hotkey(self, modifiers: int, vk: int) -> None:
        """修改热键。modifiers 取 MOD_* 组合，vk 取虚拟键码（如 VK_S）。"""
        self._modifiers = modifiers
        self._vk = vk

    @staticmethod
    def hotkey_name(modifiers: int | None = None, vk: int | None = None) -> str:
        """人类可读的热键名，如 'Ctrl+Alt+S'。"""
        modifiers = _DEFAULT_MODIFIERS if modifiers is None else modifiers
        vk = _DEFAULT_VK if vk is None else vk
        parts = [name for mod, name in _MOD_NAMES if modifiers & mod]
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
            parts.append(chr(vk))
        else:
            parts.append(f"VK_{vk:#04x}")
        return "+".join(parts)

    # ---------- 注册 / 注销 ----------

    def register(self, callback) -> bool:
        """注册全局热键，触发时调用 callback（GUI 线程）。成功返回 True。"""
        if _user32 is None:
            print("[热键] 非 Windows 平台，跳过注册")
            return False
        if callback is None:
            raise ValueError("callback 不能为空")
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("register() 需要在 QApplication 创建后调用")
        if not self._filter_installed:
            app.installNativeEventFilter(self._filter)
            self._filter_installed = True

        hid = self._next_id
        self._next_id += 1
        ok = bool(_user32.RegisterHotKey(None, hid, self._modifiers, self._vk))
        if not ok:
            name = self.hotkey_name(self._modifiers, self._vk)
            print(f"[热键] 注册失败：{name}")
            show_message(None, "快捷键冲突",
                         f"快捷键 [{name}] 无法注册。\n\n"
                         "可能已被其他程序使用，或系统拒绝注册；Windows 不提供占用程序名称，无法直接清除外部冲突。\n\n"
                         "请关闭使用该组合键的程序后重启 GridStruct-V2；当前仍可通过托盘使用对应功能。")
            return False
        self._callbacks[hid] = callback
        print(f"[热键] 已注册：{self.hotkey_name(self._modifiers, self._vk)}（id={hid}）")
        return True

    def unregister(self) -> None:
        """注销本管理器注册的全部热键。"""
        if _user32 is None:
            return
        for hid in list(self._callbacks):
            _user32.UnregisterHotKey(None, hid)
        self._callbacks.clear()
        if self._filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._filter)
            self._filter_installed = False
        print("[热键] 已注销全部热键")

    # ---------- 内部 ----------

    def _on_hotkey(self, hid: int) -> None:
        cb = self._callbacks.get(hid)
        if cb is not None:
            cb()
