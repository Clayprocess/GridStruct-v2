# 标注画布：QGraphicsView + QGraphicsScene。
# 背景为截图（按 DPR 缩放到逻辑坐标），标注图元画在上面。
# 工具状态机：当前工具为 None 时为选择模式（点选/Delete 删除）。

from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QFrame,
)
from PySide6.QtGui import (
    QPainter, QPixmap, QKeySequence, QUndoStack, QUndoCommand,
)
from PySide6.QtCore import Qt, QRectF

from .items import MarkStyle
from .tools import create_tool
from .commands import RemoveMarkCommand


class AnnotationCanvas(QGraphicsView):
    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self._dpr = pixmap.devicePixelRatio() or 1.0
        self._style = MarkStyle()
        self._tool = None
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
        self.viewport().setCursor(Qt.CrossCursor)
        return True

    def clear_tool(self):
        self._tool = None
        self.viewport().setCursor(Qt.ArrowCursor)

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
        if e.button() == Qt.LeftButton and self._tool is not None:
            self._tool.on_press(self._scene, self.mapToScene(e.pos()),
                                self._undo)
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._tool is not None and e.buttons() & Qt.LeftButton:
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
        else:
            super().keyPressEvent(e)
