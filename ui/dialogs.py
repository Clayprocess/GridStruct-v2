# 对话框基类（AGENTS.md 约定）。统一样式由 styles/theme.py 的全局 QSS 提供，
# 这里不写任何样式。

import sys

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from styles import tokens as T


class BaseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GridStruct-V2")
        # 截图遮罩和贴图始终置顶，提示必须在它们上方才能完成当前操作。
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(120)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)

    def showEvent(self, event):
        super().showEvent(event)
        # 从最小化还原也会收到（系统发的）显示事件，那时不再淡入，否则像闪了一下。
        if event.spontaneous():
            return
        self._fade.stop()
        self.setWindowOpacity(0)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()


class MessageDialog(BaseDialog):
    def __init__(self, parent, title, text, *, confirm_text="确定",
                 cancel_text=None, checkbox_text=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_4, T.SPACE_4, T.SPACE_4, T.SPACE_4)
        layout.setSpacing(T.SPACE_3)
        message = QLabel(text)
        message.setWordWrap(True)
        message.setMaximumWidth(480)
        layout.addWidget(message)
        self.checkbox = QCheckBox(checkbox_text, self) if checkbox_text else None
        if self.checkbox is not None:
            layout.addWidget(self.checkbox)
        actions = QHBoxLayout()
        actions.addStretch()
        if cancel_text:
            cancel = QPushButton(cancel_text)
            cancel.setProperty("class", "outline")
            cancel.clicked.connect(self.reject)
            actions.addWidget(cancel)
        confirm = QPushButton(confirm_text)
        confirm.setProperty("class", "primary")
        confirm.clicked.connect(self.accept)
        actions.addWidget(confirm)
        layout.addLayout(actions)


def show_message(parent, title, text):
    dialog = MessageDialog(parent, title, text)
    result = dialog.exec()
    dialog.deleteLater()
    return result


def ask_confirmation(parent, title, text, *, confirm_text="确定", cancel_text="取消"):
    dialog = MessageDialog(parent, title, text, confirm_text=confirm_text, cancel_text=cancel_text)
    accepted = dialog.exec() == QDialog.Accepted
    dialog.deleteLater()
    return accepted


def standard_window(dialog):
    """识别窗口当普通窗口用：能最小化、最大化，任务栏有自己的图标（实测决定）。

    任务栏图标只给没有所有者的窗口，调用方要传 parent=None 并自己持有引用。
    """
    dialog.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
                          | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)


def set_topmost(window, on):
    """切换置顶而不重建原生窗口。

    setWindowFlag 会销毁重建原生窗口：窗口先隐藏再显示、淡入重放，看着闪一下。
    这里只改 QWindow 的标志（Windows 上 Qt 用 SetWindowPos 切换 TOPMOST），控件记录同步改，不重建。
    """
    flags = window.windowFlags()
    flags = flags | Qt.WindowStaysOnTopHint if on else flags & ~Qt.WindowStaysOnTopHint
    handle = window.windowHandle()
    if handle is None:
        window.setWindowFlags(flags)
        return
    window.overrideWindowFlags(flags)
    handle.setFlags(flags)
    if sys.platform == "win32":
        # 个别 Qt 版本去掉置顶时不发 HWND_NOTOPMOST，直接告诉系统一次。
        import ctypes
        topmost, not_topmost = -1, -2
        no_size_move_activate = 0x0001 | 0x0002 | 0x0010
        ctypes.windll.user32.SetWindowPos(int(window.winId()), topmost if on else not_topmost,
                                          0, 0, 0, 0, no_size_move_activate)


def topmost_button(dialog, key):
    """识别窗口右上角的图钉：是否置顶由用户选，默认不置顶，记住上次选择（实测决定）。

    截图会话打开识别窗口后就结束，窗口不必再为压过截图遮罩而强制置顶。
    """
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QToolButton
    from settings import get_settings
    from .icons import tinted_icon
    settings = get_settings()
    t = T.THEMES[QApplication.instance().property("theme_mode") or "light"]
    button = QToolButton()
    button.setCheckable(True)
    button.setToolTip("窗口置顶")
    button.setAccessibleName("窗口置顶")
    icon = tinted_icon("pin", t["muted_foreground"], 16, QIcon(), QIcon.Normal, QIcon.Off)
    button.setIcon(tinted_icon("pin", t["foreground"], 16, icon, QIcon.Normal, QIcon.On))
    on = settings.value(key, False, type=bool)
    button.setChecked(on)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, on)

    def toggle(value):
        settings.setValue(key, value)
        set_topmost(dialog, value)

    button.toggled.connect(toggle)
    return button
