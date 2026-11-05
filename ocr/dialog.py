"""文字识别窗口：左边原图可缩放、滑选，右边是一份可直接修改的全文（复核窗口方案 A，Clay 2026-11-05 定）。

布局与表格识别窗口一致：上方工具行、中间原图对照、底部状态 + 主操作。
上方切换的是排版，不是视图。全文的编辑、双向定位与低置信度标记在 editor.py，截图里的识别卡片也用它。
"""

from PySide6.QtCore import QPointF, Qt, Slot
from PySide6.QtGui import QKeySequence, QPainter, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QFrame, QGraphicsScene, QGraphicsTextItem, QGraphicsView,
    QHBoxLayout, QLabel, QPushButton, QSplitter, QToolButton, QVBoxLayout,
)
from styles import tokens as T
from ui import BaseDialog, PreviewZoom, show_toast, standard_window, topmost_button
from ui.motion import SelectionPill
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


class OCRResultDialog(BaseDialog):
    def __init__(self, results, parent=None, pixmap=None):
        super().__init__(parent)
        from settings import get_settings
        self._settings = get_settings()
        image = pixmap.toImage() if pixmap is not None else None
        document = results if isinstance(results, OCRDocument) else OCRDocument(results, image)
        if pixmap is None and not document.image.isNull():
            pixmap = QPixmap.fromImage(document.image)
        self.setWindowTitle("文字识别 · 本地")
        standard_window(self)
        self.resize(1100, 680)
        self._mode = QApplication.instance().property("theme_mode") or "light"
        self._layer = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_3, T.SPACE_3, T.SPACE_3, T.SPACE_3)
        layout.setSpacing(T.SPACE_2)
        layout.addLayout(self._build_toolbar())

        self._preview = _ImagePreview()
        self._preview.setMinimumWidth(200)
        self._preview.setScene(QGraphicsScene(self._preview))
        self._zoom = PreviewZoom(self._preview, self._preview, self)

        self._text = OCRTextEdit(self._mode)
        self._text.marksChanged.connect(self._update_status)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._preview)
        splitter.addWidget(self._text)
        splitter.setSizes([560, 520])
        layout.addWidget(splitter, 1)
        layout.addLayout(self._build_footer())

        self._next_shortcut = QShortcut(QKeySequence("F8"), self, self._text.next_pending)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self._copy_all)
        self._load(document, pixmap)

    def _load(self, document, pixmap):
        """pixmap 为空表示历史纯文字结果，没有原图可对照。"""
        self._document, self._pixmap = document, pixmap
        if pixmap is not None:
            scene = self._preview.scene()
            scene.addPixmap(pixmap).setTransformationMode(Qt.SmoothTransformation)
            self._layer = add_text_layer(scene, document, self._mode)
            size = pixmap.deviceIndependentSize()
            scene.setSceneRect(0, 0, size.width(), size.height())
            self._zoom.fit()
        self._preview.setVisible(pixmap is not None)
        self._to_table.setEnabled(pixmap is not None)
        document.changed.connect(self._show_layout)
        document.focused.connect(self._scroll_preview)
        self._text.set_document(document)
        self._show_layout()

    # ---- 构造 ----

    def _build_toolbar(self):
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        caption = QLabel("排版")
        caption.setObjectName("OCRStatus")
        row.addWidget(caption)
        frame = QFrame()
        frame.setObjectName("SegmentGroup")
        segments = QHBoxLayout(frame)
        segments.setContentsMargins(1, 1, 1, 1)
        segments.setSpacing(2)
        pill = SelectionPill(frame, "background", "border")
        group = QButtonGroup(frame)
        self._layout_buttons = {}
        for mode, label, tip in LAYOUTS:
            button = QToolButton()
            button.setObjectName("Segment")
            button.setProperty("pill", True)
            button.setText(label)
            button.setToolTip(tip)
            button.setCheckable(True)
            button.toggled.connect(lambda on, value=mode, target=button: on and (pill.track(target), self._choose_layout(value)))
            group.addButton(button)
            segments.addWidget(button)
            self._layout_buttons[mode] = button
        row.addWidget(frame)
        row.addStretch()
        # 只影响复制出去的文字，不改全文。
        self._no_spaces = QCheckBox("去空格")
        self._no_spaces.setToolTip("复制时去掉所有空格")
        row.addWidget(self._no_spaces)
        row.addWidget(topmost_button(self, "window/ocr_topmost"))
        return row

    def _build_footer(self):
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        self._status = QLabel()
        self._status.setObjectName("OCRStatus")
        row.addWidget(self._status, 1)
        self._pending = QLabel()
        self._pending.setObjectName("OCRPending")
        self._pending.setToolTip("模型置信度较低的文字标了波浪线，仅供参考；文字正确时无需修改")
        self._next = QPushButton("下一处")
        self._next.setProperty("class", "ghost")
        self._next.setToolTip("跳到下一处低置信度文字 (F8)")
        self._next.clicked.connect(self._text.next_pending)
        row.addWidget(self._pending)
        row.addWidget(self._next)
        row.addSpacing(T.SPACE_2)
        self._to_table = QPushButton("按表格整理…")
        self._to_table.setProperty("class", "outline")
        self._to_table.setToolTip("复用本次识别的文字，在原图上划分行列后导出 Excel")
        self._to_table.clicked.connect(self._open_table)
        self._copy = QPushButton("复制全文")
        self._copy.setProperty("class", "primary")
        self._copy.setToolTip("复制右侧全部文字 (Ctrl+Shift+C)")
        self._copy.clicked.connect(self._copy_all)
        row.addWidget(self._to_table)
        row.addWidget(self._copy)
        return row

    # ---- 排版与状态 ----

    def _choose_layout(self, mode):
        if not self._text.choose_layout(mode):
            self._show_layout()

    @Slot()
    def _show_layout(self):
        self._layout_buttons[self._document.layout].setChecked(True)

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
        for widget in (self._pending, self._next):
            widget.setVisible(count > 0)
        self._next_shortcut.setEnabled(count > 0)

    # ---- 联动 ----

    @Slot(object)
    def _scroll_preview(self, ids):
        wanted = set(ids)
        if self._layer is not None:
            visible = self._preview.mapToScene(self._preview.viewport().rect()).boundingRect()
            for item in self._layer.childItems():
                rect = item.sceneBoundingRect()
                # 只在这一行完全不在视野里时才滚：在原图上点字也会走到这里，放大后一点就跳会很怪；
                # 整行比视野还宽时对准行首，而不是被拉到行尾。
                if wanted.intersection(item._ids) and not visible.intersects(rect):
                    rect.setWidth(min(rect.width(), visible.width() / 2))
                    self._preview.ensureVisible(rect, 20, 20)

    # ---- 输出 ----

    def _copy_all(self):
        text = self._document.text()
        if self._no_spaces.isChecked():
            text = text.replace(" ", "").replace("\u3000", "")
        QApplication.clipboard().setText(text)
        show_toast("已复制全文")

    def _open_table(self):
        from table import open_table
        open_table(self._pixmap, self._document.results)
