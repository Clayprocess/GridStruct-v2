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
    assert [item.item_id for item in document] == identifiers
    assert len(document.raw_results) == 5 and document.raw_results[-1].score == .4
    document.raw_results[0].box.setX(555)
    document.results[-1].box.translate(33, 0)
    records[0].box.setX(666)
    assert document.raw_results[0].box.x() == 220
    source.setPixelColor(2, 2, QColor(T.LIGHT["foreground"]))
    assert not document.matches(source)
    assert document.matches(document.image)
    # 排版：段落合行（中文不补空格）、原样换行、一行。
    assert document.layout == "paragraph" and document.text() == "标题\n左上左下\n右上右下"
    document.set_layout("lines")
    assert document.text() == "标题\n\n左上\n左下\n\n右上\n右下"
    document.set_layout("single")
    assert document.text() == "标题左上左下右上右下"
    # 正文合行：英文、数字之间补空格，碰到中文不补，行尾连字符直接接上。
    english = OCRDocument([row("on November 3,", 10, 10), row("2026. Co-", 10, 35), row("operate 合作", 10, 60),
                           row("说明", 10, 85)], image())
    assert english.text() == "on November 3, 2026. Co-operate 合作说明", english.text()
    # 账号、金额、单据号断行直接接上，不补空格；英文单词之间照常补。
    from ocr.layout import join_prose
    cases = {("账号 6222 0212 3456", "7890 开户行"): "账号 6222 0212 34567890 开户行",
             ("Account No. 62220212345678", "90123"): "Account No. 6222021234567890123",
             ("Ref AB12CD", "34EF done"): "Ref AB12CD34EF done", ("in 2026", "the app"): "in 2026 the app",
             ("courier collection point and the", "return address"): "courier collection point and the return address"}
    for lines, expected in cases.items():
        assert join_prose(lines) == expected, (lines, join_prose(lines))
    # 人工编辑只有一份全文；改回排版结果等于没改，换排版丢掉编辑。
    document.set_layout("lines")
    document.set_draft("独立草稿")
    assert document.draft == "独立草稿" and document.text() == "独立草稿"
    assert [item.text for item in document] == ["标题", "左上", "左下", "右上", "右下"]
    encoded = json.loads(json.dumps(document.to_payload(), ensure_ascii=False))
    restored = OCRDocument.from_payload(encoded, document.image)
    assert restored.to_payload() == encoded and restored.text() == "独立草稿" and restored.layout == "lines"
    document.set_draft("标题\n\n左上\n左下\n\n右上\n右下")
    assert document.draft is None
    document.set_draft("再改")
    document.set_layout("paragraph")
    assert document.draft is None and document.text() == "标题\n左上左下\n右上右下"
    # 第 1 版贴图：逐行校正并进全文，旧草稿原样保留。
    legacy = {key: value for key, value in encoded.items() if key not in ("mode", "draft")}
    legacy.update(version=1, draft=None, revision=1, draft_revision=0,
                  corrections={identifiers[1]: "左上校正"}, paragraphs=[])
    migrated = OCRDocument.from_payload(legacy, document.image)
    assert migrated.text() == "标题\n\n左上校正\n左下\n\n右上\n右下" and migrated.layout == "lines"
    legacy.update(corrections={}, paragraphs=[{"ids": list(document.results[i].item_id for i in (1, 2)), "text": "整段改写"}])
    assert OCRDocument.from_payload(legacy, document.image).text() == "标题\n\n整段改写\n\n右上\n右下"
    legacy.update(paragraphs=[], draft="旧草稿")
    assert OCRDocument.from_payload(legacy, document.image).text() == "旧草稿"
    # 全文改完以后又逐行改过（旧版 draft_stale）：两份都留下，较新的逐行修改在上面。
    legacy.update(corrections={identifiers[1]: "左上校正"}, revision=2, draft_revision=1)
    both = OCRDocument.from_payload(legacy, document.image).text()
    assert both.startswith("标题\n\n左上校正\n左下") and both.endswith("——\n\n旧草稿"), both
    # 全文在逐行校正之后改的，已经包含了校正。
    legacy.update(revision=2, draft_revision=2)
    assert OCRDocument.from_payload(legacy, document.image).text() == "旧草稿"
    legacy.update(revision=1, draft_revision=2)
    try:
        OCRDocument.from_payload(legacy, document.image)
    except ValueError:
        pass
    else:
        raise AssertionError("bad v1 revision accepted")
    legacy.update(corrections={}, revision=1, draft_revision=0)
    legacy.update(draft=None)
    untouched = OCRDocument.from_payload(legacy, document.image)
    assert untouched.draft is None and untouched.layout == "paragraph"
    for mutate in (lambda p: p.update(version=True),
                   lambda p: p["layout"][0].update(id=[]),
                   lambda p: p["raw"][0].update(score=float("nan")),
                   lambda p: p["raw"][0].update(score=10**400),
                   lambda p: p["raw"][0].update(polygon=[[10**400, 0]] * 4),
                   lambda p: p["raw"][0].update(polygon=[[0, 0]]),
                   lambda p: p["layout"].pop(),
                   lambda p: p.update(mode="columns"),
                   lambda p: p.update(draft=3)):
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
    print("PASS DATA: raw evidence, columns/title, stable IDs, layouts, single draft, v1 migration, validation, DPR PNG")


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
    settings = SimpleNamespace(value=lambda key, default=False, **kwargs: default, setValue=lambda key, value: None)
    with TemporaryDirectory(dir=evidence) as folder, \
         patch("pin.data_directory", return_value=Path(folder)), \
         patch("ocr.controller._get_service", return_value=service), \
         patch("settings.get_settings", return_value=settings), \
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
        document.set_draft("已校正")
        assert window._render().toImage().convertToFormat(QImage.Format_RGBA8888) == pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        window.trigger_action("pin")
        flush()
        target = pin._open_pins[-1]
        assert target._document is document and target._document.text() == "已校正"
        target._document.set_draft("来源关闭后校正")
        target._recognize()
        assert target._ocr_controller._dialogs[-1]._document is document
        fresh = AnnotationWindow(pixmap, QPoint())
        fresh._ocr.adopt_document(document, pixmap)
        fresh.select_tool("rect")
        tool = fresh._canvas._tool
        tool.on_press(fresh._canvas.scene(), QPointF(10, 80), fresh._canvas._undo)
        tool.on_move(QPointF(100, 130))
        tool.on_release(QPointF(100, 130), fresh._canvas._undo)
        # 改过的全文标注后仍留着可复制，只是不再和截图定位（Clay 2026-11-05 定）；重新识别前先确认。
        assert fresh._ocr.document is document and fresh._text_layer is None
        assert target._document is document
        with patch("annotate.window.ask_confirmation", return_value=True) as asked:
            fresh.trigger_action("ocr")
            assert asked.called
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
