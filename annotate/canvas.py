# 标注画布：QGraphicsView + QGraphicsScene。
# 背景为截图（按 DPR 缩放到逻辑坐标），标注图元画在上面。
# 工具状态机：当前工具为 None 时为选择模式（点选/Delete 删除）。

from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItem, QGraphicsTextItem, QFrame, QMenu, QApplication,
)
from PySide6.QtGui import (
    QPainter, QPixmap, QKeySequence, QUndoStack, QPen, QColor, QCursor, QTextCursor,
)
from PySide6.QtCore import Qt, QRect, QRectF, QPointF, Signal, QEvent
from copy import copy

from .items import MarkStyle, SerialMarkItem, TextMarkItem, MosaicMarkItem, ArrowMarkItem, EraseMarkItem
from .tools import create_tool
from .tools import TOOL_CLASSES
from .commands import (RemoveMarkCommand, MoveMarksCommand, SerialIndexCommand,
                       ArrowCommentCommand, ArrowCurveCommand, StyleMarksCommand)
from .pipette import PipettePopup
from styles import tokens as T
from settings import get_settings

_REMEMBERED_STYLE = ("color", "width", "font_size", "font_family",
                     "serial_start", "serial_global", "serial_auto_renumber")


class AnnotationCanvas(QGraphicsView):
    pipettePicked = Signal(str)  # 取色完成，HEX
    toolChanged = Signal(str)
    optionsChanged = Signal(str)

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self._dpr = pixmap.devicePixelRatio() or 1.0
        self._bg_pixmap = pixmap
        self.styles = {name: MarkStyle() for name in TOOL_CLASSES}
        self.styles["highlight"].color = QColor(T.ANNOTATION_COLORS[-1])
        self._settings = get_settings()
        for name, style in self.styles.items():
            for key in _REMEMBERED_STYLE:
                default = getattr(style, key)
                stored_key = f"annotation/{name}/{key}"
                if isinstance(default, QColor):
                    value = QColor(self._settings.value(stored_key, default.name(QColor.HexArgb)))
                else:
                    value = self._settings.value(stored_key, default, type=type(default))
                setattr(style, key, value)
        self._style = self.styles["rect"]
        self._tool_id = ""
        self._moving = None
        self._move_start = None
        self._selection_style = None
        self._drawing = False
        self._eraser_pos = None
        self._comment_edit = None
        self._curve_drag = None
        self._tool = None
        self._pipette = None
        self._loupe = None  # 放大镜：跟随式 loupe，不落图元
        self._undo = QUndoStack(self)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._scene.selectionChanged.connect(self._selection_changed)
        self._undo.indexChanged.connect(self._selection_changed)

        bg = QGraphicsPixmapItem(pixmap)
        # QGraphicsPixmapItem 已按 pixmap 的 DPR 使用逻辑尺寸。
        self._scene.addItem(bg)
        lw, lh = pixmap.deviceIndependentSize().width(), pixmap.deviceIndependentSize().height()
        self._scene.setSceneRect(QRectF(0, 0, lw, lh))

        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMouseTracking(True)

    # ---------- 工具 ----------

    def set_tool_by_id(self, tool_id: str) -> bool:
        """返回 False 表示该工具未实现（调用方打日志）。"""
        if tool_id not in self.styles:
            return False
        self._clear_eraser_preview()
        self._finish_tool()
        self._style = self.styles[tool_id]
        tool = create_tool(tool_id, self._style)
        if tool is None:
            return False
        self.set_pipette_mode(False)
        self._tool = tool
        self._tool_id = tool_id
        self._scene.setProperty("serial_auto_renumber", self.styles["serial"].serial_auto_renumber)
        self._scene.clearSelection()
        # 放大镜是检查模式：跟随式 loupe，不在画布落图元
        if self._loupe is not None:
            self._loupe.close()
            self._loupe = None
        if tool_id == "magnifier":
            self._loupe = PipettePopup(self._bg_pixmap, grid=True)
        self.viewport().setCursor(Qt.BlankCursor if tool_id == "eraser" else Qt.CrossCursor)
        if tool_id == "eraser" and self.viewport().underMouse():
            self._eraser_pos = self.viewport().mapFromGlobal(QCursor.pos())
            self.viewport().update(self._eraser_preview_rect())
        self.toolChanged.emit(tool_id)
        self.optionsChanged.emit(self.options_tool_id)
        return True

    def clear_tool(self):
        self._finish_tool()
        self._clear_eraser_preview()
        self._tool = None
        self._tool_id = ""
        if self._loupe is not None:
            self._loupe.close()
            self._loupe = None
        self.viewport().setCursor(Qt.ArrowCursor)
        self.toolChanged.emit("")
        self.optionsChanged.emit(self.options_tool_id)

    def scene_background(self) -> QPixmap:
        return self._bg_pixmap

    # ---------- 取色器 ----------

    def set_pipette_mode(self, on: bool):
        if on:
            self.clear_tool()
            self._pipette = PipettePopup(self._bg_pixmap)
            self.viewport().setCursor(Qt.CrossCursor)
        else:
            if self._pipette is not None:
                self._pipette.close()
                self._pipette = None
            self.viewport().setCursor(Qt.ArrowCursor)

    @property
    def pipette_active(self) -> bool:
        return self._pipette is not None

    @property
    def style(self) -> MarkStyle:
        return self._selection_style or self._style

    @property
    def options_tool_id(self):
        selected = self._selected_marks()
        return selected[0].tool_id if selected else self._tool_id

    def _selected_marks(self):
        return [item for item in self._scene.selectedItems() if hasattr(item, "style_keys")]

    def _selection_changed(self, *args):
        selected = self._selected_marks()
        self._selection_style = copy(selected[0].style) if selected else None
        if selected:
            defaults = self.styles[selected[0].tool_id]
            for key in ("wheel_enabled", "serial_start", "serial_global", "serial_auto_renumber"):
                setattr(self._selection_style, key, getattr(defaults, key))
        self.optionsChanged.emit(self.options_tool_id)

    def style_key_enabled(self, key):
        selected = self._selected_marks()
        if not selected:
            return True
        # 混选只开放共同属性，避免某些图形悄悄保持原值。
        if key in ("wheel_enabled", "serial_start", "serial_global", "serial_auto_renumber"):
            return True
        return all(key in item.style_keys for item in selected)

    @property
    def tool_id(self):
        return self._tool_id

    def update_style(self, key, value):
        selected = self._selected_marks()
        if selected and key not in ("wheel_enabled", "serial_start", "serial_global", "serial_auto_renumber"):
            changes = {}
            for item in selected:
                if key not in item.style_keys or getattr(item.style, key) == value:
                    continue
                style = copy(item.style)
                setattr(style, key, value)
                if key == "arrow_type":
                    head = "open" if value.startswith("line") or value == "dimension" else "filled"
                    style.arrow_start = head if value.endswith("_both") or value == "dimension" else "none"
                    style.arrow_end = head
                changes[item] = style
            if changes:
                self._finish_tool()
                self._undo.push(StyleMarksCommand(changes))
            return
        style = self.styles[self.options_tool_id] if selected else self._style
        old_preview = self._eraser_preview_rect() if key == "eraser_size" else None
        setattr(style, key, value)
        if key == "arrow_type":
            head = "open" if value.startswith("line") or value == "dimension" else "filled"
            style.arrow_start = head if value.endswith("_both") or value == "dimension" else "none"
            style.arrow_end = head
        if old_preview is not None:
            self.viewport().update(old_preview.united(self._eraser_preview_rect()))
        if key in _REMEMBERED_STYLE and self.options_tool_id:
            stored = value.name(QColor.HexArgb) if isinstance(value, QColor) else value
            self._settings.setValue(f"annotation/{self.options_tool_id}/{key}", stored)
        if key == "color" and self._settings.value("same_tool_color", False, type=bool):
            for name, style in self.styles.items():
                style.color = QColor(value)
                self._settings.setValue(f"annotation/{name}/color", value.name(QColor.HexArgb))
        if key == "serial_start":
            self._scene.setProperty("serial_next", value)
            if style.serial_global:
                QApplication.instance().setProperty("serial_counter", value)
        elif key == "serial_auto_renumber":
            self._scene.setProperty("serial_auto_renumber", value)
        self._selection_changed()

    def adjust_serial(self, delta):
        for item in self._scene.selectedItems():
            if isinstance(item, SerialMarkItem):
                self._undo.push(SerialIndexCommand(item, item.index + delta))

    def finish_editing(self):
        if self._comment_edit is not None:
            self._finish_comment()
            return True
        if self._tool is not None and self._tool.finishes_on_escape and self._tool._item is not None:
            self._finish_tool()
            return True
        return False

    # ---------- undo ----------

    def undo(self):
        self._finish_tool()
        self._undo.undo()

    def redo(self):
        self._finish_tool()
        self._undo.redo()

    # ---------- 导出 ----------

    def render_to_pixmap(self) -> QPixmap:
        self._finish_tool()
        rect = self._scene.sceneRect()
        from capture import ScreenManager
        pm = QPixmap(ScreenManager.image_rect_to_physical(rect, self._bg_pixmap).size())
        pm.setDevicePixelRatio(self._dpr)
        pm.fill(Qt.transparent)
        selected = self._scene.selectedItems()
        self._scene.clearSelection()
        with QPainter(pm) as p:
            self._scene.render(p, rect, rect)
        for item in selected:
            item.setSelected(True)
        return pm

    def _finish_tool(self):
        self._finish_comment()
        if self._tool is not None:
            self._tool.finish(self._undo)

    def _finish_comment(self):
        if self._comment_edit is None:
            return
        arrow, before = self._comment_edit
        after = arrow.comment_item.toPlainText()
        self._comment_edit = None
        arrow.comment_item.clearFocus()
        arrow.comment_item.setTextInteractionFlags(Qt.NoTextInteraction)
        if before != after:
            self._undo.push(ArrowCommentCommand(arrow, before, after))
        else:
            arrow.set_comment(after)

    def _edit_comment(self, arrow):
        self._finish_tool()
        self._comment_edit = (arrow, arrow.comment_item.toPlainText())
        arrow.comment_item.show()
        arrow.comment_item.setTextInteractionFlags(Qt.TextEditorInteraction)
        arrow.comment_item.setFocus()
        self._scene.clearSelection()
        arrow.setSelected(True)
        cursor = arrow.comment_item.textCursor()
        cursor.select(QTextCursor.Document)
        arrow.comment_item.setTextCursor(cursor)

    # ---------- 事件 ----------

    def _eraser_preview_rect(self):
        if self._eraser_pos is None:
            return QRect()
        center = self.mapToScene(self._eraser_pos)
        radius = self._style.eraser_size / 2
        return self.mapFromScene(QRectF(center.x() - radius, center.y() - radius,
                                       radius * 2, radius * 2)).boundingRect().adjusted(-3, -3, 3, 3)

    def _clear_eraser_preview(self):
        old_rect = self._eraser_preview_rect()
        self._eraser_pos = None
        self.viewport().update(old_rect)

    def leaveEvent(self, event):
        self._clear_eraser_preview()
        super().leaveEvent(event)

    def event(self, event):
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Tab and self._pipette is not None:
            # QWidget 默认会先用 Tab 移焦，取色模式必须在此前截获。
            self._pipette.cycle_color_format()
            event.accept()
            return True
        return super().event(event)

    def mousePressEvent(self, e):
        if self._loupe is not None:
            e.accept()
            return
        if self._pipette is not None and e.button() == Qt.LeftButton:
            color = self._pipette.pick(self.mapToScene(e.pos()), e.globalPosition().toPoint())
            self.set_pipette_mode(False)
            self.pipettePicked.emit(color.name().upper())
            return
        hit = self._selection_hit(e.pos())
        if self._comment_edit is not None and hit is not self._comment_edit[0].comment_item:
            self._finish_comment()
        if (e.button() == Qt.LeftButton and isinstance(hit, QGraphicsTextItem)
                and isinstance(hit.parentItem(), ArrowMarkItem) and self._tool_id != "eraser"):
            if self._comment_edit is None:
                self._edit_comment(hit.parentItem())
            super().mousePressEvent(e)
            return
        if e.button() == Qt.LeftButton and self._tool_id != "eraser" and not e.modifiers() & Qt.AltModifier:
            for arrow in self._scene.selectedItems():
                if isinstance(arrow, ArrowMarkItem) and arrow.control_point is not None:
                    distance = arrow.mapFromScene(self.mapToScene(e.pos())) - arrow.control_point
                    if distance.x() ** 2 + distance.y() ** 2 <= 49:
                        self._finish_tool()
                        self._curve_drag = (arrow, arrow.control_point)
                        return
        if (e.button() == Qt.RightButton and isinstance(hit, QGraphicsTextItem)
                and hit.textInteractionFlags() != Qt.NoTextInteraction):
            # OCR 文字层的菜单不能被此前选中的标注抢占。
            super().mousePressEvent(e)
            return
        if e.button() == Qt.LeftButton and isinstance(hit, TextMarkItem) and hit.hasFocus():
            super().mousePressEvent(e)
            return
        if e.button() == Qt.LeftButton and (self._tool_id == "text"):
            self._finish_tool()
        if e.button() == Qt.LeftButton and self._tool_id == "mosaic" and isinstance(hit, MosaicMarkItem):
            self._undo.push(RemoveMarkCommand(self._scene, [hit]))
            return
        # 已有图形优先进入移动；Alt 按下时允许在图形上叠加新标注。
        if (e.button() == Qt.LeftButton and hit is not None
                and hit.flags() & QGraphicsItem.ItemIsSelectable and self._tool_id != "eraser"
                and not e.modifiers() & Qt.AltModifier
                and not (self._tool is not None and self._tool.preview_on_hover and self._tool._item is not None)):
            self._finish_tool()
            if e.modifiers() & Qt.ControlModifier:
                hit.setSelected(not hit.isSelected())
            elif not hit.isSelected():
                self._scene.clearSelection()
                hit.setSelected(True)
            self._move_start = self.mapToScene(e.pos())
            self._moving = {item: QPointF(item.pos()) for item in self._scene.selectedItems()
                            if item.flags() & QGraphicsItem.ItemIsMovable}
            e.accept()
            return
        if e.button() == Qt.LeftButton and self._tool is not None:
            if not self._scene.sceneRect().contains(self.mapToScene(e.pos())):
                return
            self._drawing = True
            self._tool.on_press(self._scene, self.mapToScene(e.pos()),
                                self._undo)
        elif e.button() == Qt.RightButton and self._tool is not None and self._tool.preview_on_hover:
            self._tool.on_right_press(self.mapToScene(e.pos()), self._undo)
        elif e.button() == Qt.RightButton:
            if hit is not None and hit.flags() & QGraphicsItem.ItemIsSelectable:
                self._scene.clearSelection()
                hit.setSelected(True)
            if self._scene.selectedItems():
                menu = QMenu(self)
                delete = menu.addAction("删除选中标注")
                if menu.exec(e.globalPosition().toPoint()) is delete:
                    self._undo.push(RemoveMarkCommand(self._scene, self._scene.selectedItems()))
        else:
            super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self._loupe is not None:
            e.accept()
            return
        item = self._selection_hit(e.pos())
        if (e.button() == Qt.LeftButton and isinstance(item, QGraphicsTextItem)
                and isinstance(item.parentItem(), ArrowMarkItem)):
            self._moving = None
            self._drawing = False
            self._edit_comment(item.parentItem())
            e.accept()
            return
        if isinstance(item, ArrowMarkItem) and e.button() == Qt.LeftButton:
            self._moving = None
            self._drawing = False
            self._finish_tool()
            before = item.comment_item.toPlainText()
            if before:
                self._undo.push(ArrowCommentCommand(item, before, ""))
            else:
                self._edit_comment(item)
            e.accept()
            return
        if isinstance(item, TextMarkItem) and e.button() == Qt.LeftButton:
            self._moving = None
            self._move_start = None
            self._drawing = False
            self.set_tool_by_id("text")
            self._tool.edit(item, self._scene, self._undo)
            item.setSelected(True)
            super().mouseDoubleClickEvent(e)
            return
        if self._tool is not None and e.button() == Qt.LeftButton:
            self._tool.on_double_click(self._scene, self.mapToScene(e.pos()),
                                       self._undo)
        else:
            super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        if self._curve_drag is not None:
            arrow, _ = self._curve_drag
            arrow.set_control_point(arrow.mapFromScene(self.mapToScene(e.pos())))
            return
        if self._tool_id == "eraser":
            old_rect = self._eraser_preview_rect()
            self._eraser_pos = e.pos()
            self.viewport().update(old_rect.united(self._eraser_preview_rect()))
        if self._moving is not None:
            delta = self.mapToScene(e.pos()) - self._move_start
            for item, before in self._moving.items():
                item.setPos(before + delta)
            return
        if self._loupe is not None:
            self._loupe.pick(self.mapToScene(e.pos()),
                             e.globalPosition().toPoint())
        if self._pipette is not None:
            self._pipette.pick(self.mapToScene(e.pos()),
                               e.globalPosition().toPoint())
        elif self._tool is not None and (self._drawing or self._tool.preview_on_hover):
            self._tool.on_move(self.mapToScene(e.pos()))
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._curve_drag is not None and e.button() == Qt.LeftButton:
            arrow, before = self._curve_drag
            self._curve_drag = None
            after = arrow.control_point
            if before != after:
                self._undo.push(ArrowCurveCommand(arrow, before, after))
            return
        if self._moving is not None:
            self.mouseMoveEvent(e)
            changed = {item: before for item, before in self._moving.items() if item.pos() != before}
            if changed:
                self._undo.push(MoveMarksCommand(changed, {item: QPointF(item.pos()) for item in changed}))
            self._moving = None
            self._move_start = None
            return
        if self._tool is not None and e.button() == Qt.LeftButton and self._drawing:
            self._tool.on_release(self.mapToScene(e.pos()), self._undo)
            self._drawing = False
        else:
            super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if self.finish_editing():
                e.accept()
                return
            if self._tool is not None:
                self.clear_tool()
                e.accept()
                return
        if self._scene.focusItem() is not None:
            super().keyPressEvent(e)
            return
        if e.modifiers() == Qt.ShiftModifier and Qt.Key_A <= e.key() <= Qt.Key_Z:
            e.ignore()
            return
        if e.matches(QKeySequence.Undo):
            self.undo()
            return
        if e.matches(QKeySequence.Redo):
            self.redo()
            return
        if e.matches(QKeySequence.Delete):
            sel = self._scene.selectedItems()
            if sel:
                self._undo.push(RemoveMarkCommand(self._scene, sel))
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, event):
        if self._loupe is not None:
            event.ignore()
            return
        if event.modifiers() & Qt.ControlModifier:
            self._wheel_color_alpha(event)
            return
        if not self.options_tool_id or not self.style.wheel_enabled:
            if self.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded:
                super().wheelEvent(event)
            else:
                event.ignore()
            return
        key, lo, hi = {
            "text": ("font_size", 6, 96), "serial": ("serial_size", 12, 96),
            "mosaic": ("mosaic_strength", 2, 40), "eraser": ("eraser_size", 4, 120),
            "magnifier": ("zoom", 1, 8),
        }.get(self.options_tool_id, ("width", 1, 30))
        if not self.style_key_enabled(key):
            event.ignore()
            return
        step = 1 if event.angleDelta().y() > 0 else -1
        self.update_style(key, max(lo, min(hi, getattr(self.style, key) + step)))
        event.accept()

    def _wheel_color_alpha(self, event):
        # 同 PixPin：Ctrl+滚轮调当前颜色透明度，每格 10%；下限 10%，免得标注画出来却看不见。
        if (self.options_tool_id in ("", "mosaic", "eraser", "magnifier")
                or not self.style_key_enabled("color")):
            event.ignore()
            return
        color = QColor(self.style.color)
        step = 0.1 if event.angleDelta().y() > 0 else -0.1
        color.setAlphaF(round(max(0.1, min(1.0, color.alphaF() + step)), 1))
        self.update_style("color", color)
        event.accept()

    def _selection_hit(self, pos):
        hits = self.items(pos)
        for item in hits:
            if not isinstance(item, EraseMarkItem) and (item.flags() & QGraphicsItem.ItemIsSelectable
                                                       or isinstance(item, QGraphicsTextItem)):
                return item
        return next((item for item in hits if isinstance(item, EraseMarkItem)), hits[0] if hits else None)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        mode = QApplication.instance().property("theme_mode") or "light"
        painter.setPen(QPen(QColor(T.THEMES[mode]["muted_foreground"]), 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        for item in self._scene.selectedItems():
            painter.drawRect(item.sceneBoundingRect())
        if self._tool_id == "eraser" and self._eraser_pos is not None:
            # 画在视图前景，既跟随缩放，也不会混入导出的截图。
            center = self.mapToScene(self._eraser_pos)
            radius = self._style.eraser_size / 2
            painter.setBrush(Qt.NoBrush)
            for color, width in ((T.THEMES[mode]["background"], 3),
                                 (T.THEMES[mode]["foreground"], 1)):
                pen = QPen(QColor(color), width)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawEllipse(center, radius, radius)
