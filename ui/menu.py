"""弹出菜单的圆角：QSS 的 border-radius 只画边框，窗口本身仍是方的，四角会露出方块。"""

from PySide6.QtCore import Qt


def round_corners(menu):
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    # 系统阴影是按方形窗口画的，留着会在圆角外再框出一个方影，所以一起关掉。
    menu.setAttribute(Qt.WA_TranslucentBackground)
    return menu
