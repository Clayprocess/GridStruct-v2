# Undo 命令。AddMarkCommand 的 redo 幂等：工具在 on_press 时已把图元
# 加入 scene（拖拽可见），push 触发的第一次 redo 不重复添加。

from PySide6.QtWidgets import QGraphicsScene
from PySide6.QtGui import QUndoCommand


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
    # redo/undo 幂等：橡皮擦在 push 前已把图元移出 scene（即时反馈），
    # 而 Delete 键路径 push 时图元还在 scene 里。按 it.scene() 判断，
    # 两种调用都正确，不打 Qt 警告。
    def __init__(self, scene: QGraphicsScene, items: list):
        super().__init__("删除标注")
        self._scene = scene
        self._items = list(items)

    def redo(self):
        for it in self._items:
            if it.scene() is self._scene:
                self._scene.removeItem(it)

    def undo(self):
        for it in self._items:
            if it.scene() is not self._scene:
                self._scene.addItem(it)
