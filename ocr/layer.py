"""识别文字覆盖层，坐标与截图的逻辑坐标一致。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTransform
from PySide6.QtWidgets import QApplication, QGraphicsItemGroup, QGraphicsTextItem, QMenu
from styles import tokens as T


class _SelectableTextItem(QGraphicsTextItem):
    selectionChanged = Signal()

    def __init__(self, text, mode, on_show_results=None):
        super().__init__(text)
        self._on_show_results = on_show_results
        self._hovered = False
        self._hover_color = QColor(T.THEMES[mode]["accent"])
        self._hover_color.setAlpha(160)
        self.setAcceptHoverEvents(True)

    def hoverEnterEvent(self, event):
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter, option, widget=None):
        # 悬停反馈只属于视图，scene.render 导出原图时不写入底色。
        if self._hovered and widget is not None:
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._hover_color)
            painter.drawRect(self.boundingRect())
            painter.restore()
        super().paint(painter, option, widget)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.selectionChanged.emit()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.selectionChanged.emit()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.selectionChanged.emit()

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        self.selectionChanged.emit()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        self.selectionChanged.emit()

    def contextMenuEvent(self, event):
        if self._on_show_results is None:
            super().contextMenuEvent(event)
        else:
            menu = QMenu(event.widget())
            selected = self.textCursor().selectedText().replace("\u2029", "\n")
            copy = menu.addAction("复制选中文字", lambda: QApplication.clipboard().setText(selected))
            copy.setEnabled(bool(selected))
            menu.addAction("查看识别全文…", self._on_show_results)
            menu.exec(event.screenPos())
            menu.deleteLater()
            event.accept()
        self.selectionChanged.emit()


def add_text_layer(scene, results, mode="light", on_show_results=None) -> QGraphicsItemGroup:
    """添加可选中文字层；调用方负责隐藏、移除以及避免把它写入原始图片。"""
    layer = QGraphicsItemGroup()
    layer.setHandlesChildEvents(False)
    layer.setZValue(1000)
    scene.addItem(layer)
    for result in results:
        if result.box.isEmpty() or not result.text:
            continue
        item = _SelectableTextItem(result.text, mode, on_show_results)
        item.document().setDocumentMargin(0)
        font = QFont()
        font.setPixelSize(max(1, result.box.height()))
        item.setFont(font)
        # 只隐藏未选中文字，保留 QTextControl 的选区高亮与复制；不能降低整个图元的不透明度。
        item.setDefaultTextColor(QColor(Qt.transparent))
        item.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        bounds = item.boundingRect()
        item.setTransform(QTransform().scale(result.box.width() / bounds.width(), result.box.height() / bounds.height()))
        item.setPos(result.box.topLeft())
        layer.addToGroup(item)
    return layer
