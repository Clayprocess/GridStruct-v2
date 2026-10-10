# OCR 结果对话框：文本列表 + 复制全部。

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QListWidget, QPushButton, QHBoxLayout,
    QApplication, QListWidgetItem,
)
from PySide6.QtCore import Qt

from .engine import OCRResult


class OCRResultDialog(QDialog):
    def __init__(self, results: list[OCRResult], parent=None):
        super().__init__(parent)
        self.setWindowTitle("识别结果")
        self.setMinimumSize(400, 300)
        self._results = results

        layout = QVBoxLayout(self)
        self._list = QListWidget()
        for r in results:
            QListWidgetItem(f"{r.text}  ({r.score:.0%})", self._list)
        layout.addWidget(self._list)

        btns = QHBoxLayout()
        copy_all = QPushButton("复制全部")
        copy_all.clicked.connect(self._copy_all)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(copy_all)
        btns.addWidget(close)
        layout.addLayout(btns)

        self._list.itemDoubleClicked.connect(
            lambda it: QApplication.clipboard().setText(it.text()))

    def _copy_all(self):
        text = "\n".join(r.text for r in self._results)
        QApplication.clipboard().setText(text)
        self.accept()
