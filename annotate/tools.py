# 标注工具：12 个工具全部在此注册。
#
# 实现新工具的模式：
#   1. 子类化 MarkTool，实现 on_press / on_move / on_release；
#   2. on_press 建图元（items.py）并 scene.addItem（拖拽时可见）；
#   3. on_release 收尾：合法则 undo.push(AddMarkCommand)，非法则 scene.removeItem；
#   4. 在 TOOL_CLASSES 注册 id（id 必须与 annotate/toolbar.py 的 TOOLS 一致）。

from PySide6.QtCore import QRect, QRectF, QPointF, Qt
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene
from PySide6.QtGui import QUndoStack

from .items import (
    ArrowMarkItem, EllipseMarkItem, HighlightMarkItem, LineMarkItem,
    MarkStyle, MosaicMarkItem, PencilMarkItem,
    PolylineMarkItem, RectMarkItem, SerialMarkItem, TextMarkItem,
)
from .commands import AddMarkCommand, RemoveMarkCommand


# P0-C 设置窗口落地后，这些配置再迁入设置模块。
SERIAL_START_FROM_ZERO = False
SERIAL_GLOBAL_COUNTING = False
SERIAL_AUTO_RENUMBER = True
_serial_global_next = None


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

    def on_double_click(self, scene, pos, undo):
        pass

    def on_right_press(self, pos, undo):
        pass


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


class EllipseTool(RectTool):
    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._start = QPointF(pos)
        self._item = EllipseMarkItem(self._style)
        self._item.set_rect(QRectF(pos, pos))
        scene.addItem(self._item)


class LineTool(ArrowTool):
    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._start = QPointF(pos)
        self._item = LineMarkItem(self._style)
        self._item.set_line(self._start, self._start)
        scene.addItem(self._item)


class PolylineTool(MarkTool):
    def on_press(self, scene, pos, undo):
        if self._item is None:
            self._scene = scene
            self._item = PolylineMarkItem(self._style)
            scene.addItem(self._item)
        self._item.add_point(pos)

    def on_move(self, pos):
        self._item.set_preview(pos)

    def on_release(self, pos, undo):
        pass

    def on_double_click(self, scene, pos, undo):
        self._finish(undo)

    def on_right_press(self, pos, undo):
        self._finish(undo)

    def _finish(self, undo):
        points = self._item.points
        if len(points) > 1:
            delta = points[-1] - points[-2]
            if delta.x() * delta.x() + delta.y() * delta.y() < 16:
                self._item.remove_last_point()
        if len(points) >= 2:
            undo.push(AddMarkCommand(self._scene, self._item))
        else:
            self._scene.removeItem(self._item)
        self._item = None


class PencilTool(MarkTool):
    item_class = PencilMarkItem

    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._item = self.item_class(self._style)
        self._item.move_to(pos)
        scene.addItem(self._item)

    def on_move(self, pos):
        self._item.line_to(pos)

    def on_release(self, pos, undo):
        if self._item.point_count < 2:
            self._scene.removeItem(self._item)
        else:
            undo.push(AddMarkCommand(self._scene, self._item))
        self._item = None


class HighlightTool(PencilTool):
    item_class = HighlightMarkItem


class MosaicTool(MarkTool):
    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._start = QPointF(pos)
        self._item = MosaicMarkItem()
        self._item.set_preview_rect(QRectF(pos, pos))
        scene.addItem(self._item)

    def on_move(self, pos):
        self._item.set_preview_rect(
            QRectF(self._start, QPointF(pos)).normalized())

    def on_release(self, pos, undo):
        rect = QRectF(self._start, QPointF(pos)).normalized()
        if rect.width() < 4 or rect.height() < 4:
            self._scene.removeItem(self._item)
        else:
            bg = self._scene.views()[0].scene_background()
            dpr = bg.devicePixelRatio()
            physical = QRect(int(rect.x() * dpr), int(rect.y() * dpr),
                             int(rect.width() * dpr), int(rect.height() * dpr))
            region = bg.copy(physical)
            w, h = region.width(), region.height()
            k = max(6, min(w, h) // 12)
            small = region.scaled(max(1, w // k), max(1, h // k),
                                  Qt.IgnoreAspectRatio, Qt.FastTransformation)
            mosaic = small.scaled(w, h, Qt.IgnoreAspectRatio,
                                  Qt.FastTransformation)
            self._item.set_mosaic(mosaic, rect)
            undo.push(AddMarkCommand(self._scene, self._item))
        self._item = None


class EraserTool(MarkTool):
    def __init__(self, style):
        super().__init__(style)
        self._erased = []
        self._erased_ids = set()

    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._erase(pos)

    def on_move(self, pos):
        self._erase(pos)

    def on_release(self, pos, undo):
        if self._erased:
            deleted_indices = [item.index for item in self._erased
                               if isinstance(item, SerialMarkItem)]
            undo.push(RemoveMarkCommand(self._scene, self._erased))
            renumber_serials_after_delete(self._scene, deleted_indices)
        self._erased = []
        self._erased_ids = set()

    def _erase(self, pos):
        for item in self._scene.items(pos):
            item_id = id(item)
            if isinstance(item, QGraphicsPixmapItem) or item_id in self._erased_ids:
                continue
            self._scene.removeItem(item)
            self._erased.append(item)
            self._erased_ids.add(item_id)


class MagnifierTool(MarkTool):
    # 检查模式：跟随式 loupe 由画布接管，此处不落图元。
    def on_press(self, scene, pos, undo):
        pass

    def on_move(self, pos):
        pass

    def on_release(self, pos, undo):
        pass


class SerialTool(MarkTool):
    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._item = SerialMarkItem(self._style, _next_serial_index(scene))
        self._item.setPos(pos)
        scene.addItem(self._item)

    def on_move(self, pos):
        pass

    def on_release(self, pos, undo):
        undo.push(AddMarkCommand(self._scene, self._item))
        self._item = None

    def set_index(self, item, value):
        item.set_index(value)


def _next_serial_index(scene) -> int:
    """序号计数器。放 scene 属性上，不随工具实例重建而重置。"""
    if SERIAL_GLOBAL_COUNTING:
        global _serial_global_next
        if _serial_global_next is None:
            _serial_global_next = 0 if SERIAL_START_FROM_ZERO else 1
        idx = _serial_global_next
        _serial_global_next += 1
        return idx
    nxt = scene.property("serial_next")
    if nxt is None:
        nxt = 0 if SERIAL_START_FROM_ZERO else 1
    scene.setProperty("serial_next", int(nxt) + 1)
    return int(nxt)


def renumber_serials_after_delete(scene, deleted_indices):
    if not SERIAL_AUTO_RENUMBER:
        return
    for deleted in sorted(set(deleted_indices), reverse=True):
        for item in scene.items():
            if isinstance(item, SerialMarkItem) and item.index > deleted:
                item.set_index(item.index - 1)


# id -> 工具类。id 必须与 toolbar.py 的 TOOLS 一致。
TOOL_CLASSES: dict[str, type[MarkTool]] = {
    "rect": RectTool,
    "ellipse": EllipseTool,
    "arrow": ArrowTool,
    "pencil": PencilTool,
    "line": LineTool,
    "polyline": PolylineTool,
    "text": TextTool,
    "serial": SerialTool,
    "mosaic": MosaicTool,
    "highlight": HighlightTool,
    "eraser": EraserTool,
    "magnifier": MagnifierTool,
}


def create_tool(tool_id: str, style: MarkStyle) -> MarkTool | None:
    cls = TOOL_CLASSES.get(tool_id)
    return cls(style) if cls else None
