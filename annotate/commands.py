# Undo 命令。AddMarkCommand 的 redo 幂等：工具在 on_press 时已把图元
# 加入 scene（拖拽可见），push 触发的第一次 redo 不重复添加。

from PySide6.QtWidgets import QGraphicsScene
from PySide6.QtGui import QUndoCommand
from copy import copy
from .items import ArrowMarkItem, TextMarkItem


class StyleMarksCommand(QUndoCommand):
    def __init__(self, changes):
        super().__init__("修改标注属性")
        self._before = {item: copy(item.style) for item in changes}
        self._after = changes
        self._text_before = {item: item.toHtml() for item in changes if isinstance(item, TextMarkItem)}
        self._curve_before = {item: item.control_point for item in changes if isinstance(item, ArrowMarkItem)}
        self._curve_after = None

    def redo(self):
        for item, style in self._after.items():
            item.set_style(style)
        if self._curve_after is None:
            self._curve_after = {item: item.control_point for item in self._curve_before}
        else:
            for item, point in self._curve_after.items():
                item.set_control_point(point)

    def undo(self):
        for item, style in self._before.items():
            item.set_style(style)
        # 属性编辑可以统一字体；撤销仍应保留此前逐字编辑产生的富文本。
        for item, html in self._text_before.items():
            item.setHtml(html)
        for item, point in self._curve_before.items():
            item.set_control_point(point)


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
        self._scene.clearSelection()
        self._item.setSelected(True)

    def undo(self):
        self._scene.removeItem(self._item)
        self._in_scene = False


class RemoveMarkCommand(QUndoCommand):
    def __init__(self, scene: QGraphicsScene, items: list):
        super().__init__("删除标注")
        self._scene = scene
        self._items = list(items)
        from .items import SerialMarkItem
        self._serials = {it: it.index for it in [*scene.items(), *items]
                         if isinstance(it, SerialMarkItem)}
        self._next = scene.property("serial_next")
        self._auto = scene.property("serial_auto_renumber") is not False

    def redo(self):
        for it in self._items:
            if it.scene() is self._scene:
                self._scene.removeItem(it)
        deleted = [self._serials[it] for it in self._items if it in self._serials]
        if self._auto:
            for item, value in self._serials.items():
                if item not in self._items:
                    item.set_index(value - sum(index < value for index in deleted))
            if self._next is not None:
                self._scene.setProperty("serial_next", self._next - sum(index < self._next for index in deleted))

    def undo(self):
        for it in self._items:
            if it.scene() is not self._scene:
                self._scene.addItem(it)
        for it, index in self._serials.items():
            it.set_index(index)
        self._scene.setProperty("serial_next", self._next)


class MoveMarksCommand(QUndoCommand):
    def __init__(self, before, after):
        super().__init__("移动标注")
        self._before = before
        self._after = after

    def redo(self):
        for item, pos in self._after.items():
            item.setPos(pos)

    def undo(self):
        for item, pos in self._before.items():
            item.setPos(pos)


class SerialIndexCommand(QUndoCommand):
    def __init__(self, item, value):
        super().__init__("调整序号")
        self._item, self._before, self._after = item, item.index, value

    def redo(self):
        self._item.set_index(self._after)

    def undo(self):
        self._item.set_index(self._before)


class TextEditCommand(QUndoCommand):
    def __init__(self, item, before, after):
        super().__init__("编辑文字")
        self._item, self._before, self._after = item, before, after

    def redo(self):
        self._item.setHtml(self._after)

    def undo(self):
        self._item.setHtml(self._before)


class ArrowCommentCommand(QUndoCommand):
    def __init__(self, item, before, after):
        super().__init__("编辑箭头注释")
        self._item, self._before, self._after = item, before, after

    def redo(self):
        self._item.set_comment(self._after)

    def undo(self):
        self._item.set_comment(self._before)


class ArrowCurveCommand(QUndoCommand):
    def __init__(self, item, before, after):
        super().__init__("调整箭头曲线")
        self._item, self._before, self._after = item, before, after

    def redo(self):
        self._item.set_control_point(self._after)

    def undo(self):
        self._item.set_control_point(self._before)
