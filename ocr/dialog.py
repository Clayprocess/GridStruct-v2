"""OCR 全文复核、原图文字层及复制选项。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout,
)
from ui import BaseDialog
from .layer import add_text_layer
from .layout import format_text


class _ImagePreview(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def showEvent(self, event):
        super().showEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)


class OCRResultDialog(BaseDialog):
    def __init__(self, results, parent=None, pixmap=None):
        super().__init__(parent)
        from settings import get_settings
        self._settings = get_settings()
        self.setWindowTitle("识别结果 · 本地 OCR")
        self.resize(860, 520)
        self._results = results
        self._selection_source = None
        self._selection_text = ""
        self._balanced = False
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("识别语言"))
        language = QComboBox()
        language.addItem("中文 / English（本地自动识别）")
        row.addWidget(language)
        row.addStretch()
        self._show_image = QCheckBox("显示原图与文字层")
        self._show_image.setChecked(pixmap is not None)
        self._show_image.setEnabled(pixmap is not None)
        row.addWidget(self._show_image)
        layout.addLayout(row)
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._preview = _ImagePreview()
        self._preview.setMinimumWidth(160)
        scene = QGraphicsScene(self._preview)
        self._preview.setScene(scene)
        if pixmap is not None:
            scene.addPixmap(pixmap)
            mode = QApplication.instance().property("theme_mode") or "light"
            layer = add_text_layer(scene, results, mode)
            for item in layer.childItems():
                item.selectionChanged.connect(lambda item=item: self._selection_changed(item, item.textCursor().selectedText()))
            scene.setSceneRect(0, 0, pixmap.deviceIndependentSize().width(), pixmap.deviceIndependentSize().height())
        self._preview.setVisible(pixmap is not None)
        self._show_image.toggled.connect(self._preview.setVisible)
        self._splitter.addWidget(self._preview)
        self._text = QPlainTextEdit(format_text(results))
        self._text.setMinimumWidth(160)
        self._text.setPlaceholderText("未检测到文字")
        self._splitter.addWidget(self._text)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([400, 400])
        layout.addWidget(self._splitter)
        formats = QHBoxLayout()
        self._no_breaks = QCheckBox("去换行")
        self._no_spaces = QCheckBox("去空格")
        self._copy_on_close = QCheckBox("关闭时复制")
        self._direct = QCheckBox("下次直接复制")
        self._direct.setChecked(self._settings.value("ocr/direct_copy", False, type=bool))
        self._direct.toggled.connect(lambda value: self._settings.setValue("ocr/direct_copy", value))
        self._auto_close = QCheckBox("复制后关闭")
        for widget in (self._no_breaks, self._no_spaces, self._copy_on_close, self._direct, self._auto_close):
            formats.addWidget(widget)
        layout.addLayout(formats)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self._copy_selection = QPushButton("复制选中")
        self._copy_selection.setProperty("class", "outline")
        self._copy_selection.setEnabled(False)
        self._text.selectionChanged.connect(lambda: self._selection_changed(self._text, self._text.textCursor().selectedText()))
        self._copy_selection.clicked.connect(self._copy_selected)
        all_text = QPushButton("复制全部")
        all_text.setProperty("class", "primary")
        all_text.clicked.connect(self._copy_all)
        close = QPushButton("关闭")
        close.setProperty("class", "secondary")
        close.clicked.connect(self.accept)
        for widget in (self._copy_selection, all_text, close):
            buttons.addWidget(widget)
        layout.addLayout(buttons)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._balanced:
            half = max(1, self._splitter.width() // 2)
            self._splitter.setSizes([half, half])
            self._balanced = True
        self._preview.fitInView(self._preview.sceneRect(), Qt.KeepAspectRatio)

    def _selection_changed(self, source, text):
        if text:
            self._selection_source = source
            self._selection_text = text.replace("\u2029", "\n")
        elif self._selection_source is source:
            self._selection_text = ""
        self._copy_selection.setEnabled(bool(self._selection_text))

    def _formatted(self, text):
        if self._no_breaks.isChecked():
            text = text.replace("\n", "")
        if self._no_spaces.isChecked():
            text = text.replace(" ", "").replace("\u3000", "")
        return text

    def _copy_all(self):
        QApplication.clipboard().setText(self._formatted(self._text.toPlainText()))
        if self._auto_close.isChecked():
            self.accept()

    def _copy_selected(self):
        QApplication.clipboard().setText(self._formatted(self._selection_text))
        if self._auto_close.isChecked():
            self.accept()

    def done(self, result):
        if self._copy_on_close.isChecked():
            QApplication.clipboard().setText(self._formatted(self._text.toPlainText()))
        super().done(result)
