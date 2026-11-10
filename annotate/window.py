"""截图画布与浮动工具配置，输出与识别通过模块公开接口调用。"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QApplication, QGraphicsTextItem, QDialog, QLineEdit, QAbstractSpinBox, QComboBox,
)
from PySide6.QtGui import QPixmap, QKeySequence
from PySide6.QtCore import Qt, QPoint, QRect, Signal, QTimer, QEvent

from .canvas import AnnotationCanvas
from .toolbar import FloatPanel, shortcut_tool
from .options import ToolOptions
from ui import ask_confirmation, floating_window_type, MessageDialog, show_toast
from ui.motion import animation
from settings import get_settings
from styles import tokens as T
from export import save_image
from ocr import OCRController, OCRDocument, add_text_layer


# 开着的标注窗口：托盘退出前逐个按 Esc 的规则确认。
_open_windows = []


def confirm_discard_all():
    """托盘退出前调用；任何一个标注窗口取消，就不退出。"""
    return all(window.confirm_discard("退出 GridStruct-V2", "退出") for window in list(_open_windows))


def _confirm_exit(parent, lost="未复制或保存的标注", question="退出截图", confirm_text="退出"):
    """有未保存的标注或改过的识别文字时确认；勾选“不再提醒”后直接通过，可在设置里恢复。"""
    settings = get_settings()
    if not settings.value("capture/confirm_exit", True, type=bool):
        return True
    dialog = MessageDialog(parent, question, f"确认{question}？{lost}将丢失。",
                           confirm_text=confirm_text, cancel_text="取消", checkbox_text="不再提醒")
    accepted = dialog.exec() == QDialog.Accepted
    if accepted and dialog.checkbox.isChecked():
        settings.setValue("capture/confirm_exit", False)
    dialog.deleteLater()
    return accepted


class AnnotationWindow(QWidget):
    finished = Signal(QPixmap)
    cancelled = Signal()
    longshotRequested = Signal(QRect)

    def __init__(self, pixmap: QPixmap, pos: QPoint, theme_mode: str = "light", *,
                 scrollable: bool = False, capture_region: QRect | None = None,
                 toolbar_pos: QPoint | None = None, longshot: bool = True):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | floating_window_type())
        self._mode = theme_mode
        self._capture_position = QPoint(pos)
        # 长图和跨屏原生拼图的像素尺寸不代表原桌面采集视口，必须保留确认时的逻辑矩形。
        self._capture_region = QRect(capture_region) if capture_region is not None else QRect(pos, pixmap.deviceIndependentSize().toSize())
        self._fade = animation(self, b"windowOpacity", T.MOTION_FAST)
        self._scrollable = scrollable
        self.setWindowTitle("GridStruct-V2 截图标注")
        self._ended = False
        self._text_layer = None
        self._ocr_selected_text = ""
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
        self._toolbar.set_copy_text_selected(False)
        # 框选阶段拖动过工具条，进入标注后留在同一位置。
        self._toolbar.set_manual_position(toolbar_pos)
        if not longshot:
            # 贴图上标注：贴图不对应屏幕上可滚动的区域，长截图无从谈起。
            self._toolbar.hide_action("longshot")
        self._options = ToolOptions(self._canvas)
        self._toolbar.set_options_widget(self._options)
        self._toolbar.toolSelected.connect(self._on_tool)
        self._toolbar.actionTriggered.connect(self._on_action)
        self._canvas.toolChanged.connect(self._tool_changed)
        self._canvas.optionsChanged.connect(self._options_changed)
        self._ocr = OCRController(self, layer_first=True)
        self._ocr.recognized.connect(self._show_text_layer)
        self._ocr.statusChanged.connect(self._show_ocr_status)
        self._canvas._undo.indexChanged.connect(self._image_changed)
        self._canvas._undo.canUndoChanged.connect(self._toolbar.set_undo_enabled)
        self._options.set_tool("")
        self._position_toolbar()
        self._toolbar.show()
        QApplication.instance().installEventFilter(self)
        _open_windows.append(self)
        if scrollable:
            # 等窗口布局完成后再定位，避免 QGraphicsView 首次显示时自动居中。
            QTimer.singleShot(0, self, self._scroll_to_start)

    def _tool_changed(self, tool_id):
        if tool_id:
            self._toolbar.mark_tool(tool_id)
        else:
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
        if self._text_layer is not None:
            self._remove_text_layer()
            self._show_ocr_status("已切换到标注，全文仍可修改、复制", True)
        self._canvas.set_tool_by_id(tool_id)
        self.activateWindow()
        self._canvas.setFocus()

    def _remove_text_layer(self):
        self._ocr_selected_text = ""
        self._toolbar.set_copy_text_selected(False)
        if self._text_layer is not None:
            self._canvas.scene().removeItem(self._text_layer)
            self._text_layer.deleteLater()
            self._text_layer = None
        # 文字层没了就不能再和截图互相定位，卡片里的全文照旧可改、可复制。
        self._toolbar.set_ocr_document(self._toolbar.ocr_document, False)

    def _ocr_edited(self):
        document = self._toolbar.ocr_document
        return document is not None and document.draft is not None

    def _image_changed(self):
        if not self._ocr.running and self._ocr_edited():
            # 改过的文字是用户自己核对出来的，标注后仍留着可复制；识别结果只对应标注前的截图，
            # 不再定位，贴图、表格识别也不会复用它（它们按图像版本核对）。
            self._remove_text_layer()
            self._show_ocr_status("截图已有标注，全文不再对照截图定位，仍可修改、复制", True)
        elif self._ocr.running or self._ocr.document is not None:
            self._ocr.invalidate("图像内容已变化，原识别结果已失效；请重新识别。")

    def _show_text_layer(self, results):
        # 拖动/文字编辑尚未写入撤销栈时，像素也可能已经改变。
        if isinstance(results, OCRDocument) and not results.matches(self._render()):
            self._ocr.invalidate("图像内容已变化，本次识别结果已失效；请重新识别。")
            return
        self._remove_text_layer()
        if results:
            # 只有挂载可选文字时进入选择模式；结果失效不能打断连续标注。
            self._canvas.clear_tool()
            self._text_layer = add_text_layer(self._canvas.scene(), results, self._mode,
                                              on_show_results=self._open_ocr_results)
            self._text_layer.selectionChanged.connect(self._select_ocr_text)
        self._toolbar.set_ocr_document(results if isinstance(results, OCRDocument) else None,
                                       self._text_layer is not None)

    def _open_ocr_results(self):
        # 同表格识别：复核窗口接手这张图，截图会话随即结束，桌面不再被截图窗口锁住。
        self._ocr.show_results()
        self.close()

    def _select_ocr_text(self, _item, text):
        self._ocr_selected_text = text
        self._toolbar.set_copy_text_selected(bool(text))

    def _show_ocr_status(self, message, has_results):
        self._toolbar.set_ocr_status(message, has_results, self._ocr.state)
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
            self._focus_canvas()
        elif action == "redo":
            self._canvas.redo()
            self._focus_canvas()
        elif action == "confirm" and self._ocr_selected_text:
            # 完成原图选字时不能再发图片输出信号，否则会覆盖刚复制的文字。
            QApplication.clipboard().setText(self._ocr_selected_text)
            show_toast("已复制选中文字")
            self.close()
        elif action in ("confirm", "copy"):
            self._finish()
        elif action == "save":
            if save_image(self._render(), self):
                # 已保存的标注退出时不会丢，不必再确认；之后再改又会变回未保存。
                self._canvas._undo.setClean()
            self._focus_canvas()
        elif action == "ocr":
            if self._ocr_edited() and not ask_confirmation(
                    self, "重新识别", "重新识别会按新结果重新整理全文，你改过的文字会丢掉。", confirm_text="重新识别"):
                return
            self._ocr.start(self._render())
        elif action == "ocr_results":
            self._open_ocr_results()
        elif action == "ocr_copy":
            # 卡片里的“复制文字”：复制改过的全文并结束截图，不再发图片输出信号。
            QApplication.clipboard().setText(self._toolbar.ocr_document.text())
            show_toast("已复制文字")
            self.close()
        elif action == "ocr_cancel":
            self._ocr.cancel()
        elif action == "table":
            # 表格窗口接手这张图继续校对，同贴图一样结束截图会话；同图已有 OCR 时直接复用。
            from table import open_table
            pixmap = self._render()
            document = self._ocr.document
            open_table(pixmap, document.results if document is not None and document.matches(pixmap) else None)
            self.close()
        elif action == "longshot":
            # 改做长截图会关掉当前截图，和 Esc 同一条确认规则。
            if self.confirm_discard("改做长截图", "开始长截图"):
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
        elif action in ("cancel", "escape"):
            # 同 PixPin：Esc 在任何状态下都是关闭截图；正在输入的文字、折线先提交，再按有无未保存标注决定是否确认。
            self._request_cancel()

    def _finish(self):
        self._canvas.finish_editing()
        self._ended = True
        self.finished.emit(self._render())
        self.close()

    def _focus_canvas(self):
        self._canvas.restore_focus()

    def _request_cancel(self):
        if self.confirm_discard():
            self.close()

    def confirm_discard(self, question="退出截图", confirm_text="退出"):
        """用户主动丢弃这张截图（Esc、工具条关闭/取消、托盘退出、改做长截图）先经过这里：
        有未复制/保存的标注或改过的识别文字时确认，没有就直接通过。
        复制、贴图、转表格等已把内容交出去的出口直接 close()，不经过这里。"""
        self._canvas.finish_editing()
        annotated, edited = not self._canvas._undo.isClean(), self._ocr_edited()
        if not (annotated or edited):
            return True
        lost = "、".join(text for text, show in (("改过的识别文字", edited), ("未复制或保存的标注", annotated)) if show)
        return _confirm_exit(self, lost, question, confirm_text)

    def eventFilter(self, obj, event):
        if event.type() != QEvent.KeyPress or not isinstance(obj, QWidget):
            return False
        if obj.window() not in (self, self._toolbar):
            return False
        if QApplication.activePopupWidget() is not None:
            return False
        if event.key() == Qt.Key_Escape:
            # 文字框和面板控件都会先吃掉 Esc，统一在这里截下。
            self._on_action("escape")
            event.accept()
            return True
        if event.key() == Qt.Key_F8 and self._toolbar.ocr_document is not None:
            self._toolbar.ocr_editor.next_pending()
            event.accept()
            return True
        if obj is self._toolbar.ocr_editor:
            # 在识别全文里打字：Enter 换行，字母、Shift+数字、Ctrl+Z、Ctrl+C 都归编辑框，不切工具、不撤销标注。
            return False
        text = self._canvas.scene().focusItem()
        if (isinstance(text, QGraphicsTextItem) and text.textInteractionFlags() & Qt.TextEditable
                and (obj is self._canvas or obj is self._canvas.viewport())):
            return False
        if event.matches(QKeySequence.Copy) and not isinstance(obj, (QLineEdit, QAbstractSpinBox, QComboBox)):
            if self._ocr_selected_text:
                # 原图选字时 Ctrl+C 只复制文字、留在截图里，可以接着选下一段。
                QApplication.clipboard().setText(self._ocr_selected_text)
                show_toast("已复制选中文字")
            else:
                self._on_action("copy")
            event.accept()
            return True
        # 面板控件会先消化字母和撤销，工具命令必须在 QWidget 的局部编辑之前分发。
        tool = shortcut_tool(event)
        if tool:
            self.select_tool(tool)
        elif event.matches(QKeySequence.Undo):
            self._on_action("undo")
        elif event.matches(QKeySequence.Redo):
            self._on_action("redo")
        else:
            return False
        event.accept()
        return True

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key_Escape:
            self._on_action("escape")
        elif modifiers & Qt.ControlModifier and key in (Qt.Key_S, Qt.Key_C):
            self._on_action("save" if key == Qt.Key_S else "copy")
        elif modifiers & Qt.ShiftModifier and key == Qt.Key_O:
            self._on_action("ocr")
        elif shortcut_tool(event):
            self.select_tool(shortcut_tool(event))
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._on_action("confirm")
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        if self in _open_windows:
            _open_windows.remove(self)
        self._ocr.cancel()
        self._canvas.clear_tool()
        self._toolbar.close()
        if not self._ended:
            self._ended = True
            self.cancelled.emit()
        super().closeEvent(event)
