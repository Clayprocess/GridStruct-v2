"""离屏验收：标注被“结束”或“打断”时不丢东西、不出错。

- Esc、托盘退出、改做长截图共用一条确认规则：有未保存的标注或改过的识别文字先确认，取消就留下。
- 贴图上标注不显示长截图按钮（没有对应的屏幕区域可滚动）。
- 按住鼠标画到一半切工具：这一笔照常提交，新工具不会收到没按下过的拖动。
- 截图会话阶段（Phase）在框选、标注、长截图之间切换正确，长截图启动失败不会卡住后续截图。

用法：python -X utf8 tools/check_annotation_end.py。只用合成图和临时配置。
"""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def sample():
    pixmap = QPixmap(400, 240)
    pixmap.fill(QColor("#ffffff"))
    return pixmap


def open_window(app, **options):
    from annotate import AnnotationWindow
    window = AnnotationWindow(sample(), QPoint(100, 100), "light", **options)
    window.show()
    flush(app)
    return window


def mouse(widget, kind, pos, buttons):
    button = Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton
    point = QPointF(pos)
    event = QMouseEvent(kind, point, QPointF(widget.mapToGlobal(pos)), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def draw_rect(app, window, start=QPoint(20, 20), end=QPoint(120, 90)):
    viewport = window._canvas.viewport()
    window.select_tool("rect")
    mouse(viewport, QEvent.MouseButtonPress, start, Qt.LeftButton)
    mouse(viewport, QEvent.MouseMove, end, Qt.LeftButton)
    mouse(viewport, QEvent.MouseButtonRelease, end, Qt.NoButton)
    flush(app)


def check_longshot_confirm(app):
    # 没有标注：直接改做长截图，不打断。
    window = open_window(app)
    requested = []
    window.longshotRequested.connect(requested.append)
    with patch("annotate.window._confirm_exit", return_value=False) as confirm:
        window.trigger_action("longshot")
        assert requested and not confirm.called, "没有标注时不该确认"
    window.close()
    flush(app)

    # 画过标注：先确认，取消就留在截图里，标注还在。
    window = open_window(app)
    requested = []
    window.longshotRequested.connect(requested.append)
    draw_rect(app, window)
    assert not window._canvas._undo.isClean()
    with patch("annotate.window._confirm_exit", return_value=False) as confirm:
        window.trigger_action("longshot")
        assert confirm.called, "有标注时改做长截图必须先确认（原来直接丢掉标注）"
        assert "未复制或保存的标注" in confirm.call_args.args[1]
        assert not requested and window.isVisible()
    with patch("annotate.window._confirm_exit", return_value=True):
        window.trigger_action("longshot")
        assert requested
    window.close()
    flush(app)


def check_pin_annotation(app):
    import pin
    with patch("pin._auto_recognize"):
        window = pin.show_pin(sample())
    flush(app)
    pin._annotate(window)
    flush(app)
    annotation = window.annotation
    button = annotation._toolbar._buttons["longshot"]
    assert button.isHidden(), "贴图上标注时长截图按钮点了没反应，应当不显示"
    assert not annotation._toolbar._buttons["ocr"].isHidden()
    annotation.close()
    window._allow_close = True
    window.close()
    flush(app)


def check_exit_confirm(app):
    from annotate import confirm_discard_all
    clean = open_window(app)
    marked = open_window(app)
    draw_rect(app, marked)
    with patch("annotate.window._confirm_exit", return_value=False) as confirm:
        assert not confirm_discard_all(), "托盘退出时有未保存标注要先确认，取消就不退出"
        assert confirm.call_count == 1 and marked.isVisible() and clean.isVisible()
    with patch("annotate.window._confirm_exit", return_value=True):
        assert confirm_discard_all()
    for window in (clean, marked):
        window.close()
    flush(app)
    assert confirm_discard_all(), "关掉的标注窗口不再参与确认"


def check_switch_while_drawing(app):
    errors = []
    previous = sys.excepthook
    sys.excepthook = lambda kind, value, trace: errors.append(value)
    try:
        window = open_window(app)
        canvas = window._canvas
        viewport = canvas.viewport()
        window.select_tool("rect")
        mouse(viewport, QEvent.MouseButtonPress, QPoint(20, 20), Qt.LeftButton)
        mouse(viewport, QEvent.MouseMove, QPoint(120, 90), Qt.LeftButton)
        # 按住鼠标时按 Shift+数字换工具。
        window.select_tool("ellipse")
        mouse(viewport, QEvent.MouseMove, QPoint(150, 120), Qt.LeftButton)
        mouse(viewport, QEvent.MouseButtonRelease, QPoint(150, 120), Qt.NoButton)
        flush(app)
        assert not errors, f"切工具后继续拖动出错：{errors!r}"
        marks = [item for item in canvas.scene().items() if item.flags() & item.GraphicsItemFlag.ItemIsSelectable]
        assert canvas._undo.count() == 1 and len(marks) == 1, \
            f"画到一半的矩形应提交为一条可撤销的标注（撤销栈 {canvas._undo.count()}，图元 {len(marks)}）"
        canvas.undo()
        marks = [item for item in canvas.scene().items() if item.flags() & item.GraphicsItemFlag.ItemIsSelectable]
        assert not marks, "撤销后不该残留半截图形"
        window.close()
        flush(app)
    finally:
        sys.excepthook = previous


class _FakeLongshot(QObject):
    """只记录状态的长截图：不抓屏、不装钩子。"""
    controlsShown = Signal()
    closed = Signal()

    def __init__(self, starts=True):
        super().__init__()
        self.active = False
        self.starts = starts
        self.region = QRect()

    @property
    def is_active(self):
        return self.active

    def start(self):
        self.active = self.starts

    def start_region(self, _capture, rect):
        self.region = QRect(rect)
        self.active = self.starts

    def cancel(self):
        if self.active:
            self.active = False
            self.closed.emit()


def check_flow_phases(app):
    from capture import DesktopCapture, ScreenFrame, ScreenshotFlow
    from capture.flow import Phase
    def shot():
        # 和真实截图一样每次重新取屏幕对象，离屏平台上旧的 QScreen 包装可能已失效。
        screen = app.primaryScreen()
        geometry = screen.geometry()
        picture = QPixmap(geometry.size())
        picture.fill(QColor("#ffffff"))
        return DesktopCapture([ScreenFrame(screen, geometry, picture, geometry.topLeft())])

    flow = ScreenshotFlow(app)
    longshot = _FakeLongshot()
    flow._longshot = longshot
    longshot.closed.connect(flow._longshot_closed)
    with patch("capture.flow.capture_virtual_desktop", side_effect=shot):
        flow.start()
        assert flow._phase is Phase.SELECTING
        flow._win._set_selection(QRect(100, 100, 300, 200))
        flow._trigger_action("longshot")
        flush(app)
        assert flow._phase is Phase.LONGSHOT and longshot.active and flow._win is None
        flow.start()
        assert flow._win is None, "长截图进行中不能再开新截图"
        # 长截图完成：先关长截图、再开标注窗口。
        longshot.active = False
        longshot.closed.emit()
        flow._on_longshot_result(sample())
        flush(app)
        assert flow._phase is Phase.ANNOTATING
        flow.start()
        assert flow._win is None, "长图标注时不能再开新截图"
        # 长图标注里有标注再点长截图：取消就留下，确认才重来。
        draw_rect(app, flow._ann)
        with patch("annotate.window._confirm_exit", return_value=False):
            flow._ann.trigger_action("longshot")
        assert flow._phase is Phase.ANNOTATING and flow._ann is not None
        with patch("annotate.window._confirm_exit", return_value=True):
            flow._ann.trigger_action("longshot")
        flush(app)
        assert flow._phase is Phase.LONGSHOT_PENDING and flow._ann is None
        flow._longshot_timer.stop()
        flow.start_pending_longshot()
        assert flow._phase is Phase.LONGSHOT and longshot.active
        flow.cancel()
        flush(app)
        assert flow._phase is Phase.IDLE and not longshot.active
        # 长截图没能启动（控制条无法排除等）：阶段回到空闲，不卡住之后的截图。
        longshot.starts = False
        flow.start_longshot()
        assert flow._phase is Phase.IDLE
        flow.start()
        assert flow._phase is Phase.SELECTING
        flow.cancel()
        flush(app)
        assert flow._phase is Phase.IDLE and flow._win is None
    flow.deleteLater()
    flush(app)


def main():
    app = QApplication.instance() or QApplication([])
    from styles import apply_theme
    apply_theme(app, "light")
    with TemporaryDirectory() as folder, \
            patch("settings.storage.data_directory", return_value=Path(folder)):
        try:
            check_longshot_confirm(app)
            check_pin_annotation(app)
            check_exit_confirm(app)
            check_switch_while_drawing(app)
            check_flow_phases(app)
        finally:
            app.aboutToQuit.emit()
    print("PASS: Esc / tray exit / longshot share one confirm rule for unsaved annotations")
    print("PASS: pin annotation hides the longshot button")
    print("PASS: switching tools mid-stroke commits the stroke, no stray item, no error")
    print("PASS: session phases: select -> longshot -> annotate -> longshot again -> idle; failed start never sticks")


if __name__ == "__main__":
    main()
