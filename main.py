# PixShot 应用入口（P0-A）。
# 用法：python main.py
#
# 启动流程：单实例检查 -> 跟随系统主题 -> 托盘 -> 全局热键 -> 事件循环。
# 常驻托盘，无主窗口；退出走托盘菜单「退出」。

import sys

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QToolButton, QLineEdit, QComboBox, QCheckBox,
    QSlider, QTabWidget, QListWidget, QGroupBox, QLabel, QSystemTrayIcon,
)
from PySide6.QtCore import Qt, QSharedMemory

from styles.theme import apply_theme
from styles import tokens as T
from ui.icons import tinted_icon
from tray import create_tray, GlobalHotkey
from capture import ScreenshotFlow

try:
    from pin import show_pin_from_clipboard
except ImportError:
    show_pin_from_clipboard = None
try:
    from settings import SettingsDialog
except ImportError:
    SettingsDialog = None

APP_NAME = "PixShot"
_SINGLE_INSTANCE_KEY = "PixShot_SingleInstance_v1"


def _ensure_single_instance() -> QSharedMemory | None:
    """单实例：抢到共享内存返回对象，抢不到返回 None（调用方退出）。"""
    mem = QSharedMemory(_SINGLE_INSTANCE_KEY)
    if mem.create(1):
        return mem
    print("[PixShot] 已有实例在运行，退出。")
    return None


def _placeholder(name: str):
    def _cb():
        print(f"[回调] {name}：占位，未实现")
    return _cb


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # 常驻托盘：无窗口时不退出

    mem = _ensure_single_instance()
    if mem is None:
        return 0
    # mem 必须活到进程结束，故意不释放

    mode = apply_theme(app, "system")
    print(f"[PixShot] 启动，主题：{mode}")

    flow = ScreenshotFlow(app, theme_mode=mode)

    def _open_longshot():
        from capture.longshot import LongShotFlow
        from annotate.window import AnnotationWindow
        from PySide6.QtCore import QPoint

        def _to_annotate(pixmap):
            dpr = pixmap.devicePixelRatio() or 1.0
            w = AnnotationWindow(
                pixmap, QPoint(100, 100), mode)
            w.finished.connect(
                lambda pm: QApplication.clipboard().setPixmap(pm))
            w.cancelled.connect(w.close)
            w.show()
            # 保持引用防止 GC
            _to_annotate._wins.append(w)
        _to_annotate._wins = getattr(_to_annotate, "_wins", [])
        LongShotFlow(app, _to_annotate).start()

    def _open_pin():
        if show_pin_from_clipboard is not None:
            show_pin_from_clipboard()
        else:
            print("[回调] 贴图：模块未就绪")

    def _open_settings():
        if SettingsDialog is not None:
            SettingsDialog().exec()
        else:
            print("[回调] 设置：模块未就绪")

    callbacks = {
        "screenshot": flow.start,
        "pin": _open_pin,
        "longshot": _open_longshot,
        "settings": _open_settings,
        "update": _placeholder("检查更新"),
        "about": _placeholder("关于"),
        "exit": app.quit,
    }

    tray_icon = create_tray(app, callbacks)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[PixShot] 警告：系统托盘不可用")
    tray_icon.show()
    print("[PixShot] 托盘已创建")

    hotkey = GlobalHotkey(app)
    if hotkey.register(callbacks["screenshot"]):
        print(f"[PixShot] 全局热键已注册：{GlobalHotkey.hotkey_name()}")
    else:
        print("[PixShot] 全局热键注册失败（见上方日志）")

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
