# 标注画布：QGraphicsView + QGraphicsScene。
# 背景为截图（按 DPR 缩放到逻辑坐标），标注图元画在上面。
# 工具状态机：当前工具为 None 时为选择模式（点选/Delete 删除）。

from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QFrame,
)
from PySide6.QtGui import (
    QPainter, QPixmap, QKeySequence, QUndoStack, QUndoCommand,
)
from PySide6.QtCore import Qt, QRectF, Signal

from .items import MarkStyle, SerialMarkItem
from .tools import create_tool, renumber_serials_after_delete
from .commands import RemoveMarkCommand
from .pipette import PipettePopup


class AnnotationCanvas(QGraphicsView):
    pipettePicked = Signal(str)  # 取色完成，HEX

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self._dpr = pixmap.devicePixelRatio() or 1.0
        self._bg_pixmap = pixmap
        self._style = MarkStyle()
        self._tool = None
        self._pipette = None
        self._loupe = None  # 放大镜：跟随式 loupe，不落图元
        self._undo = QUndoStack(self)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        bg = QGraphicsPixmapItem(pixmap)
        bg.setScale(1.0 / self._dpr)  # 场景统一用逻辑坐标
        self._scene.addItem(bg)
        lw, lh = pixmap.width() / self._dpr, pixmap.height() / self._dpr
        self._scene.setSceneRect(QRectF(0, 0, lw, lh))

        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.Antialiasing)

    # ---------- 工具 ----------

    def set_tool_by_id(self, tool_id: str) -> bool:
        """返回 False 表示该工具未实现（调用方打日志）。"""
        tool = create_tool(tool_id, self._style)
        if tool is None:
            return False
        self._tool = tool
        self._scene.clearSelection()
        # 放大镜是检查模式：跟随式 loupe，不在画布落图元
        if tool_id == "magnifier":
            self._loupe = PipettePopup(self._bg_pixmap, self._dpr, grid=True)
        elif self._loupe is not None:
            self._loupe.close()
            self._loupe = None
        self.viewport().setCursor(Qt.CrossCursor)
        return True

    def clear_tool(self):
        self._tool = None
        if self._loupe is not None:
            self._loupe.close()
            self._loupe = None
        self.viewport().setCursor(Qt.ArrowCursor)

    def scene_background(self) -> QPixmap:
        return self._bg_pixmap

    # ---------- 取色器 ----------

    def set_pipette_mode(self, on: bool):
        if on:
            self._tool = None
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

    # ---------- undo ----------

    def undo(self):
        self._undo.undo()

    def redo(self):
        self._undo.redo()

    # ---------- 导出 ----------

    def render_to_pixmap(self) -> QPixmap:
        rect = self._scene.sceneRect()
        pm = QPixmap(int(rect.width() * self._dpr),
                     int(rect.height() * self._dpr))
        pm.setDevicePixelRatio(self._dpr)
        pm.fill(Qt.transparent)
        with QPainter(pm) as p:
            self._scene.render(p)
        return pm

    # ---------- 事件 ----------

    def mousePressEvent(self, e):
        if self._pipette is not None and e.button() == Qt.LeftButton:
            color = self._pipette.color
            self.set_pipette_mode(False)
            self.pipettePicked.emit(color.name().upper())
            return
        if e.button() == Qt.LeftButton and self._tool is not None:
            self._tool.on_press(self._scene, self.mapToScene(e.pos()),
                                self._undo)
        elif e.button() == Qt.RightButton and self._tool is not None:
            self._tool.on_right_press(self.mapToScene(e.pos()), self._undo)
        else:
            super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self._tool is not None and e.button() == Qt.LeftButton:
            self._tool.on_double_click(self._scene, self.mapToScene(e.pos()),
                                       self._undo)
        else:
            super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        if self._loupe is not None:
            self._loupe.pick(self.mapToScene(e.pos()),
                             self.mapToGlobal(e.pos()))
        if self._pipette is not None:
            from PySide6.QtWidgets import QApplication
            self._pipette.pick(self.mapToScene(e.pos()),
                               self.mapToGlobal(e.pos()))
        elif self._tool is not None and e.buttons() & Qt.LeftButton:
            self._tool.on_move(self.mapToScene(e.pos()))
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._tool is not None and e.button() == Qt.LeftButton:
            self._tool.on_release(self.mapToScene(e.pos()), self._undo)
        else:
            super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Delete):
            sel = self._scene.selectedItems()
            if sel:
                self._undo.push(RemoveMarkCommand(self._scene, sel))
                indices = [item.index for item in sel
                           if isinstance(item, SerialMarkItem)]
                renumber_serials_after_delete(self._scene, indices)
        else:
            super().keyPressEvent(e)
