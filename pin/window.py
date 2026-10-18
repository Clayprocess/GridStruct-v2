# 图片和文字贴图共用窗口状态；持久化由 pin 模块管理。

from pathlib import Path
from uuid import uuid4

from math import ceil

from PySide6.QtWidgets import (
    QWidget, QLabel, QTextEdit, QMenu, QFileDialog,
    QApplication, QGraphicsView, QGraphicsScene,
    QGraphicsTextItem, QFrame,
)
from PySide6.QtGui import QPixmap, QGuiApplication, QFont, QPainter, QColor, QPen
from PySide6.QtCore import Qt, QEvent, QPoint, QRectF, QSize, Signal, QPropertyAnimation, QEasingCurve, QTimer

from styles import tokens as T
from ui import floating_window_type, MessageDialog, tinted_icon, show_message

_MIN_SCALE = 0.2
_MAX_SCALE = 5.0
_SHADOW = len(T.PIN_SHADOW_ALPHAS)
# 阴影外再留 1px 描边，内容从这里开始；贴图位置、会话坐标都以内容左上角为准。
_FRAME = _SHADOW + 1
_TEXT_PADDING = 12
_TEXT_MIN_WIDTH = 160
_TEXT_MAX_WIDTH = 480
_TEXT_MAX_HEIGHT = 360
# OCR 进行中提示常驻，结束后按较长时长淡出，方便读完结果说明。
_OCR_FINAL_STATES = {"idle", "completed", "empty", "failed", "timeout", "cancelled"}


def _theme():
    return T.THEMES[QApplication.instance().property("theme_mode") or "light"]


def _fg_icon(name):
    return tinted_icon(name, _theme()["foreground"], 16)


class _ConfirmDialog(MessageDialog):
    def __init__(self, title, message, parent=None):
        super().__init__(parent, title, message, confirm_text="确认", cancel_text="取消")


class _PinBase(QWidget):
    stateChanged = Signal()
    closed = Signal(object)
    kind = ""
    _padding = 0
    _radius = 0

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | floating_window_type())
        self.setAttribute(Qt.WA_DeleteOnClose)
        # 阴影需要半透明窗口；卡片和阴影都由 paintEvent 画。
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.pin_id = uuid4().hex
        self._scale = 1.0
        self._opacity = 1.0
        self._topmost = True
        self._locked = False
        # 中键在原始大小和上一次缩放/透明度之间切换，记住切换前的值。
        self._before_reset = None
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
        # 缩放、透明度、OCR 状态只在变化时浮出提示，贴图平时只有图本身。
        self._hud = QLabel(self)
        self._hud.setObjectName("PinHud")
        self._hud.setWordWrap(True)
        self._hud.setAlignment(Qt.AlignCenter)
        self._hud.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._hud.hide()
        self._hud_timer = QTimer(self)
        self._hud_timer.setSingleShot(True)
        self._hud_timer.timeout.connect(self._hud.hide)

    @property
    def _origin(self):
        return QPoint(_FRAME + self._padding, _FRAME + self._padding)

    def move_content_to(self, pos: QPoint):
        """让贴图内容（而不是阴影边缘）落在截图原位置。"""
        self.move(pos - self._origin)

    def _show_hud(self, text, duration=T.PIN_HUD_MS):
        card = self.rect().adjusted(_FRAME, _FRAME, -_FRAME, -_FRAME)
        self._hud.setText(text)
        self._hud.setFixedWidth(min(self._hud.sizeHint().width(), max(1, card.width() - 16)))
        self._hud.setFixedHeight(self._hud.heightForWidth(self._hud.width()))
        self._hud.move(card.center().x() - self._hud.width() // 2,
                       max(card.top(), card.bottom() - self._hud.height() - 10))
        self._hud.show()
        self._hud.raise_()
        if duration:
            self._hud_timer.start(duration)
        else:
            self._hud_timer.stop()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange:
            self.update()

    def _state_color(self):
        # 参照 PixPin：阴影颜色表达贴图状态，多张贴图并排时一眼看出哪张在操作、哪张已穿透。
        if self._click_through:
            return QColor(T.PIN_STATE_COLORS["click_through"])
        if self._locked:
            return QColor(T.PIN_STATE_COLORS["locked"])
        if self.isActiveWindow():
            return QColor(T.PIN_STATE_COLORS["active"])
        return None

    def paintEvent(self, _event):
        t = _theme()
        state = self._state_color()
        card = QRectF(self.rect()).adjusted(_SHADOW + 0.5, _SHADOW + 0.5, -_SHADOW - 0.5, -_SHADOW - 0.5)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            # 由外向内叠加半透明圆角矩形，近似柔和投影，避免引入图形效果的离屏开销。
            for inset, alpha in enumerate(T.PIN_SHADOW_ALPHAS):
                spread = _SHADOW - inset
                color = QColor(state) if state is not None else QColor(0, 0, 0)
                # 彩色光晕比黑影淡，需要加倍 alpha 才看得出。
                color.setAlpha(alpha * 2 if state is not None else alpha)
                p.setBrush(color)
                p.drawRoundedRect(card.adjusted(-spread, -spread, spread, spread),
                                  self._radius + spread, self._radius + spread)
            p.setBrush(QColor(t["popover"]))
            p.setPen(QPen(state if state is not None else QColor(t["border"]), 1))
            p.drawRoundedRect(card, self._radius, self._radius)

    def _resize_content(self):
        size = self._content_size()
        self._content.setGeometry(self._origin.x(), self._origin.y(), size.width(), size.height())
        margin = 2 * (_FRAME + self._padding)
        self.resize(size.width() + margin, size.height() + margin)

    def zoom(self, factor: float, anchor: QPoint | None = None):
        old_scale = self._scale
        self._scale = min(_MAX_SCALE, max(_MIN_SCALE, old_scale * factor))
        if anchor is None:
            anchor = self._content.geometry().center()
        screen_anchor = self.mapToGlobal(anchor)
        ratio = self._scale / old_scale
        offset = anchor - self._origin
        self._resize_content()
        self.move(screen_anchor - self._origin - QPoint(round(offset.x() * ratio), round(offset.y() * ratio)))
        self._show_hud(f"{self._scale:.0%}")
        self.stateChanged.emit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta and not self._locked:
            if event.modifiers() & Qt.ControlModifier:
                self.set_opacity(self._opacity + T.PIN_OPACITY_STEP * delta / 120)
            else:
                self.zoom(1.1 ** (delta / 120), self.mapFromGlobal(event.globalPosition().toPoint()))
        event.accept()

    def toggle_original(self, anchor: QPoint | None = None):
        """中键：回到 100% 大小和不透明度；再按一次恢复之前的值（同 PixPin）。"""
        if self._locked:
            return
        if (self._scale, self._opacity) != (1.0, 1.0):
            self._before_reset = (self._scale, self._opacity)
            target = (1.0, 1.0)
        elif self._before_reset is not None:
            target, self._before_reset = self._before_reset, None
        else:
            return
        self.zoom(target[0] / self._scale, anchor)
        self.set_opacity(target[1])
        self._show_hud(f"{self._scale:.0%} · 不透明度 {self._opacity:.0%}")

    def set_locked(self, enabled: bool):
        self._locked = enabled
        self._show_hud("已锁定" if enabled else "已解锁")
        self.update()
        self.stateChanged.emit()

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        arrows = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if key == Qt.Key_Escape or (key == Qt.Key_W and modifiers & Qt.ControlModifier):
            self.close()
        elif key in arrows and not modifiers:
            if not self._locked:
                self.move(self.pos() + QPoint(*arrows[key]))
        elif key == Qt.Key_T and not modifiers:
            self.set_topmost(not self._topmost)
            self._show_hud("已置顶" if self._topmost else "已取消置顶")
        elif key == Qt.Key_L and not modifiers:
            self.set_locked(not self._locked)
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.close()

    def set_opacity(self, opacity: float):
        if self._closing:
            return
        self._opacity = round(min(1.0, max(0.1, opacity)), 2)
        self._animate_opacity(self._shown_opacity())
        self._show_hud(f"不透明度 {self._opacity:.0%}")
        self.stateChanged.emit()

    def _shown_opacity(self):
        # 同 PixPin：穿透时只把高于上限的不透明度压到上限，本来就更透明的保持不变。
        return min(self._opacity, T.PIN_CLICK_THROUGH_OPACITY) if self._click_through else self._opacity

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
        self._animate_opacity(self._shown_opacity())
        self._show_hud("鼠标穿透已开启，从托盘菜单退出" if enabled else "鼠标穿透已关闭", T.PIN_HUD_MS * 2)
        self.update()
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
            self._animate_opacity(self._shown_opacity())

    def _fade_finished(self):
        if self._closing:
            self._close_ready = True
            self.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._locked:
            self._drag_off = event.globalPosition().toPoint() - self.pos()
        elif event.button() == Qt.MiddleButton:
            self.toggle_original(self.mapFromGlobal(event.globalPosition().toPoint()))

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
        if self.kind == "text":
            menu.addAction(_fg_icon("pencil"), "编辑文字", self._edit_text)
        if self.kind == "image":
            menu.addAction(_fg_icon("pencil"), "标注", "Space", lambda: self.annotateRequested.emit(self))
            menu.addAction(_fg_icon("scan-text"), "复核识别结果" if self._document is not None else "文本识别", self._recognize)
            if self._document is not None:
                menu.addAction("重新识别（替换校正结果）", self._rerun_ocr)
        menu.addAction(_fg_icon("save"), "保存为文件", self._save)
        menu.addSeparator()
        top = menu.addAction(_fg_icon("pin"), "窗口置顶")
        top.setShortcut("T")
        top.setCheckable(True)
        top.setChecked(self._topmost)
        top.triggered.connect(self.set_topmost)
        lock = menu.addAction(_fg_icon("lock"), "锁定")
        lock.setShortcut("L")
        lock.setCheckable(True)
        lock.setChecked(self._locked)
        lock.triggered.connect(self.set_locked)
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
        menu.addAction(_fg_icon("x"), "关闭贴图", "Esc", self.close)
        menu.exec(pos)
        menu.deleteLater()

    def state(self):
        return {
            "id": self.pin_id, "kind": self.kind,
            "x": self.x() + self._origin.x(), "y": self.y() + self._origin.y(), "scale": self._scale,
            "opacity": self._opacity, "topmost": self._topmost,
            "click_through": self._click_through, "locked": self._locked,
        }

    def restore_state(self, state):
        self.pin_id = state["id"]
        self._scale = state["scale"]
        self._resize_content()
        self.move_content_to(QPoint(state["x"], state["y"]))
        if not any(screen.geometry().intersects(self.geometry()) for screen in QGuiApplication.screens()):
            self.move(QGuiApplication.primaryScreen().availableGeometry().topLeft())
        # 恢复时先一次性准备窗口属性，由模块注册后统一显示，避免中途重复淡入。
        self._opacity = state["opacity"]
        self._topmost = state["topmost"]
        self._click_through = state["click_through"]
        # 旧会话没有锁定字段。
        self._locked = state.get("locked", False)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self._topmost)
        self.setWindowFlag(Qt.WindowTransparentForInput, self._click_through)
        self.setWindowOpacity(self._shown_opacity())


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

    def _on_text(self, pos):
        return isinstance(self.itemAt(pos), QGraphicsTextItem)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.parentWidget().mousePressEvent(event)
        elif event.button() == Qt.LeftButton and not self._on_text(event.position().toPoint()):
            self._moving_pin = True
            self.parentWidget().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        # 识别出的文字上双击仍是选词，其余位置双击关闭贴图。
        if self._on_text(event.position().toPoint()):
            super().mouseDoubleClickEvent(event)
        else:
            self.parentWidget().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if isinstance(self.scene().focusItem(), QGraphicsTextItem):
            super().keyPressEvent(event)
        else:
            self.parentWidget().keyPressEvent(event)

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
    _HINT = "拖动移动；滚轮缩放，Ctrl+滚轮调透明度；中键复原；Space 标注；双击或 Esc 关闭。识别后可滑选文字并复制"
    annotateRequested = Signal(object)

    def __init__(self, pixmap: QPixmap, document=None):
        super().__init__()
        self.setWindowTitle("图片贴图")
        self._base = QPixmap(pixmap)
        self._ocr_controller = None
        self._document = None
        self._text_layer = None
        self._content = _PinImageView(self._base, self)
        self._content.setToolTip(self._HINT)
        self._resize_content()
        if document is not None:
            self._ensure_ocr_controller()
            self._ocr_controller.adopt_document(document, self._base)

    def keyPressEvent(self, event):
        # 同 PixPin：在贴图上按 Space 打开标注。
        if event.key() == Qt.Key_Space and not event.modifiers():
            self.annotateRequested.emit(self)
        else:
            super().keyPressEvent(event)

    def _content_size(self):
        self._content.resetTransform()
        self._content.scale(self._scale, self._scale)
        return (self._base.deviceIndependentSize() * self._scale).toSize().expandedTo(QSize(1, 1))

    def copy(self):
        QGuiApplication.clipboard().setPixmap(self._base)
        self._show_hud("已复制")

    def _save(self):
        path, selected = QFileDialog.getSaveFileName(
            self, "保存贴图", "贴图.png", "PNG (*.png);;JPG (*.jpg);;BMP (*.bmp)")
        if path:
            suffix = {"PNG (*.png)": ".png", "JPG (*.jpg)": ".jpg", "BMP (*.bmp)": ".bmp"}[selected]
            if not Path(path).suffix:
                path += suffix
            if self._base.save(path):
                self._show_hud("已保存")
            else:
                show_message(self, "保存失败", f"无法写入图片：{path}")

    def _ensure_ocr_controller(self):
        from ocr import OCRController
        if self._ocr_controller is None:
            self._ocr_controller = OCRController(self, layer_first=True)
            self._ocr_controller.recognized.connect(self._show_text_layer)
            self._ocr_controller.statusChanged.connect(self._show_ocr_status)

    def _recognize(self):
        self._ensure_ocr_controller()
        if self._document is not None:
            self._ocr_controller.show_results()
        else:
            self._ocr_controller.start(self._base)

    def _rerun_ocr(self):
        dialog = _ConfirmDialog("重新识别", "重新识别将替换已有校正结果和导出草稿，确认继续？", self)
        accepted = dialog.exec()
        dialog.deleteLater()
        if not accepted:
            return
        self._ensure_ocr_controller()
        self._set_document(None)
        scene = self._content.scene()
        if self._text_layer is not None:
            scene.removeItem(self._text_layer)
            self._text_layer.deleteLater()
            self._text_layer = None
        self._ocr_controller.start(self._base)
        self.stateChanged.emit()

    def _show_ocr_status(self, message, has_results):
        self._ocr_status = message + ("；右键查看识别全文" if has_results else "")
        # 提示淡出后，最近一次 OCR 状态仍可悬停查看。
        self._content.setToolTip(f"{self._ocr_status}\n{self._HINT}")
        running = self._ocr_controller is not None and self._ocr_controller.state not in _OCR_FINAL_STATES
        self._show_hud(self._ocr_status, 0 if running else T.PIN_HUD_MS * 3)

    def _set_document(self, document):
        if self._document is not None:
            self._document.changed.disconnect(self.stateChanged.emit)
        self._document = document
        if document is not None:
            document.changed.connect(self.stateChanged.emit)

    def _show_text_layer(self, document):
        from ocr import OCRDocument, add_text_layer
        document = document if isinstance(document, OCRDocument) else None
        self._set_document(document)
        scene = self._content.scene()
        if self._text_layer is not None:
            scene.removeItem(self._text_layer)
            self._text_layer.deleteLater()
            self._text_layer = None
        if document is not None and document.results:
            mode = QApplication.instance().property("theme_mode") or "light"
            self._text_layer = add_text_layer(
                scene, document, mode, on_show_results=self._ocr_controller.show_results)
        self._content.setFocus()
        self.stateChanged.emit()

    def closeEvent(self, event):
        was_closed = self._closed
        super().closeEvent(event)
        if not was_closed and event.isAccepted() and self._ocr_controller is not None:
            self._ocr_controller.cancel()

    def state(self):
        state = {**super().state(), "dpr": self._base.devicePixelRatio()}
        if self._document is not None:
            state["ocr"] = self._document.to_payload()
        return state


class _PinTextEdit(QTextEdit):
    # 只读时双击、中键、快捷键都交给贴图窗口，行为与图片贴图一致；编辑中保留文本框原生操作。
    def mouseDoubleClickEvent(self, event):
        if self.isReadOnly():
            self.parentWidget().mouseDoubleClickEvent(event)
        else:
            super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.parentWidget().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if not self.isReadOnly() and event.key() == Qt.Key_Escape:
            self.setReadOnly(True)
        elif self.isReadOnly() and not (event.modifiers() & Qt.ControlModifier and event.key() in (Qt.Key_C, Qt.Key_A)):
            self.parentWidget().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.setReadOnly(True)
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        self.parentWidget().wheelEvent(event)

    def contextMenuEvent(self, event):
        self.parentWidget()._show_menu(event.globalPos())


class PinTextWindow(_PinBase):
    kind = "text"
    # 文字区之外的内边距就是拖动把手，文字本身留给滑选和编辑。
    _padding = _TEXT_PADDING
    _radius = T.RADIUS_LG

    def __init__(self, text: str):
        super().__init__()
        self.setWindowTitle("文字贴图")
        self._content = _PinTextEdit(self)
        self._content.setObjectName("PinText")
        self._content.setFrameShape(QFrame.NoFrame)
        self._content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._content.document().setDocumentMargin(0)
        self._content.setAcceptRichText(False)
        self._content.setPlainText(text)
        self._content.setReadOnly(True)
        self._content.setToolTip("可直接滑选文字；拖动边缘移动；右键“编辑文字”修改，Esc 结束编辑")
        self._content.textChanged.connect(self.stateChanged.emit)
        # 编辑时贴图随内容伸缩，左上角不动。
        self._content.textChanged.connect(self._resize_content)
        self._resize_content()

    def _content_size(self):
        font = QFont(self._content.font())
        font.setPixelSize(max(1, round(T.FONT_MD * self._scale)))
        self._content.setFont(font)
        document = self._content.document().clone()
        document.setDefaultFont(font)
        document.setTextWidth(-1)
        width = min(max(document.idealWidth(), _TEXT_MIN_WIDTH * self._scale), _TEXT_MAX_WIDTH * self._scale)
        document.setTextWidth(width)
        height = min(document.size().height(), _TEXT_MAX_HEIGHT * self._scale)
        return QSize(ceil(width) + 1, ceil(height))

    def _edit_text(self):
        self._content.setReadOnly(False)
        self._content.setFocus()

    def copy(self):
        QGuiApplication.clipboard().setText(self._content.toPlainText())
        self._show_hud("已复制")

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存文字贴图", "贴图.txt", "文本文件 (*.txt)")
        if path:
            if not Path(path).suffix:
                path += ".txt"
            try:
                Path(path).write_text(self._content.toPlainText(), encoding="utf-8")
            except OSError as error:
                show_message(self, "保存失败", str(error))
            else:
                self._show_hud("已保存")

    def state(self):
        return {**super().state(), "text": self._content.toPlainText()}
