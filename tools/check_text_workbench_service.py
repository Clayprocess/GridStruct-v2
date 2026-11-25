"""离屏验收：文字证据先于结构结果到达，复用 OCR，并丢弃关窗后的迟到信号。"""

import os
from pathlib import Path
import sys
from threading import Event
from time import monotonic
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Signal, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from ocr import OCRDocument, OCRResult
from styles import tokens as T
from table import controller
from table.analysis import analyze
from table.pages import ImagePage


def evidence():
    image = QImage(240, 120, QImage.Format_RGB32)
    image.fill(T.PAPER_BACKGROUND)
    image.setDevicePixelRatio(2)
    result = OCRResult(QRect(10, 5, 60, 20), "斜线120", .97,
                       polygon=((20., 14.), (130., 10.), (132., 44.), (22., 48.)),
                       raw_index=7, chars=(("1", 75., 85.), ("2", 87., 98.), ("0", 100., 114.)))
    return image, (result,)


class ProbeDialog(QDialog):
    analysisRequested = Signal()

    def __init__(self, pages, parent=None, *, text_document=None, initial_mode=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.pages = pages
        self.text_document = text_document
        self.initial_mode = initial_mode or ("text" if text_document is not None else "table")
        self.pending = list(range(len(pages)))
        self.events = []

    def take_next_page(self):
        return self.pending.pop(0) if self.pending else None

    def page_request(self, _page):
        return None, None

    def set_stage(self, stage, page):
        self.events.append(("stage", page, stage))

    def apply_text_results(self, results, page, image):
        self.pages[page].results = results
        self.events.append(("text", page, results, image.devicePixelRatio()))

    def apply_analysis(self, tokens, tables, *, page):
        self.events.append(("table", page, tokens, tables))

    def show_error(self, error, page):
        self.events.append(("error", page, error))


def wait_for(app, condition):
    deadline = monotonic() + 5
    while not condition() and monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert condition(), "后台回调超时"


def close(app, dialog):
    dialog.close()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def main():
    app = QApplication.instance() or QApplication([])
    image, results = evidence()
    events = []
    with patch("table.analysis.table_regions", side_effect=RuntimeError("structure failed")), \
            patch("ocr.OCREngine.recognize_image", return_value=results) as recognize:
        try:
            analyze(image, on_results=lambda value: events.append(value))
        except RuntimeError as exc:
            assert str(exc) == "structure failed"
        else:
            raise AssertionError("没有触发结构失败")
        assert events == [results] and recognize.call_count == 1
        try:
            analyze(image, results=results, on_results=lambda value: events.append(value))
        except RuntimeError as exc:
            assert str(exc) == "structure failed"
        else:
            raise AssertionError("缓存证据后的结构失败未触发")
        assert recognize.call_count == 1 and events[-1] == results
    preserved = events[0][0]
    assert preserved.polygon == results[0].polygon and preserved.chars == results[0].chars
    assert preserved.raw_index == 7 and preserved.box == QRect(10, 5, 60, 20)

    # 构造文字入口不得显示窗口、启动线程或跑 OCR；图像和文档身份原样复用。
    document = OCRDocument(results, image)
    document.set_draft("人工改过的全文")
    with patch.object(controller, "TableDialog", ProbeDialog):
        dialog = controller.open_text_workbench(document, QPixmap.fromImage(image))
        assert not dialog.isVisible() and controller._service is None
        assert dialog.text_document is document and dialog.pages[0].results == document.raw_results
        assert dialog.pages[0].image.devicePixelRatio() == 2
        close(app, dialog)
        empty = controller.open_text_workbench(OCRDocument(()))
        assert empty.pages == [] and controller._service is None
        close(app, empty)

    with patch.object(controller, "TableDialog", ProbeDialog), patch.object(controller, "_submit_next") as submit:
        dialog = controller.open_table(image, text_document=document)
        assert dialog.isVisible() and dialog.initial_mode == "table"
        assert dialog.text_document is document and document.draft == "人工改过的全文"
        assert dialog.pages[0].results == document.raw_results
        submit.assert_called_once_with(dialog)
        close(app, dialog)

    # 两页都先交付真实文字，再报告结构失败。随后重试复用缓存，不重跑 OCR。
    with patch("ocr.OCREngine.recognize_image", return_value=results) as recognize, \
            patch("table.analysis.table_regions", side_effect=RuntimeError("structure failed")):
        dialog = ProbeDialog([ImagePage(image), ImagePage(image.copy())])
        controller._connect_analysis(dialog)
        controller._submit_next(dialog)
        wait_for(app, lambda: dialog not in controller._active)
        text = [entry for entry in dialog.events if entry[0] == "text"]
        assert len(text) == 2 and [entry[1] for entry in text] == [0, 1]
        assert all(entry[2] == results and entry[3] == 2 for entry in text)
        for page in (0, 1):
            kinds = [entry[0] for entry in dialog.events if entry[1] == page]
            assert kinds.index("text") < kinds.index("error")
            assert dialog.pages[page].results == results
        assert recognize.call_count == 2
        dialog.pending = [0]
        dialog.analysisRequested.emit()
        wait_for(app, lambda: dialog not in controller._active)
        assert recognize.call_count == 2
        close(app, dialog)

    started, release = Event(), Event()

    def slow_recognize(_image, on_stage=None):
        started.set()
        assert release.wait(5), "没有释放模拟 OCR"
        return results

    with patch("ocr.OCREngine.recognize_image", side_effect=slow_recognize), \
            patch("table.analysis.table_regions", return_value=[]), \
            patch("table.analysis.analyze_region", return_value=None):
        dialog = ProbeDialog([ImagePage(image)])
        controller._connect_analysis(dialog)
        controller._submit_next(dialog)
        wait_for(app, started.is_set)
        request = controller._active[dialog]
        event_list = dialog.events
        close(app, dialog)
        assert request.cancelled and dialog not in controller._active
        count = len(event_list)
        release.set()
        controller._service.shutdown()
        app.processEvents()
        assert len(event_list) == count, "关窗后仍向已销毁窗口送结果"
    controller._service.deleteLater()
    controller._service = None
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    print("PASS: raw OCR evidence/DPR before structure failure, lazy text factory, per-page reuse, late callback cancellation")


if __name__ == "__main__":
    main()
