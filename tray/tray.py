# 系统托盘图标与右键菜单（P0-A）。
# 图标：应用标识点阵，按当前主题前景色染色（ui/icons.py）。
# 样式：QMenu 的 QSS 由 styles/theme.py 全局提供，这里不写任何样式。

from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from styles import tokens as T
from ui import app_icon

TRAY_TOOLTIP = "GridStruct-V2"

# 右键菜单项：(callbacks key, 显示文本)，按显示顺序
MENU_ITEMS: list[tuple[str, str]] = [
    ("screenshot", "截图"),
    ("pin", "贴图"),
    ("longshot", "长截图"),
    ("settings", "设置"),
    ("update", "检查更新"),
    ("about", "关于"),
    ("exit", "退出"),
]

# 在这些 key 的菜单项之后加分隔线
_SEPARATOR_AFTER = {"longshot", "update"}


def create_tray(app, callbacks: dict | None) -> QSystemTrayIcon:
    """创建托盘图标并返回（调用方负责 show()）。

    callbacks: key 见 MENU_ITEMS；没有回调的菜单项不显示。
    截图菜单项约定用 callbacks["screenshot"]。
    """
    callbacks = callbacks or {}
    tray = QSystemTrayIcon(app_icon(T.THEMES[app.property("theme_mode")]["foreground"]), app)
    tray.setToolTip(TRAY_TOOLTIP)

    menu = QMenu()
    for key, text in MENU_ITEMS:
        if key not in callbacks:
            continue
        cb = callbacks[key]
        if key == "pin" and "restore_pin_input" in callbacks:
            submenu = menu.addMenu(text)
            action = submenu.addAction("从剪贴板贴图 / 文字")
            submenu.addAction("恢复所有贴图鼠标操作").triggered.connect(callbacks["restore_pin_input"])
        else:
            action = menu.addAction(text)
        # triggered(bool) -> 零参槽：Qt 允许槽参数少于信号参数
        action.triggered.connect(lambda _checked=False, _cb=cb: _cb())
        if key in _SEPARATOR_AFTER:
            menu.addSeparator()
    tray.setContextMenu(menu)
    if "screenshot" in callbacks:
        tray.activated.connect(lambda reason: callbacks["screenshot"]() if reason == QSystemTrayIcon.Trigger else None)
    return tray
