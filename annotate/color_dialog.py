"""取色确认后按所选格式复制，并可应用到当前标注。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QVBoxLayout, QFormLayout, QLabel, QComboBox,
                             QLineEdit, QCheckBox, QDialogButtonBox)
from ui import BaseDialog
from settings import get_settings


class ColorDialog(BaseDialog):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.setWindowTitle("取色确认")
        self.color = QColor(color)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        preview = QLabel()
        pixmap = QPixmap(300, 64)
        pixmap.fill(self.color)
        preview.setPixmap(pixmap)
        preview.setAlignment(Qt.AlignCenter)
        layout.addWidget(preview)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItems(["Hex", "RGB", "HSV"])
        self.format.setCurrentText(get_settings().value("color_format", "Hex"))
        self.value = QLineEdit()
        self.value.setReadOnly(True)
        form.addRow("颜色格式", self.format)
        form.addRow("颜色值", self.value)
        layout.addLayout(form)
        self.apply_color = QCheckBox("同时应用到当前标注工具")
        self.apply_color.setChecked(True)
        layout.addWidget(self.apply_color)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确认并复制")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.format.currentTextChanged.connect(self._update_value)
        self._update_value()

    def _update_value(self):
        c = self.color
        values = {"Hex": c.name().upper(), "RGB": f"rgb({c.red()}, {c.green()}, {c.blue()})",
                  "HSV": f"hsv({max(0, c.hue())}, {round(c.saturationF() * 100)}%, {round(c.valueF() * 100)}%)"}
        self.value.setText(values[self.format.currentText()])

    def accept(self):
        get_settings().setValue("color_format", self.format.currentText())
        super().accept()
