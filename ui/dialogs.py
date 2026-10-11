# 对话框基类（AGENTS.md 约定）。统一样式由 styles/theme.py 的全局 QSS 提供，
# 这里不写任何样式。

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
