# 系统托盘图标与右键菜单（P0-A）。
# 图标：Lucide `pin`，按当前主题前景色染色（ui/icons.py）。
# 样式：QMenu 的 QSS 由 styles/theme.py 全局提供，这里不写任何样式。

from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import Qt

from styles import tokens as T
from ui.icons import tinted_icon

TRAY_ICON_NAME = "pin"
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


def _current_theme() -> str:
    """跟 styles/theme.py 同样的跟随系统逻辑（tray 不能改 theme.py，只能本地判）。"""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"
    except Exception:
        return "light"


def _placeholder(text: str):
    def _cb():
        print(f"[托盘] {text}：占位回调，未实现")
    return _cb


def create_tray(app, callbacks: dict | None) -> QSystemTrayIcon:
    """创建托盘图标并返回（调用方负责 show()）。

    callbacks: key 见 MENU_ITEMS；缺失的 key 自动接占位回调。
    截图菜单项约定用 callbacks["screenshot"]。
    """
    callbacks = callbacks or {}
    theme = _current_theme()
    color = T.THEMES[theme]["foreground"]
    icon = tinted_icon(TRAY_ICON_NAME, color, 16)

    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip(TRAY_TOOLTIP)

    menu = QMenu()
    for key, text in MENU_ITEMS:
        cb = callbacks.get(key) or _placeholder(text)
        action = menu.addAction(text)
        # triggered(bool) -> 零参槽：Qt 允许槽参数少于信号参数
        action.triggered.connect(lambda _checked=False, _cb=cb: _cb())
        if key in _SEPARATOR_AFTER:
            menu.addSeparator()
    tray.setContextMenu(menu)
    return tray
