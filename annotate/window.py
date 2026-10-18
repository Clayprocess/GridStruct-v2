"""截图画布与浮动工具配置，输出与识别通过模块公开接口调用。"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QApplication, QGraphicsTextItem
from PySide6.QtGui import QPixmap, QColor, QKeySequence
from PySide6.QtCore import Qt, QPoint, QRect, Signal, QTimer, QPropertyAnimation, QEasingCurve, QEvent

from .canvas import AnnotationCanvas
from .toolbar import FloatPanel, shortcut_tool
from .options import ToolOptions
from .color_dialog import ColorDialog
from ui import floating_window_type, ask_confirmation
from styles import tokens as T
from export import save_image
from ocr import OCRController, OCRDocument, add_text_layer


class AnnotationWindow(QWidget):
    finished = Signal(QPixmap)
    cancelled = Signal()
    longshotRequested = Signal(QRect)

    def __init__(self, pixmap: QPixmap, pos: QPoint, theme_mode: str = "light", *,
                 scrollable: bool = False, capture_region: QRect | None = None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | floating_window_type())
        self._mode = theme_mode
        self._capture_position = QPoint(pos)
        # 长图和跨屏原生拼图的像素尺寸不代表原桌面采集视口，必须保留确认时的逻辑矩形。
        self._capture_region = QRect(capture_region) if capture_region is not None else QRect(pos, pixmap.deviceIndependentSize().toSize())
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(120)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._scrollable = scrollable
        self.setWindowTitle("GridStruct-V2 截图标注")
        self._ended = False
        self._text_layer = None
        self._pipette_tool = ""
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._canvas = AnnotationCanvas(pixmap)
        if scrollable:
            self._canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self._canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self._canvas.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._canvas)
        self.resize(pixmap.deviceIndependentSize().toSize())
        self.move(pos)

        self._toolbar = FloatPanel(mode=theme_mode, parent=self)
        self._options = ToolOptions(self._canvas)
        self._toolbar.set_options_widget(self._options)
        self._toolbar.toolSelected.connect(self._on_tool)
        self._toolbar.actionTriggered.connect(self._on_action)
        self._canvas.toolChanged.connect(self._tool_changed)
        self._canvas.optionsChanged.connect(self._options_changed)
        self._canvas.pipettePicked.connect(self._on_pipette_picked)
        self._ocr = OCRController(self, layer_first=True)
        self._ocr.recognized.connect(self._show_text_layer)
        self._ocr.statusChanged.connect(self._show_ocr_status)
        self._canvas._undo.indexChanged.connect(self._image_changed)
        self._options.set_tool("")
        self._position_toolbar()
        self._toolbar.show()
        QApplication.instance().installEventFilter(self)
        if scrollable:
            # 等窗口布局完成后再定位，避免 QGraphicsView 首次显示时自动居中。
            QTimer.singleShot(0, self, self._scroll_to_start)

    def _tool_changed(self, tool_id):
        if not tool_id:
            self._toolbar.clear_selection()

    def _options_changed(self, tool_id):
        QTimer.singleShot(0, self, self._position_toolbar)

    def showEvent(self, event):
        super().showEvent(event)
        self._fade.stop()
        self.setWindowOpacity(0)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def _position_toolbar(self):
        if self._scrollable:
            available = self.screen().availableGeometry()
            # 只限制视口，保留 scene 与 DPR；工具配置展开时也给浮条留足位置。
            height_limit = available.height() - self._toolbar.sizeHint().height() - T.SPACE_2
            rect = self._canvas.sceneRect()
            width = min(round(rect.width()) + self._canvas.verticalScrollBar().sizeHint().width(),
                        available.width())
            height = min(round(rect.height()) + self._canvas.horizontalScrollBar().sizeHint().height(),
                         height_limit)
            x = max(available.left(), min(self.x(), available.right() - width + 1))
            y = max(available.top(), min(self.y(), available.top() + height_limit - height))
            self.setGeometry(x, y, width, height)
        self._toolbar.set_position_near(self.geometry())

    def _scroll_to_start(self):
        self._canvas.horizontalScrollBar().setValue(self._canvas.horizontalScrollBar().minimum())
        self._canvas.verticalScrollBar().setValue(self._canvas.verticalScrollBar().minimum())

    def select_tool(self, tool_id):
        self._toolbar.set_tool(tool_id)

    def trigger_action(self, action):
        self._on_action(action)

    def _on_tool(self, tool_id):
        self._toggle_pipette(False)
        if self._text_layer is not None:
            self._show_ocr_status("已切换到标注，识别结果仍可查看全文", True)
        self._remove_text_layer()
        self._canvas.set_tool_by_id(tool_id)
        self.activateWindow()
        self._canvas.setFocus()

    def _toggle_pipette(self, on):
        if on:
            self._pipette_tool = self._canvas.tool_id
        self._canvas.set_pipette_mode(on)
        if on:
            self.activateWindow()
            self._canvas.setFocus()
        elif self._pipette_tool:
            self._canvas.set_tool_by_id(self._pipette_tool)
            self._pipette_tool = ""

    def _on_pipette_picked(self, color):
        self._toggle_pipette(False)
        dialog = ColorDialog(color, self)
        if dialog.exec() == ColorDialog.Accepted:
            QApplication.clipboard().setText(dialog.value.text())
            if dialog.apply_color.isChecked():
                self._canvas.update_style("color", QColor(color))
        dialog.deleteLater()

    def _remove_text_layer(self):
        if self._text_layer is not None:
            self._canvas.scene().removeItem(self._text_layer)
            self._text_layer.deleteLater()
            self._text_layer = None

    def _image_changed(self):
        if self._ocr.running or self._ocr.document is not None:
            self._ocr.invalidate("图像内容已变化，原识别结果已失效；请重新识别。")

    def _show_text_layer(self, results):
        # 拖动/文字编辑尚未写入撤销栈时，像素也可能已经改变。
        if isinstance(results, OCRDocument) and not results.matches(self._render()):
            self._ocr.invalidate("图像内容已变化，本次识别结果已失效；请重新识别。")
            return
        self._toggle_pipette(False)
        self._remove_text_layer()
        if results:
            # 只有挂载可选文字时进入选择模式；结果失效不能打断连续标注。
            self._canvas.clear_tool()
            self._text_layer = add_text_layer(self._canvas.scene(), results, self._mode,
                                              on_show_results=self._ocr.show_results)

    def _show_ocr_status(self, message, has_results):
        self._toolbar.set_ocr_status(message, has_results)
        self._position_toolbar()

    def _render(self):
        # 可选中文字只辅助复制，不覆盖原图中的像素。
        if self._text_layer is not None:
            self._text_layer.hide()
        pixmap = self._canvas.render_to_pixmap()
        if self._text_layer is not None:
            self._text_layer.show()
        return pixmap

    def _on_action(self, action):
        if action == "undo":
            self._canvas.undo()
        elif action == "redo":
            self._canvas.redo()
        elif action in ("confirm", "copy"):
            self._finish()
        elif action == "save":
            save_image(self._render(), self)
        elif action == "ocr":
            self._toggle_pipette(False)
            self._ocr.start(self._render())
        elif action == "ocr_results":
            self._ocr.show_results()
        elif action == "pipette":
            self._toggle_pipette(not self._canvas.pipette_active)
        elif action == "longshot":
            self.longshotRequested.emit(QRect(self._capture_region))
        elif action == "pin":
            from pin import show_pin
            pixmap = self._render()
            document = self._ocr.document
            if document is not None and not document.matches(pixmap):
                self._ocr.invalidate("图像内容已变化，贴图需要重新识别。")
                document = None
            show_pin(pixmap, self._capture_position, document=document)
            self.close()
        elif action == "cancel":
            self._request_cancel()

    def _finish(self):
        self._canvas.finish_editing()
        self._ended = True
        self.finished.emit(self._render())
        self.close()

    def _request_cancel(self):
        if ask_confirmation(self, "退出截图", "确认退出截图？未复制或保存的标注将丢失。"):
            self.close()

    def eventFilter(self, obj, event):
        if event.type() != QEvent.KeyPress or not isinstance(obj, QWidget):
            return False
        if obj.window() not in (self, self._toolbar):
            return False
        text = self._canvas.scene().focusItem()
        if (isinstance(text, QGraphicsTextItem) and text.textInteractionFlags() & Qt.TextEditable
                and (obj is self._canvas or obj is self._canvas.viewport())):
            return False
        # 面板控件会先消化字母和撤销，工具命令必须在 QWidget 的局部编辑之前分发。
        tool = shortcut_tool(event)
        if tool:
            self.select_tool(tool)
        elif event.matches(QKeySequence.Undo):
            self._canvas.undo()
        elif event.matches(QKeySequence.Redo):
            self._canvas.redo()
        else:
            return False
        event.accept()
        return True

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key_Escape:
            if self._canvas.pipette_active:
                self._toggle_pipette(False)
            elif not self._canvas.finish_editing():
                self._request_cancel()
        elif modifiers & Qt.ControlModifier and key in (Qt.Key_S, Qt.Key_C):
            self._on_action("save" if key == Qt.Key_S else "copy")
        elif modifiers & Qt.ShiftModifier and key == Qt.Key_O:
            self._on_action("ocr")
        elif shortcut_tool(event):
            self.select_tool(shortcut_tool(event))
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        self._ocr.cancel()
        self._canvas.clear_tool()
        self._canvas.set_pipette_mode(False)
        self._toolbar.close()
        if not self._ended:
            self._ended = True
            self.cancelled.emit()
        super().closeEvent(event)
