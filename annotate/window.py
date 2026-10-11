"""截图画布与浮动工具配置，输出与识别通过模块公开接口调用。"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolButton, QApplication, QMessageBox
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtCore import Qt, QPoint, Signal, QTimer

from .canvas import AnnotationCanvas
from .toolbar import FloatPanel
from .options import ToolOptions
from .color_dialog import ColorDialog
from ui import floating_window_type, tinted_icon
from styles import tokens as T
from export import save_image
from ocr import OCRController, add_text_layer


class AnnotationWindow(QWidget):
    finished = Signal(QPixmap)
    cancelled = Signal()

    def __init__(self, pixmap: QPixmap, pos: QPoint, theme_mode: str = "light", *,
                 scrollable: bool = False):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | floating_window_type())
        self._mode = theme_mode
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
        self._pip_btn = QToolButton()
        self._pip_btn.setIcon(tinted_icon("pipette", T.THEMES[theme_mode]["foreground"], 16))
        self._pip_btn.setProperty("class", "ghost")
        self._pip_btn.setToolTip("取色：点击截图，选择 Hex / RGB / HSV")
        self._pip_btn.setAccessibleName("取色")
        self._pip_btn.setCheckable(True)
        self._pip_btn.toggled.connect(self._toggle_pipette)
        self._toolbar.layout().addWidget(self._pip_btn, 0, Qt.AlignRight)
        self._toolbar.toolSelected.connect(self._on_tool)
        self._toolbar.actionTriggered.connect(self._on_action)
        self._canvas.toolChanged.connect(self._tool_changed)
        self._canvas.pipettePicked.connect(self._on_pipette_picked)
        self._ocr = OCRController(self, layer_first=True)
        self._ocr.recognized.connect(self._show_text_layer)
        self._ocr.statusChanged.connect(self._show_ocr_status)
        self._options.set_tool("")
        self._position_toolbar()
        self._toolbar.show()
        if scrollable:
            # 等窗口布局完成后再定位，避免 QGraphicsView 首次显示时自动居中。
            QTimer.singleShot(0, self, self._scroll_to_start)

    def _tool_changed(self, tool_id):
        self._options.set_tool(tool_id)
        if not tool_id:
            self._toolbar.clear_selection()
        QTimer.singleShot(0, self, self._position_toolbar)

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
        self._pip_btn.setChecked(False)
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
        self._pip_btn.setChecked(False)
        dialog = ColorDialog(color, self)
        if dialog.exec() == ColorDialog.Accepted:
            QApplication.clipboard().setText(dialog.value.text())
            if dialog.apply_color.isChecked():
                self._canvas.update_style("color", QColor(color))

    def _remove_text_layer(self):
        if self._text_layer is not None:
            self._canvas.scene().removeItem(self._text_layer)
            self._text_layer = None

    def _show_text_layer(self, results):
        self._pip_btn.setChecked(False)
        self._remove_text_layer()
        self._canvas.clear_tool()
        if results:
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
            self._pip_btn.setChecked(False)
            self._ocr.start(self._render())
        elif action == "ocr_results":
            self._ocr.show_results()
        elif action == "pin":
            from pin import show_pin
            show_pin(self._render(), self.pos())
            self.close()
        elif action == "cancel":
            self._request_cancel()

    def _finish(self):
        self._canvas.finish_editing()
        self._ended = True
        self.finished.emit(self._render())
        self.close()

    def _request_cancel(self):
        if QMessageBox.question(self, "退出截图", "确认退出截图？未复制或保存的标注将丢失。",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.close()

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key_Escape:
            if self._canvas.pipette_active:
                self._pip_btn.setChecked(False)
            elif not self._canvas.finish_editing():
                self._request_cancel()
        elif modifiers & Qt.ControlModifier and key in (Qt.Key_S, Qt.Key_C):
            self._on_action("save" if key == Qt.Key_S else "copy")
        elif modifiers & Qt.ShiftModifier and key == Qt.Key_O:
            self._on_action("ocr")
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._ocr.cancel()
        self._canvas.clear_tool()
        self._canvas.set_pipette_mode(False)
        self._toolbar.close()
        if not self._ended:
            self._ended = True
            self.cancelled.emit()
        super().closeEvent(event)
