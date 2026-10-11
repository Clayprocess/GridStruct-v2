# 图片和文字贴图共用窗口状态；持久化由 pin 模块管理。

from pathlib import Path
from uuid import uuid4

from PySide6.QtWidgets import (
    QWidget, QLabel, QTextEdit, QMenu, QFileDialog,
    QApplication, QGraphicsView, QGraphicsScene,
    QGraphicsTextItem, QFrame,
)
from PySide6.QtGui import QPixmap, QGuiApplication, QFont, QPainter
from PySide6.QtCore import Qt, QPoint, QSize, Signal, QPropertyAnimation, QEasingCurve

from styles import tokens as T
from ui import floating_window_type, MessageDialog, tinted_icon, show_message

_MIN_SCALE = 0.2
_MAX_SCALE = 5.0
_STATUS_HEIGHT = 26


def _fg_icon(name):
    mode = QApplication.instance().property("theme_mode") or "light"
    return tinted_icon(name, T.THEMES[mode]["foreground"], 16)


class _ConfirmDialog(MessageDialog):
    def __init__(self, title, message, parent=None):
        super().__init__(parent, title, message, confirm_text="确认", cancel_text="取消")


class _PinBase(QWidget):
    stateChanged = Signal()
    closed = Signal(object)
    kind = ""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | floating_window_type())
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.pin_id = uuid4().hex
        self._scale = 1.0
        self._opacity = 1.0
        self._topmost = True
        self._click_through = False
        self._allow_close = False
        self._closed = False
        self._closing = False
        self._close_ready = False
        self._entered = False
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(150)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._fade_finished)
        self._drag_off = None
        self._ocr_status = ""
        self._status = QLabel(self)
        self._status.setWordWrap(True)
        self._status.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setToolTip("按住状态栏拖动贴图；滚轮缩放；右键打开菜单")
        self._update_status()

    def _update_status(self):
        suffix = " · 鼠标穿透（托盘可退出）" if self._click_through else ""
        text = f"缩放: {self._scale:.0%}  不透明度: {self.windowOpacity():.0%}{suffix}"
        if self._ocr_status:
            text += "\n" + self._ocr_status
        self._status.setText(text)
        self._status.setToolTip(text + "\n拖动状态栏移动贴图；滚轮缩放；右键打开菜单")

    def _resize_content(self):
        size = self._content_size()
        self._content.setGeometry(0, 0, size.width(), size.height())
        self._update_status()
        status_height = max(_STATUS_HEIGHT, self._status.heightForWidth(size.width()))
        self._status.setGeometry(0, size.height(), size.width(), status_height)
        self.resize(size.width(), size.height() + status_height)

    def zoom(self, factor: float, anchor: QPoint | None = None):
        old_scale = self._scale
        self._scale = min(_MAX_SCALE, max(_MIN_SCALE, old_scale * factor))
        if anchor is None:
            anchor = self._content.rect().center()
        screen_anchor = self.mapToGlobal(anchor)
        ratio = self._scale / old_scale
        self._resize_content()
        self.move(screen_anchor - QPoint(round(anchor.x() * ratio), round(anchor.y() * ratio)))
        self.stateChanged.emit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.1 ** (delta / 120), self.mapFromGlobal(event.globalPosition().toPoint()))
        event.accept()

    def set_opacity(self, opacity: float):
        if self._closing:
            return
        self._opacity = min(1.0, max(0.1, opacity))
        self._animate_opacity(self._opacity * (0.5 if self._click_through else 1.0))
        self._update_status()
        self.stateChanged.emit()

    def set_topmost(self, enabled: bool):
        if self._closing:
            return
        visible = self.isVisible()
        self._topmost = enabled
        self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
        if visible:
            self.show()
        self.stateChanged.emit()

    def set_click_through(self, enabled: bool):
        if self._closing:
            return
        visible = self.isVisible()
        current = self.windowOpacity()
        self._click_through = enabled
        self.setWindowFlag(Qt.WindowTransparentForInput, enabled)
        if visible:
            self.show()
        self.setWindowOpacity(current)
        self._animate_opacity(self._opacity * (0.5 if enabled else 1.0))
        self._update_status()
        self.stateChanged.emit()

    def _animate_opacity(self, target):
        self._fade.stop()
        if not self.isVisible():
            self.setWindowOpacity(target)
            return
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(target)
        self._fade.start()

    def showEvent(self, event):
        super().showEvent(event)
        # 改窗口 flag 会重新发送 showEvent，只在首次显示时做入场动画。
        if not self._entered:
            self._entered = True
            self.setWindowOpacity(0)
            self._animate_opacity(self._opacity * (0.5 if self._click_through else 1.0))

    def _fade_finished(self):
        if self._closing:
            self._close_ready = True
            self.close()
        else:
            self._update_status()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_off = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_off is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_off = None

    def moveEvent(self, event):
        super().moveEvent(event)
        self.stateChanged.emit()

    def closeEvent(self, event):
        # WA_DeleteOnClose 的删除发生在事件循环后；重复 close 不应再次发送关闭信号。
        if self._closed:
            event.accept()
            return
        if self._closing and not self._close_ready:
            event.ignore()
            return
        if not self._allow_close:
            dialog = _ConfirmDialog("关闭贴图", "确认关闭贴图？关闭后下次启动不会恢复此贴图。", self)
            accepted = dialog.exec()
            dialog.deleteLater()
            if not accepted:
                event.ignore()
                return
            self._allow_close = True
        if not self._close_ready and self.isVisible():
            self._closing = True
            event.ignore()
            self._animate_opacity(0)
            return
        self._closed = True
        self.closed.emit(self)
        event.accept()

    def contextMenuEvent(self, event):
        self._show_menu(event.globalPos())

    def _show_menu(self, pos):
        menu = QMenu(self)
        menu.addAction(_fg_icon("copy"), "复制图片" if self.kind == "image" else "复制全部文字", self.copy)
        if self.kind == "text" and self._content.textCursor().hasSelection():
            menu.addAction("复制选中文字", self._content.copy)
        if self.kind == "image":
            menu.addAction(_fg_icon("scan-text"), "文本识别", self._recognize)
            if self._text_layer is not None:
                menu.addAction("查看识别全文", self._ocr_controller.show_results)
        menu.addAction(_fg_icon("save"), "保存为文件", self._save)
        menu.addSeparator()
        top = menu.addAction(_fg_icon("pin"), "窗口置顶")
        top.setCheckable(True)
        top.setChecked(self._topmost)
        top.triggered.connect(self.set_topmost)
        through = menu.addAction("鼠标穿透（从托盘退出）")
        through.setCheckable(True)
        through.setChecked(self._click_through)
        through.triggered.connect(self.set_click_through)
        opacity = menu.addMenu("调整透明度")
        opacity.addAction("增加 10%", lambda: self.set_opacity(self._opacity + 0.1))
        opacity.addAction("减少 10%", lambda: self.set_opacity(self._opacity - 0.1))
        opacity.addSeparator()
        for percent in range(100, 0, -10):
            action = opacity.addAction(f"{percent}%")
            action.setCheckable(True)
            action.setChecked(round(self._opacity * 100) == percent)
            action.triggered.connect(lambda checked=False, value=percent: self.set_opacity(value / 100))
        menu.addSeparator()
        menu.addAction(_fg_icon("x"), "关闭贴图", self.close)
        menu.exec(pos)
        menu.deleteLater()

    def state(self):
        return {
            "id": self.pin_id, "kind": self.kind,
            "x": self.x(), "y": self.y(), "scale": self._scale,
            "opacity": self._opacity, "topmost": self._topmost,
            "click_through": self._click_through,
        }

    def restore_state(self, state):
        self.pin_id = state["id"]
        self._scale = state["scale"]
        self._resize_content()
        self.move(state["x"], state["y"])
        if not any(screen.geometry().intersects(self.geometry()) for screen in QGuiApplication.screens()):
            self.move(QGuiApplication.primaryScreen().availableGeometry().topLeft())
        # 恢复时先一次性准备窗口属性，由模块注册后统一显示，避免中途重复淡入。
        self._opacity = state["opacity"]
        self._topmost = state["topmost"]
        self._click_through = state["click_through"]
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self._topmost)
        self.setWindowFlag(Qt.WindowTransparentForInput, self._click_through)
        self.setWindowOpacity(self._opacity * (0.5 if self._click_through else 1.0))
        self._update_status()


class _PinImageView(QGraphicsView):
    def __init__(self, pixmap, parent):
        super().__init__(parent)
        scene = QGraphicsScene(self)
        scene.addPixmap(pixmap)
        size = pixmap.deviceIndependentSize()
        scene.setSceneRect(0, 0, size.width(), size.height())
        self.setScene(scene)
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self._moving_pin = False

    def mousePressEvent(self, event):
        if (event.button() == Qt.LeftButton
                and not isinstance(self.itemAt(event.position().toPoint()), QGraphicsTextItem)):
            self._moving_pin = True
            self.parentWidget().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._moving_pin:
            self.parentWidget().mouseMoveEvent(event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._moving_pin:
            self.parentWidget().mouseReleaseEvent(event)
            self._moving_pin = False
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        self.parentWidget().wheelEvent(event)

    def contextMenuEvent(self, event):
        if isinstance(self.itemAt(event.pos()), QGraphicsTextItem):
            super().contextMenuEvent(event)
        else:
            self.parentWidget()._show_menu(event.globalPos())


class PinWindow(_PinBase):
    kind = "image"

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.setWindowTitle("图片贴图")
        self._base = QPixmap(pixmap)
        self._ocr_controller = None
        self._text_layer = None
        self._content = _PinImageView(self._base, self)
        self._content.setToolTip("拖动图片空白处或状态栏移动；识别后可滑选文字并复制")
        self._resize_content()

    def _content_size(self):
        self._content.resetTransform()
        self._content.scale(self._scale, self._scale)
        return (self._base.deviceIndependentSize() * self._scale).toSize().expandedTo(QSize(1, 1))

    def copy(self):
        QGuiApplication.clipboard().setPixmap(self._base)

    def _save(self):
        path, selected = QFileDialog.getSaveFileName(
            self, "保存贴图", "贴图.png", "PNG (*.png);;JPG (*.jpg);;BMP (*.bmp)")
        if path:
            suffix = {"PNG (*.png)": ".png", "JPG (*.jpg)": ".jpg", "BMP (*.bmp)": ".bmp"}[selected]
            if not Path(path).suffix:
                path += suffix
            if not self._base.save(path):
                show_message(self, "保存失败", f"无法写入图片：{path}")

    def _recognize(self):
        from ocr import OCRController
        if self._ocr_controller is None:
            self._ocr_controller = OCRController(self, layer_first=True)
            self._ocr_controller.recognized.connect(self._show_text_layer)
            self._ocr_controller.statusChanged.connect(self._show_ocr_status)
        self._ocr_controller.start(self._base)

    def _show_ocr_status(self, message, has_results):
        self._ocr_status = message + ("；右键查看识别全文" if has_results else "")
        self._resize_content()

    def _show_text_layer(self, results):
        from ocr import add_text_layer
        scene = self._content.scene()
        if self._text_layer is not None:
            scene.removeItem(self._text_layer)
            self._text_layer = None
        if results:
            mode = QApplication.instance().property("theme_mode") or "light"
            self._text_layer = add_text_layer(
                scene, results, mode, on_show_results=self._ocr_controller.show_results)
        self._content.setFocus()

    def closeEvent(self, event):
        was_closed = self._closed
        super().closeEvent(event)
        if not was_closed and event.isAccepted() and self._ocr_controller is not None:
            self._ocr_controller.cancel()

    def state(self):
        return {**super().state(), "dpr": self._base.devicePixelRatio()}


class _PinTextEdit(QTextEdit):
    def mouseDoubleClickEvent(self, event):
        self.setReadOnly(False)
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        self.setReadOnly(True)
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        self.parentWidget().wheelEvent(event)

    def contextMenuEvent(self, event):
        self.parentWidget()._show_menu(event.globalPos())


class PinTextWindow(_PinBase):
    kind = "text"

    def __init__(self, text: str):
        super().__init__()
        self.setWindowTitle("文字贴图")
        self._content = _PinTextEdit(self)
        self._content.setAcceptRichText(False)
        self._content.setPlainText(text)
        self._content.setReadOnly(True)
        self._content.setToolTip("可直接滑选文字，双击编辑；拖动底部状态栏移动贴图")
        self._content.textChanged.connect(self.stateChanged.emit)
        self._resize_content()

    def _content_size(self):
        font = QFont(self._content.font())
        font.setPixelSize(max(1, round(T.FONT_MD * self._scale)))
        self._content.setFont(font)
        self._content.document().setDefaultFont(font)
        return QSize(round(360 * self._scale), round(240 * self._scale))

    def copy(self):
        QGuiApplication.clipboard().setText(self._content.toPlainText())

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存文字贴图", "贴图.txt", "文本文件 (*.txt)")
        if path:
            if not Path(path).suffix:
                path += ".txt"
            try:
                Path(path).write_text(self._content.toPlainText(), encoding="utf-8")
            except OSError as error:
                show_message(self, "保存失败", str(error))

    def state(self):
        return {**super().state(), "text": self._content.toPlainText()}
