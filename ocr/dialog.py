"""共享识别工作台内的文字校对面板；每页保留编辑器与原生撤销历史。"""

from PySide6.QtCore import QEvent, QPointF, QRectF, QSignalBlocker, QSize, Qt, Slot
from PySide6.QtGui import QKeySequence, QPainter, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGraphicsScene, QGraphicsTextItem, QGraphicsView,
    QHBoxLayout, QLabel, QSplitter, QToolButton, QVBoxLayout, QWidget,
)
from styles import tokens as T
from ui import PreviewZoom, paint_svg, show_toast, tinted_icon
from .editor import OCRTextEdit
from .layer import add_text_layer
from .layout import LAYOUTS
from .model import OCRDocument


class _ImagePreview(QGraphicsView):
    """原图预览，缩放与拖动交给 ui.zoom（同表格识别）。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("OCRPreview")
        self.setAlignment(Qt.AlignCenter)
        # 缩小显示时不开平滑，文字笔画会被隔行丢掉，看着发虚、锯齿。
        self.setRenderHint(QPainter.SmoothPixmapTransform)

    def zoom_scale(self):
        return self.transform().m11()

    def set_zoom_scale(self, scale):
        self.setTransform(QTransform.fromScale(scale, scale))

    def fit_zoom_scale(self, size):
        rect = self.sceneRect()
        # 历史纯文字结果没有原图，预览隐藏，场景为空。
        if rect.isEmpty():
            return 1.0
        return min(size.width() / rect.width(), size.height() / rect.height())

    def to_content(self, point):
        return self.mapToScene(point)

    def from_content(self, point):
        return QPointF(self.mapFromScene(point))

    def pans_at(self, point):
        return not isinstance(self.itemAt(point), QGraphicsTextItem)


class _TextPreview(_ImagePreview):
    def __init__(self, editor):
        super().__init__()
        self.setObjectName("OCRResultView")
        self.editor = editor
        self.setScene(QGraphicsScene(self))
        editor.setFixedSize(620, 440)
        self.proxy = self.scene().addWidget(editor)
        self.setSceneRect(self.proxy.boundingRect().adjusted(-80, -80, 80, 80))

    def pans_at(self, point):
        return self.itemAt(point) is None


class _TextZoom(PreviewZoom):
    def eventFilter(self, watched, event):
        # 正文保留空格输入和滚动，画布空白仍沿用共享缩放和平移。
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease):
            if self._target.editor.hasFocus() and event.key() == Qt.Key_Space:
                return False
        if event.type() == QEvent.Wheel:
            point = self._area.viewport().mapFromGlobal(event.globalPosition().toPoint())
            if not self._target.pans_at(point) and not event.modifiers() & Qt.ControlModifier:
                return False
        return super().eventFilter(watched, event)


class _CopySpaceButton(QToolButton):
    def sizeHint(self):
        return super().sizeHint() + QSize(20, 0)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isChecked():
            color = T.THEMES[QApplication.instance().property("theme_mode") or "light"]["table_selected_foreground"]
            painter = QPainter(self)
            paint_svg(painter, "check", color, QRectF(self.width() - 16, (self.height() - 12) / 2, 12, 12))
            painter.end()


class OCRReviewPane(QWidget):
    def __init__(self, document, pixmap, parent=None):
        super().__init__(parent)
        from table import FloatingTools
        self.setObjectName("OCRReview")
        self._document, self._pixmap = document, pixmap
        self._mode = QApplication.instance().property("theme_mode") or "light"
        self._layer = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_2)
        self._preview = _ImagePreview()
        self._preview.setMinimumWidth(200)
        self._preview.setScene(QGraphicsScene(self._preview))
        self._zoom = PreviewZoom(self._preview, self._preview, self, shortcut=False)
        self._text = OCRTextEdit(self._mode)
        self._text.setProperty("review", True)
        self._result_view = _TextPreview(self._text)
        self.result_zoom = _TextZoom(self._result_view, self._result_view, self, shortcut=False)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._preview)
        self.splitter.addWidget(self._result_view)
        self.splitter.setSizes([450, 550])
        layout.addWidget(self.splitter, 1)

        self._tools = FloatingTools(self._result_view)
        self._pending = QLabel()
        self._pending.setObjectName("TablePending")
        self._pending.setToolTip("模型置信度较低，仅供参考；文字正确时无需修改")
        self._next = self._button("下一处", "arrow-right", "下一处低置信度文字 (F8)")
        self._next.clicked.connect(self._next_pending)
        self._pending_group = self._tools.group(self._pending, self._next)
        self._layouts = QComboBox()
        self._layouts.setAccessibleName("文字排版")
        for mode, label, tip in LAYOUTS:
            self._layouts.addItem(label, mode)
            self._layouts.setItemData(self._layouts.count() - 1, tip, Qt.ToolTipRole)
        self._layouts.currentIndexChanged.connect(self._choose_layout)
        self._no_spaces = self._button("去空格", "eraser", "仅复制时去掉普通和全角空格，不修改正文", _CopySpaceButton)
        self._no_spaces.setCheckable(True)
        self._tools.group(self._layouts, self._no_spaces)

        footer = QHBoxLayout()
        self._status = QLabel()
        self._status.setObjectName("OCRStatus")
        footer.addWidget(self._status, 1)
        self._copy = self._button("复制全文", "copy", "复制全部文字 (Ctrl+Shift+C)")
        self._copy.setProperty("class", "outline")
        self._copy.clicked.connect(self._copy_all)
        footer.addWidget(self._copy)
        layout.addLayout(footer)
        self._next_shortcut = QShortcut(QKeySequence("F8"), self, self._next_pending)
        self._copy_shortcut = QShortcut(QKeySequence("Ctrl+Shift+C"), self, self._copy_all)
        self._fit_shortcut = QShortcut(QKeySequence("Ctrl+0"), self, self._fit_active)
        for shortcut in (self._next_shortcut, self._copy_shortcut, self._fit_shortcut):
            shortcut.setContext(Qt.WindowShortcut)
        self._text.marksChanged.connect(self._update_status)
        document.changed.connect(self._show_layout)
        document.focused.connect(self._scroll_preview)
        self._text.set_document(document)
        self._show_layout()
        self.set_source_pixmap(pixmap)
        self._zoom.fit()
        self.result_zoom.fit()

    def set_source_pixmap(self, pixmap):
        """文件页离开时卸载原图，回来重渲；不重绑全文以保留原生撤销栈。"""
        self._zoom.stop_pan()
        self._pixmap = pixmap
        self._layer = None
        scene = self._preview.scene()
        scene.clear()
        scene.setSceneRect(0, 0, 0, 0)
        if pixmap is not None:
            scene.addPixmap(pixmap).setTransformationMode(Qt.SmoothTransformation)
            self._layer = add_text_layer(scene, self._document, self._mode)
            size = pixmap.deviceIndependentSize()
            scene.setSceneRect(0, 0, size.width(), size.height())
        self._preview.setVisible(pixmap is not None)
        self._update_status()

    def _sync_shortcuts(self):
        visible = self.isVisible()
        self._next_shortcut.setEnabled(visible and bool(self._text.pending_spans()))
        self._copy_shortcut.setEnabled(visible)
        self._fit_shortcut.setEnabled(visible)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_shortcuts()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_shortcuts()

    @property
    def preview(self):
        return self._preview

    @property
    def zoom(self):
        return self._zoom

    def _button(self, text, icon, tip, button_type=QToolButton):
        button = button_type()
        button.setText(text)
        button.setIcon(tinted_icon(icon, T.THEMES[self._mode]["table_foreground"], 16))
        button.setIconSize(QSize(16, 16))
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setProperty("class", "ghost")
        button.setProperty("labelled", True)
        button.setToolTip(tip)
        return button

    def _next_pending(self):
        # 代理内的正文焦点还需要外层视图激活，否则模式栏仍会吃掉后续输入。
        self._result_view.setFocus(Qt.OtherFocusReason)
        self._text.next_pending()

    def _fit_active(self):
        if self._text.hasFocus() or self._result_view.hasFocus():
            self.result_zoom.fit()
        else:
            self._zoom.fit()

    def _choose_layout(self, index):
        self._text.choose_layout(self._layouts.itemData(index))
        self._show_layout()

    @Slot()
    def _show_layout(self):
        with QSignalBlocker(self._layouts):
            self._layouts.setCurrentIndex(self._layouts.findData(self._document.layout))

    @Slot()
    def _update_status(self):
        if not self._document:
            message = "未检测到文字"
        else:
            message = "已修改，复制以这里为准" if self._document.draft is not None else "可直接修改文字"
            if self._layer is not None:
                message += " · 点原图上的字，定位到这里"
        self._status.setText(message)
        count = len(self._text.pending_spans())
        self._pending.setText(f"低置信度 {count} 处")
        self._pending_group.setVisible(count > 0)
        self._sync_shortcuts()
        self._tools.place()

    @Slot(object)
    def _scroll_preview(self, ids):
        wanted = set(ids)
        if self._layer is not None:
            visible = self._preview.mapToScene(self._preview.viewport().rect()).boundingRect()
            for item in self._layer.childItems():
                rect = item.sceneBoundingRect()
                if wanted.intersection(item._ids) and not visible.intersects(rect):
                    rect.setWidth(min(rect.width(), visible.width() / 2))
                    self._preview.ensureVisible(rect, 20, 20)

    def _copy_all(self):
        text = self._document.text()
        if self._no_spaces.isChecked():
            text = text.replace(" ", "").replace("\u3000", "")
        QApplication.clipboard().setText(text)
        show_toast("已复制全文")


def OCRResultDialog(results, parent=None, pixmap=None):
    """兼容旧调用点；返回唯一公共窗口，由调用者照常 show。"""
    from table import open_text_workbench
    image = pixmap.toImage() if pixmap is not None else None
    document = results if isinstance(results, OCRDocument) else OCRDocument(results, image)
    if pixmap is None and not document.image.isNull():
        pixmap = QPixmap.fromImage(document.image)
    return open_text_workbench(document, pixmap, parent=parent)
