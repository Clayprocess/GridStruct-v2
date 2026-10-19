"""共享校正文字的原图覆盖层；四点几何映射至截图逻辑坐标。"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPolygonF, QTransform
from PySide6.QtWidgets import QApplication, QGraphicsObject, QGraphicsTextItem, QMenu
from styles import tokens as T
from .model import OCRDocument


class _SelectableTextItem(QGraphicsTextItem):
    selectionChanged = Signal(object, str)

    def __init__(self, result, layer):
        super().__init__(result.text, layer)
        self._ids = result.source_ids or (result.item_id,)
        self._layer = layer
        self._hovered = False
        self._focused = False
        self.setAcceptHoverEvents(True)
        self.document().setDocumentMargin(0)
        font = QFont()
        font.setPixelSize(max(1, result.box.height()))
        self.setFont(font)
        self.setDefaultTextColor(QColor(Qt.transparent))
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        bounds = self.boundingRect()
        source = QPolygonF([bounds.topLeft(), bounds.topRight(), bounds.bottomRight(), bounds.bottomLeft()])
        if result.polygon:
            dpr = layer.document.image.devicePixelRatio()
            target = QPolygonF([QPointF(x / dpr, y / dpr) for x, y in result.polygon])
        else:
            box = result.box
            target = QPolygonF([QPointF(box.left(), box.top()), QPointF(box.left() + box.width(), box.top()),
                                QPointF(box.left() + box.width(), box.top() + box.height()),
                                QPointF(box.left(), box.top() + box.height())])
        # 三参数重载明确返回成功标志，避免不同绑定把元组或 None 交给 setTransform。
        transform = QTransform()
        if not QTransform.quadToQuad(source, target, transform):
            delta = target[0] - source[0]
            transform = QTransform.fromTranslate(delta.x(), delta.y())
        self.setTransform(transform)
        self.selectionChanged.connect(layer.selectionChanged)

    def hoverEnterEvent(self, event):
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter, option, widget=None):
        # 视图定位和选字反馈不写入 scene.render 的图片导出。
        if widget is None:
            return
        # 不调用 QGraphicsTextItem.paint：Qt 会用不透明的 HighlightedText 重画选中的识别文字，
        # 盖住原图字形；这里只画半透明的选区和悬停底色，原图像素始终可见。
        painter.save()
        painter.setPen(Qt.NoPen)
        if self._hovered or self._focused:
            painter.setBrush(QColor(*T.OCR_HOVER))
            painter.drawRect(self.boundingRect())
        cursor = self.textCursor()
        if cursor.hasSelection():
            painter.setBrush(QColor(*T.OCR_SELECTION))
            for rect in self._selection_rects(cursor.selectionStart(), cursor.selectionEnd()):
                painter.drawRect(rect)
        painter.restore()

    def _selection_rects(self, start, end):
        layout = self.document().documentLayout()
        block = self.document().begin()
        while block.isValid():
            origin = layout.blockBoundingRect(block).topLeft()
            lines = block.layout()
            for index in range(lines.lineCount()):
                line = lines.lineAt(index)
                first = max(start, block.position() + line.textStart()) - block.position()
                last = min(end, block.position() + line.textStart() + line.textLength()) - block.position()
                if first < last:
                    left, right = line.cursorToX(first)[0], line.cursorToX(last)[0]
                    yield QRectF(origin.x() + left, origin.y() + line.y(), right - left, line.height())
            block = block.next()

    def _emit_selection(self):
        self.selectionChanged.emit(self, self.textCursor().selectedText().replace("\u2029", "\n"))

    def mousePressEvent(self, event):
        self._layer.document.focus(self._ids)
        super().mousePressEvent(event)
        self._emit_selection()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self._emit_selection()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._emit_selection()

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        self._emit_selection()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        self._emit_selection()

    def contextMenuEvent(self, event):
        if self._layer.on_show_results is None:
            super().contextMenuEvent(event)
        else:
            self._layer.document.focus(self._ids)
            menu = QMenu(event.widget())
            selected = self.textCursor().selectedText().replace("\u2029", "\n")
            copy = menu.addAction("复制选中文字", lambda: QApplication.clipboard().setText(selected))
            copy.setEnabled(bool(selected))
            menu.addAction("复制此条校正文字", lambda: QApplication.clipboard().setText(self.toPlainText()))
            menu.addAction("查看与校正文字…", self._layer.on_show_results)
            menu.exec(event.screenPos())
            menu.deleteLater()
            event.accept()
        self._emit_selection()


class _TextLayer(QGraphicsObject):
    selectionChanged = Signal(object, str)

    def __init__(self, document, mode, on_show_results):
        super().__init__()
        self.document = document
        self.mode = mode
        self.on_show_results = on_show_results
        self._focused_ids = document.focused_ids
        self._items = []
        self.setZValue(1000)
        self.document.changed.connect(self._rebuild)
        self.document.focused.connect(self._focus)
        self._rebuild()

    def boundingRect(self):
        return self.childrenBoundingRect()

    def paint(self, painter, option, widget=None):
        pass

    @Slot()
    def _rebuild(self):
        self.prepareGeometryChange()
        # Qt 对象销毁自动断开文档信号，层释放后文档仍能供别的窗口使用。
        for item in self.childItems():
            item.setVisible(False)
            item.setParentItem(None)
            if item.scene() is not None:
                item.scene().removeItem(item)
            item.deleteLater()
        self.selectionChanged.emit(self, "")
        self._items = [_SelectableTextItem(result, self) for result in self.document.results
                       if result.text and not result.box.isEmpty()]
        self._focus(self._focused_ids)

    @Slot(object)
    def _focus(self, ids):
        self._focused_ids = tuple(ids)
        selected = set(ids)
        for item in self.childItems():
            item._focused = bool(selected.intersection(item._ids))
            item.update()


def add_text_layer(scene, results, mode="light", on_show_results=None):
    """添加共享文字层；旧列表调用沿用原有语义，原图导出不含选字反馈。"""
    document = results if isinstance(results, OCRDocument) else OCRDocument(results)
    layer = _TextLayer(document, mode, on_show_results)
    scene.addItem(layer)
    return layer
