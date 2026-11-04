"""离屏回归：拖图时换画布，以及再次打开文字识别窗口时保留窗口状态。"""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ocr import OCRController, OCRDocument, OCREngine, OCRResult
from styles import tokens as T
from table.analysis import TableResult
from table.dialog import TableDialog
from table.grid import Candidate, Suggestion
from table.model import GridSpec
from table.pages import ImagePage


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def check_offline():
    image = QImage(601, 180, QImage.Format_RGB32)
    image.fill(QColor(T.LIGHT["background"]))
    with QPainter(image) as painter:
        painter.setPen(QColor(T.LIGHT["foreground"]))
        painter.setFont(QFont("Arial", 26))
        painter.drawText(20, 60, "GridStruct 123")
        painter.drawText(20, 120, "Hello World")
    engine = OCREngine()
    with TemporaryDirectory() as cache, patch("requests.sessions.Session.request",
                                            side_effect=AssertionError("OCR attempted network download")):
        engine._ensure()
        engine._engine.cfg.Global.model_root_dir = cache
        results = engine.recognize_image(image)
        assert [result.text for result in results] == ["GridStruct 123", "Hello World"]
        assert engine._engine.text_cls is None
        assert not list(Path(cache).glob("*.onnx"))
    print("PASS offline OCR: empty dependency model cache, classifier not loaded", flush=True)


def check_pan(app, image):
    grid = GridSpec((0, 200), (0, 300))
    result = TableResult(QRect(200, 300, 300, 200), (),
                         Suggestion(Candidate(grid, "structure", 1), (), 1, "回归"))
    for action in ("page", "regions", "analysis"):
        dialog = TableDialog([ImagePage(image), ImagePage(image)])
        dialog.show()
        if action != "analysis":
            dialog.apply_analysis((), (result,))
        flush(app)
        canvas = dialog._canvas
        # 等待识别时画布禁用，实际鼠标由保留的视口接收。
        receiver = dialog._area.viewport() if action == "analysis" else canvas
        QTest.mousePress(receiver, Qt.MiddleButton, Qt.NoModifier, QPoint(80, 80))
        assert dialog._zoom._press is not None
        if action == "page":
            QTest.keyClick(canvas, Qt.Key_PageDown, Qt.ControlModifier)
        elif action == "regions":
            QTest.keyClick(canvas, Qt.Key_R)
        else:
            QTimer.singleShot(0, lambda: dialog.apply_analysis((), (result,)))
        flush(app)
        assert dialog._canvas is not canvas
        assert dialog._zoom._press is None
        QTest.mouseMove(dialog._canvas, QPoint(100, 100))
        QTest.mouseRelease(dialog._canvas, Qt.MiddleButton, Qt.NoModifier, QPoint(100, 100))
        # 新画布仍能开始和结束下一次拖动。
        QTest.mousePress(dialog._canvas, Qt.MiddleButton, Qt.NoModifier, QPoint(80, 80))
        QTest.mouseRelease(dialog._canvas, Qt.MiddleButton, Qt.NoModifier, QPoint(100, 100))
        assert dialog._zoom._press is None
        dialog.close()
        flush(app)
        print(f"PASS pan replacement: {action}", flush=True)


def check_window(app, image):
    owner = QWidget()
    controller = OCRController(owner)
    document = OCRDocument([OCRResult(QRect(20, 20, 300, 30), "sample text", .99)], image)
    controller.adopt_document(document, QPixmap.fromImage(image))
    controller.show_results()
    flush(app)
    dialog = controller._dialogs[-1]
    for maximized in (False, True):
        (dialog.showMaximized if maximized else dialog.showNormal)()
        flush(app)
        controller.show_results()
        flush(app)
        assert dialog.isMaximized() == maximized
        dialog.showMinimized()
        flush(app)
        controller.show_results()
        flush(app)
        assert not dialog.isMinimized()
        assert dialog.isMaximized() == maximized
    dialog.close()
    flush(app)
    print("PASS OCR normal/maximized/minimized reopen", flush=True)


if __name__ == "__main__":
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    if sys.platform == "win32":
        # Windows 离屏插件不枚举系统字体，显式加载与真实 OCR 冒烟相同的字体。
        QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / "arial.ttf"))
    image = QImage(1800, 2400, QImage.Format_RGB32)
    image.fill(QColor(T.LIGHT["background"]))
    try:
        check_offline()
        check_pan(app, image)
        check_window(app, image)
    finally:
        app.aboutToQuit.emit()
