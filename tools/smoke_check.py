"""离屏功能回归：python -X utf8 tools/smoke_check.py。使用合成图片，不读取桌面或用户配置。"""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QMouseEvent, QPainter, QPixmap, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from annotate import AnnotationCanvas
from annotate.items import SerialMarkItem, TextMarkItem
from capture.longshot import ImageStitcher
from ocr import OCREngine
from pin import PinWindow
from settings import SettingsDialog
from styles import tokens as T
from styles import apply_theme

app = QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)
if sys.platform == "win32":
    # Windows 的 offscreen 插件不自动枚举系统字体。
    for filename in ("arial.ttf", "msyh.ttc"):
        QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
errors = []
sys.excepthook = lambda kind, value, tb: errors.append(f"{kind.__name__}: {value}")


def flush():
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def sample(width=240, height=160, dpr=1):
    pm = QPixmap(width, height)
    pm.fill(QColor(T.LIGHT["background"]))
    with QPainter(pm) as painter:
        painter.fillRect(0, 0, width // 2, height, QColor(T.LIGHT["foreground"]))
    pm.setDevicePixelRatio(dpr)
    return pm


def high_dpi_canvas():
    for dpr in (1, 1.25, 1.5, 2):
        pm = sample(dpr=dpr)
        canvas = AnnotationCanvas(pm)
        assert canvas.scene().items()[0].sceneBoundingRect().size() == pm.deviceIndependentSize()
        out = canvas.render_to_pixmap()
        assert out.toImage().convertToFormat(QImage.Format_RGBA8888) == pm.toImage().convertToFormat(QImage.Format_RGBA8888), f"DPR={dpr}: screenshot pixels changed"
        canvas.deleteLater()
        flush()


def ocr_pixels():
    engine = OCREngine()
    previous = engine._engine
    seen = []
    def infer(arr):
        seen.append(arr.shape)
        return SimpleNamespace(boxes=np.array([[[0, 0], [20, 0], [20, 10], [0, 10]]] * 2),
                               txts=["hello", "world"], scores=np.array([0.9, 0.8]))
    engine._engine = infer
    try:
        for width in (100, 101, 102, 103):
            result = engine.recognize(sample(width, 40))
            assert result[0].text == "hello"
            assert seen[-1] == (40, width, 3)
    finally:
        engine._engine = previous


def offline_models():
    engine = OCREngine()
    previous = engine._engine
    engine._engine = None
    try:
        with patch("requests.sessions.Session.request", side_effect=AssertionError("OCR attempted network download")):
            engine._ensure()
            for key in ("Det", "Cls", "Rec"):
                path = engine._engine.cfg[key].model_path
                assert path and Path(path).is_file(), f"{key}: repository model not configured"
    finally:
        engine._engine = previous


def empty_polyline():
    c = AnnotationCanvas(sample())
    c.set_tool_by_id("polyline")
    c._tool.on_right_press(QPointF(10, 10), c._undo)
    assert c._undo.count() == 0
    c.deleteLater()


def text_undo():
    c = AnnotationCanvas(sample(640, 300))
    c.resize(640, 300)
    c.show()
    c.activateWindow()
    c.setFocus()
    c.set_tool_by_id("text")
    flush()
    QTest.mouseClick(c.viewport(), Qt.LeftButton, pos=c.mapFromScene(QPointF(40, 40)))
    QTest.keyClicks(c.viewport(), "hello")
    c.set_tool_by_id("rect")
    marks = [i for i in c.scene().items() if isinstance(i, TextMarkItem)]
    assert len(marks) == 1 and marks[0].toPlainText() == "hello", "text input failed"
    c.undo()
    assert not any(isinstance(i, TextMarkItem) for i in c.scene().items()), "typed text cannot be undone"
    c.redo()
    assert marks[0] in c.scene().items()
    c.close()
    c.deleteLater()


def serial_undo():
    c = AnnotationCanvas(sample())
    c.set_tool_by_id("serial")
    for x in (30, 90, 150):
        c._tool.on_press(c.scene(), QPointF(x, 40), c._undo)
        c._tool.on_release(QPointF(x, 40), c._undo)
    items = sorted((i for i in c.scene().items() if isinstance(i, SerialMarkItem)), key=lambda i: i.index)
    c.scene().clearSelection()
    items[1].setSelected(True)
    QTest.keyClick(c, Qt.Key_Delete)
    assert sorted(i.index for i in c.scene().items() if isinstance(i, SerialMarkItem)) == [1, 2]
    c.undo()
    assert [i.index for i in items] == [1, 2, 3], "undo leaves duplicate serial numbers"
    c.redo()
    assert sorted(i.index for i in c.scene().items() if isinstance(i, SerialMarkItem)) == [1, 2]
    c.scene().clearSelection()
    items[2].setSelected(True)
    c.update_style("serial_type", "letter")
    assert items[2].style.serial_type == "letter" and items[2]._label() == "B"
    c.undo()
    assert items[2].style.serial_type == "circle" and items[2]._label() == "2"
    c.deleteLater()


def pin_geometry():
    p = PinWindow(sample(400, 200, 2))
    p.show()
    flush()
    assert p._content.width() == 200 and p._content.height() == 100, "pin uses physical size as logical size"
    assert p.height() == p._content.height() + p._status.height()
    p.zoom(2)
    assert p._content.width() == 400 and p._content.height() == 200
    p._allow_close = True
    p.close()
    p.deleteLater()


def longshot_pixels():
    rng = np.random.default_rng(12)
    arr = rng.integers(0, 256, (600, 200, 3), dtype=np.uint8)
    source = QPixmap.fromImage(QImage(arr.data, 200, 600, arr.strides[0], QImage.Format_RGB888).copy())
    for dpr in (1, 1.25, 1.5, 2):
        first, second = source.copy(0, 0, 200, 400), source.copy(0, 200, 200, 400)
        first.setDevicePixelRatio(dpr)
        second.setDevicePixelRatio(dpr)
        stitch = ImageStitcher()
        assert stitch.feed(first)
        assert stitch.feed(second)
        assert stitch.height == 600, f"DPR={dpr}: stitched height {stitch.height}, expected 600"
        result = stitch.result.toImage()
        result.setDevicePixelRatio(1)
        assert result == source.toImage(), "overlap changed pixels"
        stitch.feed(second)
        assert stitch.height == 600, "unchanged frame appended again"


def cancelled_settings():
    with TemporaryDirectory() as folder:
        s = QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
        s.setValue("theme", "light")
        with patch("settings.dialog.get_settings", return_value=s):
            dialog = SettingsDialog()
            dialog._theme.setCurrentIndex(dialog._theme.findData("dark"))
            dialog.reject()
            assert s.value("theme") == "light", "cancel persisted theme"
            dialog.deleteLater()


def real_ocr():
    pm = QPixmap(601, 180)
    pm.fill(QColor(T.LIGHT["background"]))
    with QPainter(pm) as painter:
        painter.setPen(QColor(T.LIGHT["foreground"]))
        painter.setFont(QFont("Arial", 26))
        painter.drawText(20, 60, "GridStruct 123")
        painter.drawText(20, 120, "Hello World")
    with patch("requests.sessions.Session.request", side_effect=AssertionError("network access during OCR")):
        result = OCREngine().recognize(pm)
    assert [r.text for r in result] == ["GridStruct 123", "Hello World"], [r.text for r in result]


def all_tools():
    from annotate import TOOL_CLASSES
    for name in TOOL_CLASSES:
        if name == "text":
            continue  # text_undo 通过实际键盘事件覆盖。
        c = AnnotationCanvas(sample(640, 300))
        c.set_tool_by_id(name)
        tool = c._tool
        tool.on_press(c.scene(), QPointF(40, 40), c._undo)
        tool.on_move(QPointF(160, 100))
        tool.on_release(QPointF(160, 100), c._undo)
        if name == "polyline":
            tool.on_press(c.scene(), QPointF(160, 100), c._undo)
            tool.on_right_press(QPointF(160, 100), c._undo)
        rendered = c.render_to_pixmap()
        assert not rendered.isNull()
        if name == "magnifier":
            assert c._loupe is not None and c._undo.count() == 0
            assert len(c.scene().items()) == 1, "loupe modified the captured image"
        elif name != "eraser":
            assert c._undo.count() == 1, name
            c.undo()
            assert len(c.scene().items()) == 1, name
            c.redo()
            assert len([item for item in c.scene().items() if item.parentItem() is None]) == 2, name
        c.deleteLater()
        flush()


def empty_shapes():
    for name in ("rect", "ellipse"):
        c = AnnotationCanvas(sample())
        c.set_tool_by_id(name)
        c._tool.on_press(c.scene(), QPointF(20, 20), c._undo)
        c._tool.on_release(QPointF(20, 20), c._undo)
        assert c._undo.count() == 0 and len(c.scene().items()) == 1, name
        c.deleteLater()


def scroll_sampling():
    from capture import LongShotFlow
    from capture.longshot import LongShotBar
    from PySide6.QtCore import QObject, Signal
    flow = LongShotFlow(app, lambda pm: None)
    flow._bar = LongShotBar()
    flow._bar.move(1000, 1000)
    flow._screen_region = QRect(0, 0, 400, 400)
    before, after = sample(200, 200), sample(200, 200)
    after.fill(QColor(T.LIGHT["foreground"]))
    flow._last_frame = before
    flow._toggle_capture()
    def append_frame(frame):
        flow._last_frame = frame
        return True
    with patch.object(flow, "_grab", side_effect=[before, after, after]) as grab, \
         patch.object(flow, "_append_frame", side_effect=append_frame) as append, \
         patch.object(flow._manager, "cursor_position", return_value=QPoint(100, 100)):
        flow._on_wheel(-120)
        timer_id = flow._sample_timer.timerId()
        for _ in range(10):
            flow._on_wheel(-120)
            assert flow._sample_timer.timerId() == timer_id, "continuous wheel postponed capture"
        assert grab.call_count == 0, "capture ran inside wheel handler"
        flow._sample_frame()
        assert append.call_count == 0, "unchanged frame appended"
        flow._sample_frame()
        assert append.call_count == 1, "intermediate changed frame lost"
        flow._sample_frame()
        assert append.call_count == 1, "duplicate frame appended"
    class Source(QObject):
        wheel = Signal(int)
    source = Source()
    source.wheel.connect(flow._on_wheel, Qt.QueuedConnection)
    source.wheel.emit(-120)
    flow._cleanup()
    flush()
    assert not flow._sample_timer.isActive(), "stale wheel restarted finished session"
    flow.deleteLater()
    result = []
    flow = LongShotFlow(app, result.append)
    flow._bar = LongShotBar()
    white = QPixmap(200, 200)
    white.fill(QColor(T.LIGHT["background"]))
    flow._last_frame = white
    flow._stitcher.feed(white)
    flow._toggle_capture()
    with patch.object(flow, "_grab", return_value=white):
        flow._finish()
        assert not result, "finish delivered before returning to the event loop"
        flush()
    assert result[0].height() == 200, "finish appended unchanged blank frame"
    assert not flow._stitcher.feed(white), "blank repeated frame appended"
    flow.deleteLater()


def screenshot_lifecycle():
    from capture import ScreenshotFlow, DesktopCapture, ScreenFrame
    flow = ScreenshotFlow(app)
    source = sample(800, 600)
    shot = DesktopCapture([ScreenFrame(app.primaryScreen(), QRect(-800, 0, 800, 600), source, QPoint(-800, 0))])
    copied = []
    clipboard = SimpleNamespace(setPixmap=lambda pixmap: copied.append(QPixmap(pixmap)))
    with patch("capture.flow.capture_virtual_desktop", return_value=shot), \
         patch("capture.flow.QApplication.clipboard", return_value=clipboard):
        for _ in range(3):
            flow.start()
            flow._win._sel = QRect(-760, 50, 300, 200)
            flow._win.confirm()
            window = flow._ann
            assert window.pos() == QPoint(-760, 50), window.pos()
            window._on_action("confirm")
            flush()
            assert (copied[-1].toImage().convertToFormat(QImage.Format_RGBA8888)
                    == source.copy(40, 50, 300, 200).toImage().convertToFormat(QImage.Format_RGBA8888))
            assert flow._ann is None and flow._win is None
        flow.start()
        flow._win._sel = QRect(-760, 50, 300, 200)
        QTest.keyClick(flow._win._windows[0], Qt.Key_Return)
        flush()
        assert flow._ann is None and flow._win is None, "Enter did not complete capture"
        flow.start()
        flow._win.cancelled.emit()
        flush()
        assert flow._win is None
        flow.start()
        flow._win._windows[0].close()
        flush()
        assert flow._win is None, "window-manager close left capture session active"
        flow.start()
        flow._win._sel = QRect(-760, 50, 300, 200)
        flow._win.confirm()
        flow._ann.close()
        flush()
        assert flow._ann is None, "window-manager close left annotation session active"
    flow.deleteLater()


def pin_lifecycle():
    from pin import show_pin, _open_pins
    count = len(_open_pins)
    w = show_pin(sample())
    assert len(_open_pins) == count + 1
    w._allow_close = True
    w.close()
    QTest.qWait(180)
    flush()
    assert len(_open_pins) == count, "closed pin remains retained"


def longshot_lifecycle():
    from capture import LongShotFlow, DesktopCapture, ScreenFrame
    result = []
    flow = LongShotFlow(app, result.append)
    source = sample(800, 600)
    shot = DesktopCapture([ScreenFrame(app.primaryScreen(), QRect(-800, 0, 800, 600), source, QPoint(-800, 0))])
    with patch("capture.longshot.capture_virtual_desktop", return_value=shot), \
         patch("capture.wheelhook.WheelHook.start", return_value=True), \
         patch.object(flow, "_grab", side_effect=AssertionError("first frame captured overlay")):
        flow.start()
        overlay = flow._win
        flow.start()
        assert flow._win is overlay, "duplicate longshot session"
        flow._win._sel = QRect(-780, 30, 300, 200)
        QTest.keyClick(flow._win._windows[0], Qt.Key_Return)
        assert flow._last_frame.toImage() == source.copy(20, 30, 300, 200).toImage()
        flow._toggle_capture()
        flow._sample_timer.start()
        flow._bar.close()
        flush()
        assert flow._bar is None and flow._hook is None and not flow._sample_timer.isActive()
        flow.start()
        flow._win._sel = QRect(-780, 30, 300, 200)
        QTest.keyClick(flow._win._windows[0], Qt.Key_Return)
        assert not flow._bar._finish_button.isEnabled()
        flow._finish()
        assert not result, "unstarted longshot delivered a result"
        flow._toggle_capture()
        flow._finish()
        flush()
        assert len(result) == 1 and result[0].size() == source.copy(20, 30, 300, 200).size()
    flow.deleteLater()


def save_failure():
    from annotate import AnnotationWindow
    w = AnnotationWindow(sample(), QPoint())
    app.clipboard().setText("preserve")
    with TemporaryDirectory() as folder:
        missing = str(Path(folder) / "missing" / "shot.png")
        with patch("export.image.QFileDialog.getSaveFileName", return_value=(missing, "PNG (*.png)")), \
             patch("export.image.show_message") as warning:
            w.trigger_action("save")
            assert warning.call_count == 1
            assert app.clipboard().text() == "preserve"
    w.close()
    w.deleteLater()


def saved_settings():
    import settings.dialog as settings_module
    with TemporaryDirectory() as folder:
        s = QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
        s.setValue("theme", "light")
        with patch("settings.dialog.get_settings", return_value=s), \
             patch("settings.dialog._write_autostart") as autostart:
            dialog = SettingsDialog()
            dialog._theme.setCurrentIndex(dialog._theme.findData("dark"))
            dialog._img_fmt.setCurrentText("jpg")
            dialog._save()
            assert s.value("theme") == "dark" and s.value("image_format") == "jpg"
            assert app.property("theme_mode") == "dark"
            if sys.platform == "win32":
                assert autostart.call_count == 1
                cmd = settings_module._autostart_command()
                assert "pythonw.exe" in cmd and str(Path(__file__).resolve().parents[1] / "main.py") in cmd
            dialog.deleteLater()
        from annotate import AnnotationWindow
        w = AnnotationWindow(sample(), QPoint())
        destination = str(Path(folder) / "saved.jpg")
        with patch("export.image.get_settings", return_value=s), \
             patch("export.image.QFileDialog.getSaveFileName", return_value=(destination, "JPG (*.jpg)")) as choose:
            w.trigger_action("save")
            assert choose.call_args.args[2].endswith("GridStruct-V2.jpg")
            assert not QImage(destination).isNull()
        w.close()
        w.deleteLater()


def canvas_mouse_events():
    from annotate import AnnotationWindow
    from pin import _open_pins
    w = AnnotationWindow(sample(640, 320), QPoint())
    w.show()
    flush()
    c = w._canvas
    for name in ("rect", "ellipse", "arrow", "line", "pencil", "highlight", "mosaic", "magnifier", "serial"):
        w._on_tool(name)
        p1, p2 = c.mapFromScene(QPointF(30, 30)), c.mapFromScene(QPointF(120, 90))
        QTest.mouseMove(c.viewport(), p1)
        QTest.mousePress(c.viewport(), Qt.LeftButton, pos=p1)
        move = QMouseEvent(QEvent.MouseMove, QPointF(p2), QPointF(c.viewport().mapToGlobal(p2)),
                           Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(c.viewport(), move)
        QTest.mouseRelease(c.viewport(), Qt.LeftButton, pos=p2)
        assert not errors, errors
        c.undo()
    w.trigger_action("pipette")
    from annotate.color_dialog import ColorDialog
    with patch.object(ColorDialog, "exec", return_value=ColorDialog.Accepted):
        QTest.mouseClick(c.viewport(), Qt.LeftButton, pos=c.mapFromScene(QPointF(400, 80)))
    assert app.clipboard().text() == T.LIGHT["background"].upper()
    assert not c.pipette_active
    count = len(_open_pins)
    w._on_action("pin")
    assert len(_open_pins) == count + 1 and not w.isVisible()
    _open_pins[-1]._allow_close = True
    _open_pins[-1].close()
    w.deleteLater()


def ocr_copy_text():
    from ocr import OCRResult, OCRResultDialog
    dialog = OCRResultDialog([OCRResult(QRect(), "plain text", 0.99)])
    cursor = dialog._text.textCursor()
    cursor.select(QTextCursor.Document)
    dialog._text.setTextCursor(cursor)
    assert dialog._copy_selection.isEnabled()
    dialog._copy_selection.click()
    assert app.clipboard().text() == "plain text"
    dialog._copy_all()
    assert app.clipboard().text() == "plain text"
    dialog.deleteLater()


def render_windows():
    from annotate import AnnotationWindow, ColorDialog
    from ocr import OCRResult, OCRResultDialog
    from capture.longshot import LongShotBar
    from ui import MessageDialog
    folder = Path(__file__).resolve().parents[1] / ".local-run" / "smoke"
    folder.mkdir(parents=True, exist_ok=True)
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        annotation = AnnotationWindow(sample(640, 320), QPoint(), mode)
        windows = {"annotation": annotation, "toolbar": annotation._toolbar,
                   "settings": SettingsDialog(),
                   "ocr": OCRResultDialog([OCRResult(QRect(0, 0, 40, 20), "合成识别结果 Sample 123", 0.99)]),
                   "pin": PinWindow(sample(400, 200, 2)), "longshot": LongShotBar(),
                   "color": ColorDialog(QColor(T.ANNOTATION_COLORS[0])),
                   "message": MessageDialog(None, "达到长截图上限", "已达到 29000 像素的拼接上限，可使用当前结果。",
                                            checkbox_text="下次不再提示")}
        for name, window in windows.items():
            window.show()
            flush()
            assert window.grab().save(str(folder / f"{name}-{mode}.png"))
        for window in windows.values():
            if isinstance(window, PinWindow):
                window._allow_close = True
            window.close()
            window.deleteLater()
        flush()


if __name__ == "__main__":
    checks = [high_dpi_canvas, ocr_pixels, offline_models, empty_polyline,
              text_undo, serial_undo, pin_geometry, longshot_pixels, cancelled_settings,
              real_ocr, all_tools, empty_shapes, scroll_sampling, screenshot_lifecycle, pin_lifecycle, save_failure,
              longshot_lifecycle, saved_settings, canvas_mouse_events, ocr_copy_text, render_windows]
    failures = []
    with TemporaryDirectory() as folder, ExitStack() as isolated:
        directory = Path(folder)
        settings = QSettings(str(directory / "settings.ini"), QSettings.IniFormat)
        settings.setValue("config_version", 1)
        settings.sync()
        # 生产模块已迁到便携配置；验收仍只读写临时目录，不迁移用户注册表。
        isolated.enter_context(patch("settings.storage.data_directory", return_value=directory))
        isolated.enter_context(patch("pin.data_directory", return_value=directory))
        isolated.enter_context(patch("settings.dialog._read_autostart", return_value=False))
        try:
            for check in checks:
                errors.clear()
                try:
                    check()
                    flush()
                    assert not errors, errors
                except Exception as exc:
                    failures.append(check.__name__)
                    print(f"FAIL {check.__name__}: {type(exc).__name__}: {exc}", flush=True)
                else:
                    print(f"PASS {check.__name__}", flush=True)
        finally:
            # 脚本不运行 app.exec()，显式触发后台 OCR 服务的退出收尾。
            app.aboutToQuit.emit()
    print(f"RESULT: {len(checks) - len(failures)}/{len(checks)} passed", flush=True)
    sys.exit(bool(failures))
