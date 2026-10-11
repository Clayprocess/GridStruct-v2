# tray 模块公开接口：跨模块调用只走这里。
from .tray import create_tray
from .hotkey import GlobalHotkey, MOD_CONTROL, MOD_ALT

__all__ = ["create_tray", "GlobalHotkey", "MOD_CONTROL", "MOD_ALT"]
