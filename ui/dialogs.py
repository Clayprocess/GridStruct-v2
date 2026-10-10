# 对话框基类（AGENTS.md 约定）。统一样式由 styles/theme.py 的全局 QSS 提供，
# 这里不写任何样式。

from PySide6.QtWidgets import QDialog


class BaseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PixShot")
