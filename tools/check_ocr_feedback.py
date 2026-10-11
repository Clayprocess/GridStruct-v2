"""可见业务窗口 OCR 反馈回归；用服务信号替代模型，不操作桌面输入。"""

from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QPoint, QRect, Signal, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QAbstractButton, QWidget, QPushButton, QVBoxLayout
from PySide6.QtTest import QTest

from annotate import AnnotationWindow
from pin import PinWindow
from ocr import OCRResult
from styles import apply_theme, tokens as T


def _check_tool_focus(app, service):
    import ctypes
    from ctypes import wintypes
    from capture import ScreenshotFlow, DesktopCapture, ScreenFrame
    foreground = ctypes.windll.user32.GetForegroundWindow
    foreground.restype = wintypes.HWND
    screen = app.primaryScreen()
    pixmap = QPixmap(screen.size())
    pixmap.fill(T.LIGHT["background"])
    capture = DesktopCapture([ScreenFrame(screen, screen.geometry(), pixmap, QPoint())])
    flow = ScreenshotFlow(app, "light")
    with patch("capture.flow.capture_virtual_desktop", return_value=capture):
        flow.start()
    flow._win._sel = QRect(120, 120, 640, 240)
    flow._win.confirm()
    owner = flow._ann
    QTest.qWait(160)
    assert owner.windowType() == Qt.Tool, "验收必须保留正式 Tool 类型"
    assert int(foreground()) == int(owner.winId()), "进入标注后前台仍为遮罩"
    controller = owner._ocr
    blocker = QWidget()
    blocker.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint)
    blocker.setWindowTitle("GridStruct 焦点恢复验收")
    blocker.resize(240, 80)
    try:
        for index in range(12):
            controller.start(owner._canvas.render_to_pixmap())
            QTest.qWait(30)
            blocker.show()
            blocker.raise_()
            blocker.activateWindow()
            QTest.qWait(40)
            assert int(foreground()) == int(blocker.winId()), "未建立焦点恢复干扰条件"
            request = controller._request
            if index % 2:
                controller.cancel()
            else:
                service.finished.emit(request, [OCRResult(QRect(20, 20, 120, 24), "Focus 123", .99)], "")
            QTest.qWait(80)
            assert int(foreground()) == int(owner.winId()), f"第 {index + 1} 次 OCR 结束未恢复前台"
            assert not controller.running and flow._win._frozen
        print("PASS: native Qt.Tool entry and 12 OCR completion/cancellation focus recoveries")
    finally:
        blocker.close()
        flow.cancel()


class _Service(QObject):
    requested = Signal(object)
    finished = Signal(object, object, str)
    stage = Signal(object, str)
    active = None


def _visible_text(window):
    return " ".join(child.text() for kind in (QLabel, QAbstractButton)
                    for child in window.findChildren(kind) if child.isVisible())


def main():
    app = QApplication(sys.argv)
    if app.platformName() == "offscreen":
        raise RuntimeError("此验收必须使用可见 Windows 窗口。")
    service = _Service()
    errors = []
    settings = type("Settings", (), {"value": lambda self, key, default, **kw:
                                    key == "ocr/hide_empty_notice" or default})()
    with (patch("ocr.controller._get_service", return_value=service),
          patch("settings.get_settings", return_value=settings),
          patch("ocr.controller.show_message") as warning):
        if "--focus-check" in sys.argv:
            anchor = QWidget()
            anchor.setWindowTitle("GridStruct 正式 Tool 焦点验收")
            layout = QVBoxLayout(anchor)
            layout.addWidget(QLabel("从可见按钮启动，核对正式 Tool 窗口的真实前台 HWND。"))
            button = QPushButton("运行焦点验收")
            layout.addWidget(button)
            def run_focus_check():
                try:
                    _check_tool_focus(app, service)
                except Exception as error:
                    print(f"FAIL native focus: {error}", flush=True)
                    app.exit(1)
                else:
                    app.exit(0)
            button.clicked.connect(run_focus_check)
            anchor.show()
            return app.exec()
        for mode in ("light", "dark"):
            apply_theme(app, mode)
            pixmap = QPixmap(640, 240)
            pixmap.fill(T.THEMES[mode]["background"])
            windows = [AnnotationWindow(pixmap, QPoint(120, 120), mode), PinWindow(pixmap)]
            for window in windows:
                window.show()
                app.processEvents()
                if isinstance(window, AnnotationWindow):
                    window.trigger_action("ocr")
                    controller = window._ocr
                else:
                    window._recognize()
                    controller = window._ocr_controller
                controller._request.started_at = monotonic()
                service.stage.emit(controller._request, "recognizing")
                app.processEvents()
                if "正在本地识别" not in _visible_text(window):
                    errors.append(f"{mode}/{type(window).__name__}: 无运行状态")
                controller.cancel()
                app.processEvents()
                if "已取消" not in _visible_text(window):
                    errors.append(f"{mode}/{type(window).__name__}: 无取消状态")
                controller.start(pixmap)
                service.finished.emit(controller._request, [], "验收用模型错误")
                app.processEvents()
                if "识别失败" not in _visible_text(window) or not warning.called:
                    errors.append(f"{mode}/{type(window).__name__}: 无失败状态")
                controller.start(pixmap)
                controller._timer.timeout.emit()
                app.processEvents()
                if "识别超时" not in _visible_text(window):
                    errors.append(f"{mode}/{type(window).__name__}: 无超时状态")
                controller.start(pixmap)
                service.finished.emit(controller._request, [], "")
                app.processEvents()
                if "未检测到文字" not in _visible_text(window):
                    errors.append(f"{mode}/{type(window).__name__}: 无空结果状态")
                controller.start(pixmap)
                results = [OCRResult(QRect(20, 20, 180, 30), "识别反馈验收", 0.99)]
                service.finished.emit(controller._request, results, "")
                app.processEvents()
                text = _visible_text(window)
                if "已识别" not in text or "原图" not in text:
                    errors.append(f"{mode}/{type(window).__name__}: 识别完成后无可见提示：{text}")
                if isinstance(window, AnnotationWindow):
                    buttons = [b for b in window.findChildren(QAbstractButton)
                               if b.isVisible() and b.text() == "查看全文"]
                    if not buttons:
                        errors.append(f"{mode}/AnnotationWindow: 没有可见全文入口")
                    else:
                        buttons[0].click()
                        app.processEvents()
                        if not controller._dialogs or not controller._dialogs[-1].isVisible():
                            errors.append("全文按钮未打开结果对话框")
                        else:
                            controller._dialogs[-1].close()
                    window.select_tool("arrow")
                    canvas = window._canvas
                    viewport = canvas.viewport()
                    QTest.mousePress(viewport, Qt.LeftButton, pos=QPoint(200, 80))
                    QTest.mouseMove(viewport, QPoint(300, 160))
                    QTest.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(300, 160))
                    app.processEvents()
                    if controller.document is not None or window._text_layer is not None:
                        errors.append(f"{mode}/AnnotationWindow: 新标注未作废旧 OCR")
                    if canvas.tool_id != "arrow" or not window._options.isVisible():
                        errors.append(f"{mode}/AnnotationWindow: OCR 失效打断连续标注")
                    QTest.mousePress(viewport, Qt.LeftButton, pos=QPoint(400, 80))
                    QTest.mouseMove(viewport, QPoint(500, 160))
                    QTest.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(500, 160))
                    marks = [item for item in canvas.scene().items() if getattr(item, "tool_id", "") == "arrow"]
                    if len(marks) != 2:
                        errors.append(f"{mode}/AnnotationWindow: OCR 后无法连续绘制两个图形")
                window._allow_close = True
                window.close()
                app.processEvents()
    for error in errors:
        print("FAIL", error)
    print(f"OCR feedback: {'FAIL' if errors else 'PASS'}")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
