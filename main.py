# PixShot 应用入口（P0-A）。
# 用法：python main.py
#
# 启动流程：单实例检查 -> 跟随系统主题 -> 托盘 -> 全局热键 -> 事件循环。
# 常驻托盘，无主窗口；退出走托盘菜单「退出」。

import sys

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QToolButton, QLineEdit, QComboBox, QCheckBox,
    QSlider, QTabWidget, QListWidget, QGroupBox, QLabel, QSystemTrayIcon, QMessageBox,
)
from PySide6.QtCore import Qt, QSharedMemory, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from styles import apply_theme
from styles import tokens as T
from ui import tinted_icon, show_desktop_check
from tray import create_tray, GlobalHotkey
from capture import ScreenshotFlow

from pin import show_pin_from_clipboard, restore_pins, save_pins, shutdown_pins, disable_click_through
from settings import SettingsDialog, get_settings

APP_NAME = "PixShot"
_SINGLE_INSTANCE_KEY = "PixShot_SingleInstance_v1"


def _ensure_single_instance() -> QSharedMemory | None:
    """单实例：抢到共享内存返回对象，抢不到返回 None（调用方退出）。"""
    mem = QSharedMemory(_SINGLE_INSTANCE_KEY)
    if mem.create(1):
        return mem
    print("[PixShot] 已有实例在运行，退出。")
    return None


def main() -> int:
    desktop_check = "--desktop-check" in sys.argv
    app = QApplication(sys.argv)
    app.setProperty("desktop_check", desktop_check)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # 常驻托盘：无窗口时不退出

    mem = _ensure_single_instance()
    if mem is None:
        return 0
    # mem 必须活到进程结束，故意不释放

    try:
        config = get_settings()
    except OSError as exc:
        QMessageBox.critical(None, "无法读取便携配置", f"{exc}\n请将应用放在有写入权限的目录。")
        return 1
    mode = apply_theme(app, config.value("theme", "system"))
    print(f"[PixShot] 启动，主题：{mode}")

    flow = ScreenshotFlow(app, theme_mode=mode)

    longshot_flow = None

    def _open_longshot():
        nonlocal longshot_flow
        from capture import LongShotFlow
        from annotate import AnnotationWindow
        from PySide6.QtCore import QPoint

        def _to_annotate(pixmap):
            w = AnnotationWindow(
                pixmap, QPoint(100, 100), app.property("theme_mode"), scrollable=True)
            w.finished.connect(
                lambda pm: QApplication.clipboard().setPixmap(pm))
            w.cancelled.connect(w.close)
            w.show()
            # 保持引用防止 GC
            _to_annotate._wins.append(w)
            w.destroyed.connect(lambda: _to_annotate._wins.remove(w))
        _to_annotate._wins = getattr(_to_annotate, "_wins", [])
        if longshot_flow is None:
            longshot_flow = LongShotFlow(app, _to_annotate)
        longshot_flow.start()

    def _open_pin():
        show_pin_from_clipboard()

    def _open_settings():
        SettingsDialog().exec()
        tray_icon.setIcon(tinted_icon("pin", T.THEMES[app.property("theme_mode")]["foreground"], 16))

    def _exit():
        if shutdown_pins():
            flow.cancel()
            if longshot_flow is not None:
                longshot_flow.cancel()
            app.quit()

    def _about():
        QMessageBox.about(None, "关于 PixShot", "PixShot（GridStruct v2）0.1.0\n本地截图、标注、贴图与中英文 OCR。\n便携配置保存在 user-data。")

    def _updates():
        if QMessageBox.question(None, "检查更新", "当前版本 0.1.0。是否打开项目发布页查看可用版本？",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl("https://github.com/Clayprocess/GridStruct-v2/releases"))

    callbacks = {
        "screenshot": flow.start,
        "pin": _open_pin,
        "longshot": _open_longshot,
        "settings": _open_settings,
        "update": _updates,
        "about": _about,
        "exit": _exit,
        "restore_pin_input": disable_click_through,
    }

    tray_icon = create_tray(app, callbacks)
    if app.property("desktop_check"):
        app._desktop_check_window = show_desktop_check(callbacks)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[PixShot] 警告：系统托盘不可用")
    tray_icon.show()
    print("[PixShot] 托盘已创建")

    hotkey = GlobalHotkey(app)
    if hotkey.register(callbacks["screenshot"]):
        print(f"[PixShot] 全局热键已注册：{GlobalHotkey.hotkey_name()}")
    else:
        print("[PixShot] 全局热键注册失败（见上方日志）")
        tray_icon.showMessage("截图热键不可用", "Ctrl+Alt+S 被其他程序占用。请关闭冲突程序后重启，或从托盘点击截图。", QSystemTrayIcon.Warning)

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
    w.setWindowTitle(f"PixShot 主题演示（{mode}）")
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
