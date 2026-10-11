# 标注工具：12 个工具全部在此注册。
#
# 实现新工具的模式：
#   1. 子类化 MarkTool，实现 on_press / on_move / on_release；
#   2. on_press 建图元（items.py）并 scene.addItem（拖拽时可见）；
#   3. on_release 收尾：合法则 undo.push(AddMarkCommand)，非法则 scene.removeItem；
#   4. 在 TOOL_CLASSES 注册 id（id 必须与 annotate/toolbar.py 的 TOOLS 一致）。

from PySide6.QtCore import QRect, QRectF, QPointF, Qt
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QApplication
from PySide6.QtGui import QUndoStack, QImage, QPixmap, QGuiApplication

from .items import (
    ArrowMarkItem, EllipseMarkItem, HighlightMarkItem, LineMarkItem,
    MarkStyle, MosaicMarkItem, PencilMarkItem,
    PolylineMarkItem, RectMarkItem, SerialMarkItem, TextMarkItem,
)
from .commands import AddMarkCommand, RemoveMarkCommand, TextEditCommand


class MarkTool:
    """标注工具基类。style 为画布持有的共享 MarkStyle（引用）。"""

    preview_on_hover = False
    finishes_on_escape = False

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

    def finish(self, undo):
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
        r = QRectF(self._start, QPointF(pos)).normalized()
        self._item.set_rect(r)
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
        self._item.set_line(self._start, p)
        dx, dy = p.x() - self._start.x(), p.y() - self._start.y()
        if dx * dx + dy * dy < 16:
            self._scene.removeItem(self._item)
        else:
            undo.push(AddMarkCommand(self._scene, self._item))
        self._item = None


class TextTool(MarkTool):
    finishes_on_escape = True

    def __init__(self, style):
        super().__init__(style)
        self._before = None

    def on_press(self, scene, pos, undo):
        self.finish(undo)
        self._scene = scene
        self._item = TextMarkItem(self._style)
        self._item.setPos(pos)
        scene.addItem(self._item)
        self._item.setFocus()

    def edit(self, item, scene, undo):
        self.finish(undo)
        self._scene, self._item = scene, item
        self._before = item.toHtml()
        item.setTextInteractionFlags(Qt.TextEditorInteraction)
        item.setFocus()

    def on_move(self, pos):
        pass

    def on_release(self, pos, undo):
        pass

    def finish(self, undo):
        if self._item is not None:
            self._item.clearFocus()
            self._item.setTextInteractionFlags(Qt.NoTextInteraction)
            if self._before is not None:
                after = self._item.toHtml()
                if after != self._before:
                    undo.beginMacro("编辑文字")
                    undo.push(TextEditCommand(self._item, self._before, after))
                    if not self._item.toPlainText():
                        undo.push(RemoveMarkCommand(self._scene, [self._item]))
                    undo.endMacro()
            elif self._item.toPlainText():
                undo.push(AddMarkCommand(self._scene, self._item))
            else:
                self._scene.removeItem(self._item)
            self._item = None
            self._before = None


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
    preview_on_hover = True
    finishes_on_escape = True
    def on_press(self, scene, pos, undo):
        if self._item is None:
            self._scene = scene
            self._item = PolylineMarkItem(self._style)
            scene.addItem(self._item)
        self._item.add_point(pos)

    def on_move(self, pos):
        if self._item is not None:
            self._item.set_preview(pos)

    def on_release(self, pos, undo):
        pass

    def on_double_click(self, scene, pos, undo):
        self._finish(undo)

    def on_right_press(self, pos, undo):
        self._finish(undo)

    def _finish(self, undo):
        if self._item is None:
            return  # 尚未落点或已经通过双击结束。
        self._item.set_preview(None)
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

    def finish(self, undo):
        self._finish(undo)


class PencilTool(MarkTool):
    item_class = PencilMarkItem

    def on_press(self, scene, pos, undo):
        self._scene = scene
        self._start = QPointF(pos)
        self._item = self.item_class(self._style)
        self._item.move_to(pos)
        scene.addItem(self._item)

    def on_move(self, pos):
        if self.item_class is PencilMarkItem and QGuiApplication.keyboardModifiers() & Qt.ShiftModifier:
            self._item.set_straight(self._start, pos)
        else:
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
        rect = QRectF(self._start, QPointF(pos)).normalized()
        if rect.width() >= 4 and rect.height() >= 4:
            self._update_mosaic(rect)
        else:
            self._item.set_preview_rect(rect)

    def on_release(self, pos, undo):
        rect = QRectF(self._start, QPointF(pos)).normalized()
        if rect.width() < 4 or rect.height() < 4:
            size = max(16, self._style.mosaic_strength * 2)
            rect = QRectF(pos.x() - size / 2, pos.y() - size / 2, size, size)
        self._update_mosaic(rect)
        undo.push(AddMarkCommand(self._scene, self._item))
        self._item = None

    def _update_mosaic(self, rect):
        bg = self._scene.views()[0].scene_background()
        rect = rect.intersected(self._scene.sceneRect())
        from capture import ScreenManager
        physical = ScreenManager.image_rect_to_physical(rect, bg)
        region = bg.copy(physical)
        w, h = region.width(), region.height()
        strength = self._style.mosaic_strength
        if self._style.mosaic_mode == "blur":
            import numpy as np
            image = region.toImage().convertToFormat(QImage.Format_RGBA8888)
            pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(h, image.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
            # 积分图实现方框模糊，避免引入图像处理依赖。
            radius = strength
            padded = np.pad(pixels.astype(np.float64), ((radius, radius), (radius, radius), (0, 0)), mode="edge")
            integral = np.pad(padded, ((1, 0), (1, 0), (0, 0))).cumsum(0).cumsum(1)
            size = radius * 2 + 1
            blurred = ((integral[size:, size:] - integral[:-size, size:] - integral[size:, :-size] + integral[:-size, :-size]) / (size * size)).astype(np.uint8)
            mosaic = QPixmap.fromImage(QImage(blurred.data, w, h, w * 4, QImage.Format_RGBA8888).copy())
        else:
            small = region.scaled(max(1, w // strength), max(1, h // strength), Qt.IgnoreAspectRatio, Qt.FastTransformation)
            mosaic = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        self._item.set_mosaic(mosaic, rect)


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
            undo.push(RemoveMarkCommand(self._scene, self._erased))
        self._erased = []
        self._erased_ids = set()

    def _erase(self, pos):
        radius = self._style.eraser_size / 2
        for item in self._scene.items(QRectF(pos.x() - radius, pos.y() - radius, radius * 2, radius * 2)):
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
        self._item = SerialMarkItem(self._style, _next_serial_index(scene, self._style))
        self._item.setPos(pos)
        scene.addItem(self._item)

    def on_move(self, pos):
        pass

    def on_release(self, pos, undo):
        undo.push(AddMarkCommand(self._scene, self._item))
        self._item = None

    def set_index(self, item, value):
        item.set_index(value)


def _next_serial_index(scene, style) -> int:
    """序号计数器。放 scene 属性上，不随工具实例重建而重置。"""
    start = style.serial_start
    if style.serial_global:
        app = QApplication.instance()
        nxt = app.property("serial_counter")
        if nxt is None:
            nxt = start
        app.setProperty("serial_counter", int(nxt) + 1)
        return int(nxt)
    nxt = scene.property("serial_next")
    if nxt is None:
        nxt = start
    scene.setProperty("serial_next", int(nxt) + 1)
    return int(nxt)


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
