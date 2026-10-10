# Undo 命令。AddMarkCommand 的 redo 幂等：工具在 on_press 时已把图元
# 加入 scene（拖拽可见），push 触发的第一次 redo 不重复添加。

from PySide6.QtWidgets import QGraphicsScene
from PySide6.QtGui import QUndoCommand
from PySide6.QtGui import QColor


class AddMarkCommand(QUndoCommand):
    def __init__(self, scene: QGraphicsScene, item):
        super().__init__("添加标注")
        self._scene = scene
        self._item = item
        self._in_scene = True  # on_press 已加入

    def redo(self):
        if not self._in_scene:
            self._scene.addItem(self._item)
            self._in_scene = True

    def undo(self):
        self._scene.removeItem(self._item)
        self._in_scene = False


class RemoveMarkCommand(QUndoCommand):
    def __init__(self, scene: QGraphicsScene, items: list):
        super().__init__("删除标注")
        self._scene = scene
        self._items = list(items)

    def redo(self):
        for it in self._items:
            self._scene.removeItem(it)

    def undo(self):
        for it in self._items:
            self._scene.addItem(it)
