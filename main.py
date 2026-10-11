# GridStruct-V2 应用入口（P0-A）。
# 用法：python main.py
#
# 启动流程：单实例检查 -> 跟随系统主题 -> 托盘 -> 全局热键 -> 事件循环。
# 常驻托盘，无主窗口；退出走托盘菜单「退出」。

import sys


def _initialize_dpi_awareness() -> None:
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


# 必须先于 Qt 和其他可能创建 HWND 的模块初始化进程 DPI 模式。
if __name__ == "__main__":
    _initialize_dpi_awareness()

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QToolButton, QLineEdit, QComboBox, QCheckBox,
    QSlider, QTabWidget, QListWidget, QGroupBox, QLabel, QSystemTrayIcon,
)
from PySide6.QtCore import Qt, QSharedMemory, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from styles import apply_theme
from styles import tokens as T
from ui import tinted_icon, show_desktop_check, show_message
from tray import create_tray, GlobalHotkey, MOD_CONTROL, MOD_ALT
from capture import ScreenshotFlow, ScreenManager

from pin import show_pin_from_clipboard, restore_pins, save_pins, shutdown_pins, disable_click_through
from settings import SettingsDialog, get_settings, check_updates

APP_NAME = "GridStruct-V2"
# 显示名称变化时仍与旧包共享单实例锁，避免争抢全局热键。
_SINGLE_INSTANCE_KEY = "PixShot_SingleInstance_v1"


def _ensure_single_instance() -> QSharedMemory | None:
    """单实例：抢到共享内存返回对象，抢不到返回 None（调用方退出）。"""
    mem = QSharedMemory(_SINGLE_INSTANCE_KEY)
    if mem.create(1):
        return mem
    print("[GridStruct-V2] 已有实例在运行，退出。")
    return None


def main() -> int:
    desktop_check = "--desktop-check" in sys.argv
    app = QApplication(sys.argv)
    app.setProperty("desktop_check", desktop_check)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # 常驻托盘：无窗口时不退出
    ScreenManager.instance()

    mem = _ensure_single_instance()
    if mem is None:
        return 0
    # mem 必须活到进程结束，故意不释放

    try:
        config = get_settings()
    except OSError as exc:
        show_message(None, "无法读取便携配置", f"{exc}\n请将应用放在有写入权限的目录。")
        return 1
    mode = apply_theme(app, config.value("theme", "system"))
    print(f"[GridStruct-V2] 启动，主题：{mode}")

    flow = ScreenshotFlow(app, theme_mode=mode)

    def _open_pin():
        show_pin_from_clipboard()

    def _open_settings():
        SettingsDialog().exec()
        tray_icon.setIcon(tinted_icon("pin", T.THEMES[app.property("theme_mode")]["foreground"], 16))

    def _exit():
        if shutdown_pins():
            flow.cancel()
            app.quit()

    def _about():
        show_message(None, "关于 GridStruct-V2", "GridStruct-V2 0.1.0\n本地截图、标注、贴图与中英文 OCR。\n便携配置保存在 user-data。")

    callbacks = {
        "screenshot": flow.start,
        "pin": _open_pin,
        "longshot": flow.start_longshot,
        "settings": _open_settings,
        "update": check_updates,
        "about": _about,
        "exit": _exit,
        "restore_pin_input": disable_click_through,
    }

    tray_icon = create_tray(app, callbacks)
    if app.property("desktop_check"):
        app._desktop_check_window = show_desktop_check(callbacks)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[GridStruct-V2] 警告：系统托盘不可用")
    tray_icon.show()
    print("[GridStruct-V2] 托盘已创建")

    hotkey = GlobalHotkey(app)
    hotkey.register(callbacks["screenshot"])
    hotkey.set_hotkey(MOD_CONTROL | MOD_ALT, ord("L"))
    hotkey.register(callbacks["longshot"])

    QTimer.singleShot(0, restore_pins)
    app.aboutToQuit.connect(save_pins)

    ret = app.exec()
    hotkey.unregister()
    return ret


# ---------------------------------------------------------------------------
# 以下仅供 tools/render_check.py 离屏渲染自查主题用，不是应用逻辑。
# main() 已改为真正的应用入口，`python main.py` 不再弹演示窗口。
# ---------------------------------------------------------------------------

def themed_icon(name, mode):
    t = T.THEMES[mode]
    return tinted_icon(name, t["foreground"], 16)


def build_demo(mode: str) -> QMainWindow:
    w = QMainWindow()
    w.setWindowTitle(f"GridStruct-V2 主题演示（{mode}）")
    w.resize(560, 640)
    central = QWidget()
    w.setCentralWidget(central)
    layout = QVBoxLayout(central)
    layout.setSpacing(12)
    layout.setContentsMargins(20, 20, 20, 20)

    # 按钮区
    layout.addWidget(QLabel("按钮"))
    row = QHBoxLayout()
    for cls, text in [("primary", "Primary"), ("secondary", "Secondary"),
                      ("outline", "Outline"), ("ghost", "Ghost"),
                      ("destructive", "删除")]:
        b = QPushButton(text)
        b.setProperty("class", cls)
        row.addWidget(b)
    layout.addLayout(row)

    # 工具条按钮
    layout.addWidget(QLabel("工具条"))
    trow = QHBoxLayout()
    for name, tip in [("square", "矩形"), ("circle", "椭圆"),
                      ("arrow-up-right", "箭头"), ("pencil", "画笔"),
                      ("type", "文字"), ("eraser", "擦除")]:
        tb = QToolButton()
        tb.setIcon(themed_icon(name, mode))
        tb.setToolTip(tip)
        tb.setCheckable(True)
        trow.addWidget(tb)
    trow.addStretch()
    layout.addLayout(trow)

    # 输入区
    layout.addWidget(QLabel("输入"))
    irow = QHBoxLayout()
    irow.addWidget(QLineEdit("截图保存路径"))
    cb = QComboBox()
    cb.addItems(["PNG", "JPG", "BMP", "PDF"])
    irow.addWidget(cb)
    layout.addLayout(irow)

    # 选项
    orow = QHBoxLayout()
    orow.addWidget(QCheckBox("开机启动"))
    c2 = QCheckBox("深色跟随系统")
    c2.setChecked(True)
    orow.addWidget(c2)
    s = QSlider(Qt.Horizontal)
    s.setValue(70)
    orow.addWidget(s)
    layout.addLayout(orow)

    # Tab + 列表
    tabs = QTabWidget()
    tabs.addTab(QLabel("  通用设置页内容"), "通用")
    tabs.addTab(QLabel("  截图设置页内容"), "截图")
    tabs.addTab(QLabel("  关于页内容"), "关于")
    layout.addWidget(tabs)

    gb = QGroupBox("分组示例")
    gl = QVBoxLayout(gb)
    lw = QListWidget()
    lw.addItems(["截图 2026-10-10.png", "贴图分组 A", "OCR 历史"])
    gl.addWidget(lw)
    layout.addWidget(gb)

    layout.addStretch()
    return w


if __name__ == "__main__":
    sys.exit(main())
