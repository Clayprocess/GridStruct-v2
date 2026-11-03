# GridStruct-V2 应用入口（P0-A）。
# 用法：python main.py
#
# 启动流程：单实例检查 -> 跟随系统主题 -> 托盘 -> 全局热键 -> 事件循环。
# 常驻托盘，无主窗口；退出走托盘菜单「退出」。

import sys


# 必须先于 Qt 和其他可能创建 HWND 的模块初始化进程 DPI 模式。
if __name__ == "__main__":
    from dpi import initialize_dpi_awareness
    initialize_dpi_awareness()

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QToolButton, QLineEdit, QComboBox, QCheckBox,
    QSlider, QTabWidget, QListWidget, QGroupBox, QLabel, QSystemTrayIcon,
)
from PySide6.QtCore import Qt, QSharedMemory, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from styles import apply_theme
from styles import tokens as T
from ui import app_icon, tinted_icon, show_desktop_check, show_message
from ui.motion import enable_menu_effects
from tray import create_tray, GlobalHotkey, HOTKEY_DEFAULTS, parse_hotkey
from capture import ScreenshotFlow, ScreenManager

from pin import show_pin_from_clipboard, restore_pins, save_pins, shutdown_pins, disable_click_through
from settings import SettingsDialog, VERSION, get_settings, check_updates

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
    app.setWindowIcon(app_icon())
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
    # 窗口、任务栏按钮和托盘的图标跟主题反色：深色主题白点黑边。
    app.setWindowIcon(app_icon(mode))
    enable_menu_effects(app)
    print(f"[GridStruct-V2] 启动，主题：{mode}")

    hotkey = GlobalHotkey(app)
    flow = ScreenshotFlow(app, theme_mode=mode, hotkeys=hotkey)

    def _open_pin():
        show_pin_from_clipboard()

    def _open_document():
        # 表格模块带 numpy/onnxruntime，首次用到再加载，不拖慢托盘启动。
        from table import open_document
        open_document()

    def _open_settings():
        SettingsDialog(apply_hotkeys=_apply_hotkeys).exec()
        mode = app.property("theme_mode")
        app.setWindowIcon(app_icon(mode))
        tray_icon.setIcon(app_icon(mode))

    def _exit():
        if shutdown_pins():
            flow.cancel()
            app.quit()

    def _about():
        show_message(None, "关于 GridStruct-V2", f"GridStruct-V2 {VERSION}\n本地截图、标注、贴图、文字识别与表格识别。\n便携配置保存在 user-data。")

    callbacks = {
        "screenshot": flow.start,
        "pin": _open_pin,
        "longshot": flow.start_longshot,
        "document": _open_document,
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

    bound = {}  # 名称 -> (注册 id 或 None, 组合键文本)
    for name, default in HOTKEY_DEFAULTS.items():
        text = config.value(f"hotkey/{name}", default)
        try:
            if text or HOTKEY_DEFAULTS[name]:
                parse_hotkey(text)
        except ValueError:
            # 配置被手改坏时退回默认键，不能让截图入口失效。
            text = default
        bound[name] = (hotkey.register(text, callbacks[name]) if text else None, text)

    # 没有主窗口，第一次启动时用托盘气泡告诉用户程序在哪、怎么开始；只提示一次。
    if not config.value("hint/tray_intro_shown", False, type=bool):
        hid, text = bound["screenshot"]
        start = f"按 {text} 截图" if hid is not None else "单击托盘图标截图"
        tray_icon.showMessage("GridStruct-V2 已在托盘运行", f"{start}，右键托盘图标打开识别、设置等功能。",
                              app_icon(mode), 8000)
        config.setValue("hint/tray_intro_shown", True)

    def _apply_hotkeys(texts):
        """设置确认前调用：新组合全部注册成功才替换旧的，任何一个失败都保留旧热键并返回原因。"""
        added = {}
        for name, text in texts.items():
            hid, current = bound[name]
            if text == current and (hid is not None or not text):
                continue
            if not text:
                # 清空可选热键：只释放旧的，不注册新的。
                added[name] = None
                continue
            new = hotkey.register(text, callbacks[name], warn=False)
            if new is None:
                for done in added.values():
                    if done is not None:
                        hotkey.release(done)
                return f"[{text}] 无法注册，可能已被其他程序占用，请换一个组合。"
            added[name] = new
        for name, new in added.items():
            if bound[name][0] is not None:
                hotkey.release(bound[name][0])
            bound[name] = (new, texts[name])
        return None

    QTimer.singleShot(0, restore_pins)
    app.aboutToQuit.connect(save_pins)
    app.aboutToQuit.connect(flow.cancel)

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
