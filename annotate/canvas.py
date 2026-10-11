# 标注画布：QGraphicsView + QGraphicsScene。
# 背景为截图（按 DPR 缩放到逻辑坐标），标注图元画在上面。
# 工具状态机：当前工具为 None 时为选择模式（点选/Delete 删除）。

from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItem, QGraphicsTextItem, QFrame, QMenu, QApplication,
)
from PySide6.QtGui import (
    QPainter, QPixmap, QKeySequence, QUndoStack, QPen, QColor,
)
from PySide6.QtCore import Qt, QRectF, QPointF, Signal

from .items import MarkStyle, SerialMarkItem, TextMarkItem, MosaicMarkItem
from .tools import create_tool
from .tools import TOOL_CLASSES
from .commands import RemoveMarkCommand, MoveMarksCommand, SerialIndexCommand
from .pipette import PipettePopup
from styles import tokens as T
from settings import get_settings

_REMEMBERED_STYLE = ("color", "width", "font_size", "font_family",
                     "serial_start", "serial_global", "serial_auto_renumber")


class AnnotationCanvas(QGraphicsView):
    pipettePicked = Signal(str)  # 取色完成，HEX
    styleChanged = Signal(str)
    toolChanged = Signal(str)

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
                    value = QColor(self._settings.value(stored_key, default.name()))
                else:
                    value = self._settings.value(stored_key, default, type=type(default))
                setattr(style, key, value)
        self._style = self.styles["rect"]
        self._tool_id = ""
        self._moving = None
        self._drawing = False
        self._tool = None
        self._pipette = None
        self._loupe = None  # 放大镜：跟随式 loupe，不落图元
        self._undo = QUndoStack(self)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        bg = QGraphicsPixmapItem(pixmap)
        # QGraphicsPixmapItem 已按 pixmap 的 DPR 使用逻辑尺寸。
        self._scene.addItem(bg)
        lw, lh = pixmap.width() / self._dpr, pixmap.height() / self._dpr
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
            self._loupe = PipettePopup(self._bg_pixmap, self._dpr, grid=True)
        self.viewport().setCursor(Qt.CrossCursor)
        self.toolChanged.emit(tool_id)
        return True

    def clear_tool(self):
        self._finish_tool()
        self._tool = None
        self._tool_id = ""
        if self._loupe is not None:
            self._loupe.close()
            self._loupe = None
        self.viewport().setCursor(Qt.ArrowCursor)
        self.toolChanged.emit("")

    def scene_background(self) -> QPixmap:
        return self._bg_pixmap

    # ---------- 取色器 ----------

    def set_pipette_mode(self, on: bool):
        if on:
            self.clear_tool()
            self._pipette = PipettePopup(self._bg_pixmap, self._dpr)
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
        return self._style

    @property
    def tool_id(self):
        return self._tool_id

    def update_style(self, key, value):
        setattr(self._style, key, value)
        if key in _REMEMBERED_STYLE and self._tool_id:
            stored = value.name() if isinstance(value, QColor) else value
            self._settings.setValue(f"annotation/{self._tool_id}/{key}", stored)
        if key == "color" and self._settings.value("same_tool_color", False, type=bool):
            for name, style in self.styles.items():
                style.color = QColor(value)
                self._settings.setValue(f"annotation/{name}/color", value.name())
        if key == "serial_start":
            self._scene.setProperty("serial_next", value)
            if self._style.serial_global:
                QApplication.instance().setProperty("serial_counter", value)
        elif key == "serial_auto_renumber":
            self._scene.setProperty("serial_auto_renumber", value)
        self.styleChanged.emit(self._tool_id)

    def adjust_serial(self, delta):
        for item in self._scene.selectedItems():
            if isinstance(item, SerialMarkItem):
                self._undo.push(SerialIndexCommand(item, item.index + delta))

    def finish_editing(self):
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
        pm = QPixmap(int(rect.width() * self._dpr),
                     int(rect.height() * self._dpr))
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
        if self._tool is not None:
            self._tool.finish(self._undo)

    # ---------- 事件 ----------

    def mousePressEvent(self, e):
        if self._loupe is not None:
            e.accept()
            return
        if self._pipette is not None and e.button() == Qt.LeftButton:
            color = self._pipette.pick(self.mapToScene(e.pos()), e.globalPosition().toPoint())
            self.set_pipette_mode(False)
            self.pipettePicked.emit(color.name().upper())
            return
        hit = self.itemAt(e.pos())
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
            self._moving = {item: QPointF(item.pos()) for item in self._scene.items()
                            if item.flags() & QGraphicsItem.ItemIsMovable}
            super().mousePressEvent(e)
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
        item = self.itemAt(e.pos())
        if isinstance(item, TextMarkItem) and e.button() == Qt.LeftButton:
            self.set_tool_by_id("text")
            self._tool.edit(item, self._scene, self._undo)
            super().mouseDoubleClickEvent(e)
            return
        if self._tool is not None and e.button() == Qt.LeftButton:
            self._tool.on_double_click(self._scene, self.mapToScene(e.pos()),
                                       self._undo)
        else:
            super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        if self._moving is not None:
            super().mouseMoveEvent(e)
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
        if self._moving is not None:
            super().mouseReleaseEvent(e)
            changed = {item: before for item, before in self._moving.items() if item.pos() != before}
            if changed:
                self._undo.push(MoveMarksCommand(changed, {item: QPointF(item.pos()) for item in changed}))
            self._moving = None
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
        if self._tool is None or not self._style.wheel_enabled:
            if self.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded:
                super().wheelEvent(event)
            else:
                event.ignore()
            return
        key, lo, hi = {
            "text": ("font_size", 6, 96), "serial": ("serial_size", 12, 96),
            "mosaic": ("mosaic_strength", 2, 40), "eraser": ("eraser_size", 4, 120),
            "magnifier": ("zoom", 1, 8),
        }.get(self._tool_id, ("width", 1, 30))
        step = 1 if event.angleDelta().y() > 0 else -1
        self.update_style(key, max(lo, min(hi, getattr(self._style, key) + step)))
        event.accept()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        mode = QApplication.instance().property("theme_mode") or "light"
        painter.setPen(QPen(QColor(T.THEMES[mode]["muted_foreground"]), 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        for item in self._scene.selectedItems():
            painter.drawRect(item.sceneBoundingRect())
