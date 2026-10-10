# 标注工具：Codex 批量实现其余 9 个工具时，照这个模式抄。
#
# 模式：
#   1. 子类化 MarkTool，实现 on_press / on_move / on_release；
#   2. on_press 建图元（items.py）并 scene.addItem（拖拽时可见）；
#   3. on_release 收尾：合法则 undo.push(AddMarkCommand)，非法则 scene.removeItem；
#   4. 在 TOOL_CLASSES 注册 id（id 必须与 annotate/toolbar.py 的 TOOLS 一致）。

from PySide6.QtCore import QRectF, QPointF
from PySide6.QtWidgets import QGraphicsScene
from PySide6.QtGui import QUndoStack

from .items import MarkStyle, RectMarkItem, ArrowMarkItem, TextMarkItem
from .commands import AddMarkCommand


class MarkTool:
    """标注工具基类。style 为画布持有的共享 MarkStyle（引用）。"""

    def __init__(self, style: MarkStyle):
        self._style = style
        self._scene: QGraphicsScene | None = None
        self._item = None

    def on_press(self, scene: QGraphicsScene, pos: QPointF, undo: QUndoStack):
        raise NotImplementedError

    def on_move(self, pos: QPointF):
        raise NotImplementedError

    def on_release(self, pos: QPointF, undo: QUndoStack):
        raise NotImplementedError


class RectTool(MarkTool):
    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._start = QPointF(pos)
        self._item = RectMarkItem(self._style)
        self._item.set_rect(QRectF(pos, pos))
        scene.addItem(self._item)

    def on_move(self, pos):
        self._item.set_rect(QRectF(self._start, QPointF(pos)).normalized())

    def on_release(self, pos, undo):
        r = self._item.boundingRect()
        if r.width() < 4 or r.height() < 4:
            self._scene.removeItem(self._item)
        else:
            undo.push(AddMarkCommand(self._scene, self._item))
        self._item = None


class ArrowTool(MarkTool):
    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._start = QPointF(pos)
        self._item = ArrowMarkItem(self._style)
        self._item.set_line(self._start, self._start)
        scene.addItem(self._item)

    def on_move(self, pos):
        self._item.set_line(self._start, QPointF(pos))

    def on_release(self, pos, undo):
        p = QPointF(pos)
        dx, dy = p.x() - self._start.x(), p.y() - self._start.y()
        if dx * dx + dy * dy < 16:
            self._scene.removeItem(self._item)
        else:
            undo.push(AddMarkCommand(self._scene, self._item))
        self._item = None


class TextTool(MarkTool):
    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._item = TextMarkItem(self._style)
        self._item.setPos(pos)
        scene.addItem(self._item)
        self._item.setFocus()

    def on_move(self, pos):
        pass

    def on_release(self, pos, undo):
        # 空文本不入栈，失去焦点时由 canvas 清理
        if self._item.toPlainText():
            undo.push(AddMarkCommand(self._scene, self._item))
        self._item = None


# id -> 工具类。id 必须与 toolbar.py 的 TOOLS 一致。
TOOL_CLASSES: dict[str, type[MarkTool]] = {
    "rect": RectTool,
    "arrow": ArrowTool,
    "text": TextTool,
}


def create_tool(tool_id: str, style: MarkStyle) -> MarkTool | None:
    cls = TOOL_CLASSES.get(tool_id)
    return cls(style) if cls else None
