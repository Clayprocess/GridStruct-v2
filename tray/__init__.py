# tray 模块公开接口：跨模块调用只走这里。
from .tray import create_tray
from .hotkey import GlobalHotkey, HOTKEY_DEFAULTS, parse_hotkey

__all__ = ["create_tray", "GlobalHotkey", "HOTKEY_DEFAULTS", "parse_hotkey"]
