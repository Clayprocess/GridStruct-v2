"""进程 DPI 模式：托盘主程序和识别文档入口都要在创建任何窗口前调用。"""

import sys


def initialize_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    prefix = "[Initialize::SetPerMonitorV2DpiAwareness]"
    print(f"{prefix} 初始化")
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_context = user32.GetThreadDpiAwarenessContext
        get_context.argtypes = []
        get_context.restype = ctypes.c_void_p
        equal_context = user32.AreDpiAwarenessContextsEqual
        equal_context.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        equal_context.restype = wintypes.BOOL
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = wintypes.BOOL
        # manifest 已生效时重复设置会返回 ACCESS_DENIED，不能因此降级到 V1。
        if equal_context(get_context(), -4):
            print(f"{prefix} 保留已生效的 PerMonitorV2")
        elif set_context(-4):
            print(f"{prefix} 已设置 PerMonitorV2")
        else:
            print(f"{prefix} 警告：设置失败，WinError={ctypes.get_last_error()}")
        if equal_context(get_context(), -4):
            print(f"{prefix} 验证：PerMonitorV2=True")
            return
    except (AttributeError, OSError) as exc:
        print(f"{prefix} 警告：PerMonitorV2 API 不可用：{exc}")

    # SetProcessDpiAwareness(2) 只能提供 V1，供不支持 V2 的 Windows 兼容使用。
    try:
        set_awareness = ctypes.WinDLL("shcore").SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        result = set_awareness(2)
        print(f"{prefix} 警告：PerMonitorV2=False，V1 兼容设置 HRESULT={result}")
    except (AttributeError, OSError) as exc:
        print(f"{prefix} 警告：V1 兼容设置失败：{exc}")
