"""阶段 B 数据及截图转贴图接线冒烟；只使用合成图片和隔离会话。"""

import copy
import json
import os
import sys
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, QRect, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from annotate import AnnotationWindow
from ocr import OCRDocument, OCREngine, OCRResult
from styles import tokens as T
import pin


class Service(QObject):
    requested = Signal(object)
    finished = Signal(object, object, str)
    stage = Signal(object, str)
    active = None


app = QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)
errors = []
def report_error(kind, value, tb):
    errors.append(str(value))
    traceback.print_exception(kind, value, tb)

sys.excepthook = report_error
evidence = ROOT / ".scratch" / "ocr-stages-bcd" / "data"
evidence.mkdir(parents=True, exist_ok=True)


def flush():
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not errors, errors


def image(dpr=1):
    result = QImage(600, 300, QImage.Format_RGBA8888)
    result.fill(QColor(T.LIGHT["background"]))
    result.setPixelColor(5, 5, QColor(T.LIGHT["foreground"]))
    result.setDevicePixelRatio(dpr)
    return result


def row(text, x, y, width=90):
    return OCRResult(QRect(x, y, width, 20), text, .4)


def data_contract():
    source = image(1.5)
    records = [row("右下", 220, 100), row("左下", 10, 100),
               row("标题", 10, 10, 400), row("右上", 220, 75), row("左上", 10, 75)]
    document = OCRDocument(records, source)
    assert [item.text for item in document] == ["标题", "左上", "左下", "右上", "右下"]
    assert [item.text for item in document.raw_results] == [item.text for item in records]
    identifiers = [item.item_id for item in document]
    document.correct((identifiers[1],), "左上校正")
    document.relayout()
    assert [item.item_id for item in document] == identifiers
    assert document.results[1].text == "左上校正"
    assert document.results[1].original_text == "左上"
    assert len(document.raw_results) == 5 and document.raw_results[-1].score == .4
    document.raw_results[0].box.setX(555)
    document.results[-1].box.translate(33, 0)
    records[0].box.setX(666)
    assert document.raw_results[0].box.x() == 220
    source.setPixelColor(2, 2, QColor(T.LIGHT["foreground"]))
    assert not document.matches(source)
    assert document.matches(document.image)
    paragraph = document.groups()[1]
    document.correct(paragraph, "整段自由改写\n第二行")
    assert document.results[1].source_ids == paragraph
    assert document.results[1].polygon == () and len(document) == 4
    document.set_draft("独立草稿")
    document.correct((identifiers[0],), "标题更新")
    assert document.draft_stale and document.draft == "独立草稿"
    encoded = json.loads(json.dumps(document.to_payload(), ensure_ascii=False))
    restored = OCRDocument.from_payload(encoded, document.image)
    assert restored.to_payload() == encoded and restored.text() == document.text()
    for mutate in (lambda p: p.update(version=True),
                   lambda p: p["layout"][0].update(id=[]),
                   lambda p: p["raw"][0].update(score=float("nan")),
                   lambda p: p["raw"][0].update(score=10**400),
                   lambda p: p["raw"][0].update(polygon=[[10**400, 0]] * 4),
                   lambda p: p["raw"][0].update(polygon=[[0, 0]]),
                   lambda p: p["layout"].pop(),
                   lambda p: p.update(draft_revision=-1)):
        corrupted = copy.deepcopy(encoded)
        mutate(corrupted)
        try:
            OCRDocument.from_payload(corrupted, document.image)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed OCR payload accepted")
    assert not OCRDocument([], image())
    assert OCRDocument([OCRResult(QRect(), "旧全文", .9)]).text() == "旧全文"
    for dpr in (1, 1.25, 1.5, 2, 2.25):
        source = image(dpr)
        document = OCRDocument([row("DPR", 10, 10)], source)
        filename = evidence / "roundtrip.png"
        assert source.save(str(filename))
        restored_image = QImage(str(filename))
        restored_image.setDevicePixelRatio(dpr)
        assert document.matches(restored_image)
        assert OCRDocument.from_payload(document.to_payload(), restored_image).text() == "DPR"
    print("PASS DATA: raw evidence, columns/title, stable IDs, correction, draft, validation, DPR PNG")


def engine_geometry():
    engine = OCREngine()
    previous = engine._engine
    polygon = [[30, 45], [150, 30], [156, 60], [36, 75]]
    engine._engine = lambda arr: SimpleNamespace(
        boxes=np.array([polygon, [[0, 0], [10, 0], [10, 10], [0, 10]]]),
        txts=["旋转", ""], scores=[.35, .1])
    try:
        phases = []
        result = engine.recognize_image(image(1.5), phases.append)
        assert phases == ["recognizing"]
        assert result[0].polygon == tuple(tuple(float(value) for value in point) for point in polygon)
        assert result[0].box == QRect(20, 20, 84, 30)
        assert [item.raw_index for item in result] == [0, 1]
        document = OCRDocument(result, image(1.5))
        assert len(document.raw_results) == 2 and len(document) == 1
    finally:
        engine._engine = previous
    print("PASS DATA: physical rotated polygon, logical DPR box, raw engine order and empty evidence")


def annotation_transfer():
    service = Service()
    settings = SimpleNamespace(value=lambda key, default=False, **kwargs: key == "ocr/hide_empty_notice" or default)
    with TemporaryDirectory(dir=evidence) as folder, \
         patch("pin.data_directory", return_value=Path(folder)), \
         patch("ocr.controller._get_service", return_value=service), \
         patch("settings.get_settings", return_value=settings), \
         patch("ocr.controller.show_message"), \
         patch.object(OCREngine, "recognize_image", side_effect=AssertionError("transfer started inference")):
        pixmap = QPixmap.fromImage(image(1.5))
        window = AnnotationWindow(pixmap, QPoint())
        window.show()
        window.trigger_action("ocr")
        request = window._ocr._request
        request.started_at = monotonic()
        service.stage.emit(request, "recognizing")
        service.finished.emit(request, [row("原文", 10, 20)], "")
        document = window._ocr.document
        document.correct((document.results[0].item_id,), "已校正")
        assert window._render().toImage().convertToFormat(QImage.Format_RGBA8888) == pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        window.trigger_action("pin")
        flush()
        target = pin._open_pins[-1]
        assert target._document is document and target._document.text() == "已校正"
        target._document.correct((target._document.results[0].item_id,), "来源关闭后校正")
        target._recognize()
        assert target._ocr_controller._dialogs[-1]._document is document
        fresh = AnnotationWindow(pixmap, QPoint())
        fresh._ocr.adopt_document(document, pixmap)
        fresh.select_tool("rect")
        tool = fresh._canvas._tool
        tool.on_press(fresh._canvas.scene(), QPointF(10, 80), fresh._canvas._undo)
        tool.on_move(QPointF(100, 130))
        tool.on_release(QPointF(100, 130), fresh._canvas._undo)
        assert fresh._ocr.document is None and fresh._text_layer is None
        assert target._document is document
        fresh.trigger_action("ocr")
        obsolete = fresh._ocr._request
        fresh._canvas.undo()
        assert obsolete.cancelled.is_set() and not fresh._ocr.running
        service.finished.emit(obsolete, [row("迟到", 10, 20)], "")
        assert fresh._ocr.document is None
        fresh.select_tool("rect")
        fresh.trigger_action("ocr")
        pending = fresh._ocr._request
        fresh.select_tool("rect")
        tool = fresh._canvas._tool
        tool.on_press(fresh._canvas.scene(), QPointF(20, 85), fresh._canvas._undo)
        tool.on_move(QPointF(110, 140))
        service.finished.emit(pending, [row("编辑中的迟到结果", 10, 20)], "")
        assert fresh._ocr.document is None and fresh._text_layer is None
        assert fresh._ocr.state == "idle"
        fresh.close()
        pin._shutting_down = True
        target._allow_close = True
        target._close_ready = True
        target.close()
        flush()
        pin._open_pins.clear()
        pin._shutting_down = False
        if pin._save_timer is not None:
            pin._save_timer.stop()
    print("PASS TRANSFER: actual annotation/pin callbacks, source deletion, pixel invalidation, late discard, zero inference")


if __name__ == "__main__":
    data_contract()
    engine_geometry()
    annotation_transfer()
    print("RESULT: 3/3 groups PASS")
