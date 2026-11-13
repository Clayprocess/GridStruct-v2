"""合成多屏 Qt 绘制验收：缺边、跨屏接缝、拖动手柄和局部重绘，不读取桌面。"""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication
from capture import DesktopCapture, ScreenFrame, ScreenShotWindow
from styles import tokens as T


class Detector(QObject):
    regionDetected = Signal(QRect)

    def __init__(self, **_kwargs):
        super().__init__()
        self.result = QRect()
        self.requests = []
        self.steps = []
        self.cells = []

    def cells_at(self, pos):
        return list(self.cells) if any(cell.contains(pos) for cell in self.cells) else []

    def redetect(self, pos):
        self.requests.append(QPoint(pos))
        self.regionDetected.emit(self.result)

    def shutdown(self):
        pass

    def step_level(self, delta):
        self.steps.append(delta)


def controller(rects, dprs):
    frames = []
    for rect, dpr in zip(rects, dprs):
        pixmap = QPixmap(round(rect.width() * dpr), round(rect.height() * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(QColor(T.LIGHT["background"]))
        frames.append(ScreenFrame(QApplication.primaryScreen(), rect, pixmap, rect.topLeft()))
    with patch("capture.overlay.RegionDetector", Detector):
        c = ScreenShotWindow(DesktopCapture(frames))
    c._guides = False
    return c


def render(window, dpr):
    image = QImage(round(window.width() * dpr), round(window.height() * dpr), QImage.Format_RGB32)
    image.setDevicePixelRatio(dpr)
    image.fill(QColor(T.LIGHT["background"]))
    with QPainter(image) as painter:
        window.render(painter, QPoint())
    return image


def color_at(c, images, point):
    window, image = next((w, im) for w, im in zip(c._windows, images) if w.frame.geometry.contains(point))
    local = point - window.frame.geometry.topLeft()
    dpr = image.devicePixelRatio()
    return image.pixelColor(int((local.x() + .5) * dpr), int((local.y() + .5) * dpr))


def rotate_rect(rect, turns):
    for _ in range(turns):
        rect = QRect(-rect.y() - rect.height(), rect.x(), rect.height(), rect.width())
    return rect


def rotate_point(point, turns):
    for _ in range(turns):
        point = QPoint(-point.y() - 1, point.x())
    return point


def check_edges():
    # 每组显式指定应着色/应保持截图原色的像素；旋转覆盖四条边和负坐标。
    cases = [
        ("staggered", [QRect(0, 0, 240, 180), QRect(240, 80, 240, 180)],
         QRect(0, 0, 240, 180), [(238, 30), (241, 120)], [(236, 30), (238, 120)]),
        ("gap", [QRect(0, 0, 240, 180), QRect(260, 0, 240, 180)],
         QRect(0, 0, 240, 180), [(238, 90)], [(236, 90)]),
        ("cross-screen", [QRect(0, 0, 240, 180), QRect(240, 80, 240, 180)],
         QRect(20, 90, 400, 70), [(239, 88), (240, 88)], [(239, 120), (240, 120)]),
        ("hole", [QRect(0, 0, 300, 80), QRect(0, 80, 80, 160),
                  QRect(220, 80, 80, 160), QRect(0, 240, 300, 80)],
         QRect(0, 80, 80, 160), [(78, 150)], [(76, 150)]),
        ("partial-room", [QRect(0, 0, 240, 180)],
         QRect(20, 20, 219, 140), [(237, 90), (238, 90), (239, 90)], [(236, 90)]),
        ("interior", [QRect(0, 0, 240, 180)],
         QRect(30, 30, 160, 120), [(190, 80), (191, 80), (192, 80)], [(189, 80)]),
    ]
    count = 0
    for name, rects, selection, blue, clear in cases:
        for turns in range(4):
            for scale in (1, 1.25, 1.5, 2):
                dprs = [scale if i % 2 == 0 else 1 for i in range(len(rects))]
                c = controller([rotate_rect(r, turns) for r in rects], dprs)
                c.size_tip = lambda: (QRect(10000, 10000, 10, 10), "")
                try:
                    for mode in ("snap", "create"):
                        target = rotate_rect(selection, turns)
                        c._sel = target if mode == "create" else QRect()
                        c._snap_shown = target if mode == "snap" else QRect()
                        c._drag_mode = "create" if mode == "create" else None
                        images = [render(w, dpr) for w, dpr in zip(c._windows, dprs)]
                        for points, expected in ((blue, QColor(T.CAPTURE_ACCENT)),
                                                 (clear, QColor(T.LIGHT["background"]))):
                            for x, y in points:
                                point = rotate_point(QPoint(x, y), turns)
                                actual = color_at(c, images, point)
                                assert actual == expected, (name, turns, scale, mode, point, actual.name(), expected.name())
                        count += 1
                finally:
                    c.close()
    print(f"PASS: {count} rendered edge cases (four directions, holes, gaps, mixed DPR, no cross-screen seams)")


def event(point, modifiers=Qt.NoModifier):
    return SimpleNamespace(button=lambda: Qt.LeftButton, globalPosition=lambda: SimpleNamespace(toPoint=lambda: point),
                           modifiers=lambda: modifiers)


def check_repaint():
    c = controller([QRect(0, 0, 600, 400)], [1])
    dirty = QRegion()
    original_update = c.update

    def record(region=None):
        nonlocal dirty
        dirty |= QRegion(c._vg) if region is None else region
        original_update(region)

    c.update = record

    def transition(action):
        nonlocal dirty
        before = render(c._windows[0], 1)
        dirty = QRegion()
        action()
        after = render(c._windows[0], 1)
        with QPainter(before) as painter:
            painter.setClipRegion(dirty)
            painter.drawImage(0, 0, after)
        assert before == after, "局部刷新范围没有覆盖全部变化，实际窗口会留下旧遮罩或边框"

    try:
        old, new = QRect(40, 40, 170, 120), QRect(300, 200, 140, 100)
        c._show_snap(old)
        transition(lambda: c.mouse_press(event(QPoint(270, 180))))
        assert c._snap_rect.isNull() and c._snap_shown.isNull()
        c._snap.result = old
        transition(lambda: c.mouse_release(event(QPoint(271, 181))))
        assert c._sel.isNull() and c._snap.requests == [QPoint(271, 181)]
        assert c._snap_shown == old, "小选区取消后应重新发布相同吸附目标"
        transition(lambda: c._on_snap(new))
        assert c._snap_from == old and c._snap_rect == new
        transition(lambda: c._snap_step(.5))
        assert c._snap_shown != old and c._snap_shown != new
        transition(lambda: c._snap_step(1))
        c._snap_anim.stop()
        transition(lambda: c.mouse_press(event(new.center())))
        transition(lambda: c.mouse_release(event(new.center())))
        assert c._sel == new and c._snap_shown.isNull()
        image = render(c._windows[0], 1)
        assert color_at(c, [image], new.center()) == QColor(T.LIGHT["background"])
    finally:
        c.close()
    print("PASS: outside tiny-selection cancellation redetects; dirty-region repaint matches full repaint; animated snap and click stay lit")


def check_handles():
    c = controller([QRect(0, 0, 600, 400)], [1])
    c._sel = QRect(80, 80, 300, 220)
    try:
        idle = render(c._windows[0], 1)
        # 左上手柄超出边框的圆环像素；新框外侧线最多只占 3px。
        probe = QPoint(76, 76)
        base_tip = c.size_tip()[0].adjusted(0, 0, -1, -1)
        for mode in ("create", "move", 0):
            c._drag_mode = mode
            dragging = render(c._windows[0], 1)
            assert color_at(c, [dragging], probe) != color_at(c, [idle], probe), mode
            assert dragging.copy(base_tip) == idle.copy(base_tip), "拖动时尺寸提示应保持显示"
        c._drag_mode = None
        assert render(c._windows[0], 1) == idle
    finally:
        c.close()
    print("PASS: create/move/resize hide handles, preserve size tip, and restore handles on release")


def check_wheel():
    """一次触控板滑动（事件间隔不到 50ms）只切一层；停顿后再滚才切下一层；横向滚动不切。"""
    c = controller([QRect(0, 0, 600, 400)], [1])
    clock = [100.0]

    def wheel(dy, after):
        clock[0] += after
        c.wheel(SimpleNamespace(angleDelta=lambda: QPoint(0, dy)))

    try:
        with patch("capture.overlay.time.monotonic", lambda: clock[0]):
            for _ in range(8):
                wheel(15, 0.012)
            assert c._snap.steps == [1], c._snap.steps
            wheel(120, 0.2)
            wheel(-120, 0.2)
            wheel(0, 0.2)
            assert c._snap.steps == [1, 1, -1], c._snap.steps
            c._sel = QRect(10, 10, 100, 100)
            wheel(120, 0.2)
            assert c._snap.steps == [1, 1, -1], "已有选区时滚轮不切层级"
    finally:
        c.close()
    print("PASS: wheel steps one snap level per gesture, ignores horizontal scroll and active selections")


def check_cells():
    """Excel 按格拖选：拖动时选区是两格围成的整块，压到合并格时包进整个合并格；拖出表格区停在边上；
    拉边角时被拉的边落在单元格边界；按住 Alt 自由框选；单击仍采用单个单元格。"""
    c = controller([QRect(0, 0, 800, 600)], [1])

    def cell(col, row, cols=1):
        return QRect(100 + col * 72, 100 + row * 19, 72 * cols, 19)

    merged = cell(1, 4, 3)  # B5:D5
    grid = [cell(col, row) for row in range(12) for col in range(8)
            if not (row == 4 and 1 <= col <= 3)] + [merged]
    c._snap.cells = grid

    def drag(start, end, modifiers=Qt.NoModifier):
        c._sel, c._grid = QRect(), []
        c._show_snap(cell(0, 0) if cell(0, 0).contains(start) else QRect(start - QPoint(5, 5), QSize(10, 10)))
        c._snap_anim.stop()
        c.mouse_press(event(start, modifiers))
        c.mouse_move(event((start + end) / 2, modifiers))
        c.mouse_move(event(end, modifiers))
        c.mouse_release(event(end, modifiers))
        return QRect(c._sel)

    try:
        a1 = cell(0, 0).center()
        assert drag(a1, cell(2, 2).center()) == cell(0, 0).united(cell(2, 2)), c._sel
        assert drag(a1, cell(2, 2).center() + QPoint(5, 3)) == cell(0, 0).united(cell(2, 2)), "格内位置不影响选区"
        assert drag(a1, cell(2, 3).center()) == cell(0, 0).united(cell(2, 3))
        assert drag(a1, cell(2, 4).center()) == cell(0, 0).united(merged), "压到合并格时包进整块"
        assert drag(cell(5, 6).center(), cell(0, 0).center()) == cell(0, 0).united(cell(5, 6)), "反向拖"
        assert drag(a1, QPoint(790, 590)) == cell(0, 0).united(cell(7, 11)), "拖出表格区停在最后一格"
        free = drag(a1, QPoint(250, 160), Qt.AltModifier)
        assert free == QRect(a1, QPoint(250, 160)).normalized() and c._grid == [], "按住 Alt 自由框选"
        # 拉右下角：右、下两边落到单元格边界；Alt 时自由。
        drag(a1, cell(2, 2).center())
        corner = c._sel.bottomRight()
        c.mouse_press(event(corner))
        c.mouse_move(event(corner + QPoint(80, 30)))
        c.mouse_release(event(corner + QPoint(80, 30)))
        assert c._sel == cell(0, 0).united(cell(3, 4)), c._sel
        corner = c._sel.bottomRight()
        c.mouse_press(event(corner))
        c.mouse_move(event(corner + QPoint(10, 5), Qt.AltModifier))
        c.mouse_release(event(corner + QPoint(10, 5), Qt.AltModifier))
        assert c._sel.bottomRight() == corner + QPoint(10, 5), "Alt 拉边角自由调整"
        # 单击（没拖动）仍采用吸附框；不在单元格上按下仍是普通框选。
        c._sel, c._grid = QRect(), []
        c._show_snap(cell(0, 0))
        c._snap_anim.stop()
        c.mouse_press(event(a1))
        c.mouse_release(event(a1))
        assert c._sel == cell(0, 0)
        assert drag(QPoint(20, 20), QPoint(150, 130)) == QRect(QPoint(20, 20), QPoint(150, 130)), "表格区外是自由框选"
    finally:
        c.close()
    print("PASS: Excel cell drag selects whole cells (merged cells included, clamped to the visible grid), "
          "handles snap to cell edges, Alt drags freely, click still adopts one cell")


if __name__ == "__main__":
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    check_edges()
    check_repaint()
    check_handles()
    check_wheel()
    check_cells()
