"""离屏验收：五像素线距、模型窄格压实及编辑预览打断。仅合成数据。"""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from styles import tokens as T
from table.editor import ADD_HORIZONTAL, DELETE, SELECT, GridCanvas
from table.model import CellMerge, GridSpec
from table.slanet import grid_from_structure
from table.grid import RuleEvidence, RuleLine, snap_grid


def main():
    app = QApplication.instance() or QApplication([])
    from styles import apply_theme
    apply_theme(app, "light")
    image = QImage(200, 120, QImage.Format_RGB32)
    image.fill(T.PAPER_BACKGROUND)
    canvas = GridCanvas(image)
    canvas.set_grid(GridSpec((0, 60, 120), (0, 100, 200)))
    canvas._add_line("h", 4)
    assert canvas.grid.horizontal_lines == (0, 60, 120)
    canvas._add_line("h", 5)
    assert canvas.grid.horizontal_lines == (0, 5, 60, 120)
    canvas._dragging = ("h", 2)
    changes = []
    canvas.gridChanged.connect(lambda grid, final: changes.append(final))
    canvas.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, QPointF(70, 6), QPointF(70, 6),
                                    Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    assert canvas.grid.horizontal_lines[2] == 10
    canvas.set_tool(ADD_HORIZONTAL)
    assert changes == [False, True] and canvas._dragging is None
    canvas.set_tool(SELECT)
    canvas._set_hovered(("v", 1))
    assert canvas._delete_target() is None
    canvas.set_tool(DELETE)
    canvas._set_hovered(("v", 1))
    assert canvas._delete_target() == ("v", 1)
    canvas.set_tool(SELECT)
    assert canvas._delete_target() is None
    dense = GridSpec((0, 2, 40, 98, 100), (0, 2, 50, 100), (CellMerge(1, 1, 2, 3),)).compact()
    assert dense.horizontal_lines == (0, 40, 100)
    assert dense.vertical_lines == (0, 50, 100)
    assert dense.merges == (CellMerge(0, 0, 1, 2),)
    grid = grid_from_structure([(0, 0, 0, 0), (1, 1, 0, 0)],
                               [(0, 0, 100, 2), (0, 2, 100, 100)], image_width=100, image_height=100)
    assert grid.horizontal_lines == (0, 100), grid
    rules = RuleEvidence((RuleLine(2, 1, 0, 100),), (), False, 100, 100)
    snapped = snap_grid(GridSpec((0, 5, 100), (0, 100)), rules, ())
    assert snapped.horizontal_lines == (0, 100)
    canvas.set_grid(GridSpec((0, 60, 120), (0, 100, 200)))
    canvas.set_tool(SELECT)
    canvas.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(0, 30), QPointF(0, 30),
                                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert canvas.grid.horizontal_lines == (0, 30, 60, 120)
    finished = []
    canvas.toolFinished.connect(lambda: finished.append(True))
    canvas.set_tool(DELETE)
    canvas.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(50, 30), QPointF(50, 30),
                                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert canvas.grid.horizontal_lines == (0, 60, 120) and canvas.tool == SELECT and finished == [True]
    canvas._menu_preview(("delete", ("v", 1)))
    assert canvas._delete_target() == ("v", 1)
    canvas._menu_preview(("h", 30))
    assert canvas._delete_target() is None
    canvas._menu_preview(None)
    assert canvas._preview is None
    framed = GridCanvas(image, QRect(20, 20, 100, 60))
    bounds_events = []
    framed.boundsChanged.connect(bounds_events.append)
    def corner_event(kind, x, y):
        return QMouseEvent(kind, QPointF(x, y), QPointF(x, y),
                           Qt.NoButton if kind == QMouseEvent.MouseMove else Qt.LeftButton,
                           Qt.NoButton if kind == QMouseEvent.MouseButtonRelease else Qt.LeftButton, Qt.NoModifier)
    assert not framed.pans_at(QPointF(122, 82))
    before = framed.grid
    framed.mousePressEvent(corner_event(QMouseEvent.MouseButtonPress, 120, 80))
    framed.mouseMoveEvent(corner_event(QMouseEvent.MouseMove, 140, 90))
    assert framed.bounds == QRect(20, 20, 100, 60) and framed.grid == before
    assert framed._bounds_preview == QRect(20, 20, 120, 70) and not bounds_events
    framed.mouseReleaseEvent(corner_event(QMouseEvent.MouseButtonRelease, 140, 90))
    assert bounds_events == [QRect(20, 20, 120, 70)] and framed._bounds_preview is None
    framed.mousePressEvent(corner_event(QMouseEvent.MouseButtonPress, 20, 20))
    framed.mouseMoveEvent(corner_event(QMouseEvent.MouseMove, -20, -20))
    assert framed._bounds_preview == QRect(0, 0, 120, 80)
    framed.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    framed.mouseReleaseEvent(corner_event(QMouseEvent.MouseButtonRelease, 0, 0))
    assert len(bounds_events) == 1 and framed._bounds_drag is None
    framed.mousePressEvent(corner_event(QMouseEvent.MouseButtonPress, 120, 80))
    framed.mouseMoveEvent(corner_event(QMouseEvent.MouseMove, 21, 21))
    assert framed._bounds_preview.size().width() == 16 and framed._bounds_preview.size().height() == 16
    framed.set_tool(ADD_HORIZONTAL)
    framed.mouseReleaseEvent(corner_event(QMouseEvent.MouseButtonRelease, 21, 21))
    assert len(bounds_events) == 1 and framed.bounds == QRect(20, 20, 100, 60)
    framed.close()
    destination = Path(__file__).resolve().parents[1] / ".scratch" / "grid-edit-polish"
    destination.mkdir(parents=True, exist_ok=True)
    canvas.show()
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        canvas.set_tool(DELETE)
        canvas._set_hovered(("v", 1))
        app.processEvents()
        canvas.grab().save(str(destination / f"{mode}-delete.png"))
        canvas.set_tool(ADD_HORIZONTAL)
        canvas._pointer = (50, 30)
        canvas.update()
        app.processEvents()
        canvas.grab().save(str(destination / f"{mode}-add.png"))
    canvas.close()
    print("PASS: 5px add/drag limit, interrupted drag commits once, delete preview is mode-specific, model slivers collapse with merge indices; corner resize previews without mutating data, commits on release and cancels on interruption")


if __name__ == "__main__":
    main()
