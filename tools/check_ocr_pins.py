"""离屏验证 OCR 贴图共享、校正保存恢复以及损坏数据保护。"""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QCoreApplication, QEvent, QRect, QSaveFile
from PySide6.QtGui import QFont, QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import pin
from ocr import OCRController, OCRDocument, OCRResult
from styles import apply_theme, tokens as T


def flush(app, milliseconds=0):
    if milliseconds:
        QTest.qWait(milliseconds)
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def clear_pins(app):
    pin._shutting_down = True
    if pin._save_timer is not None:
        pin._save_timer.stop()
    for window in pin._open_pins:
        window._allow_close = True
        window.close()
    flush(app, 180)
    pin._open_pins.clear()
    pin._shutting_down = False
    pin._restore_failed = False


def sample(mode):
    image = QImage(640, 360, QImage.Format_RGBA8888)
    image.fill(T.THEMES[mode]["background"])
    painter = QPainter(image)
    painter.setPen(T.THEMES[mode]["foreground"])
    painter.drawText(30, 50, "GridStruct OCR evidence")
    painter.drawText(30, 100, "Offline review and export")
    painter.end()
    image.setDevicePixelRatio(1.5)
    results = [OCRResult(QRect(20, 15, 260, 30), "GridStruct OCR evidencc", .93),
               OCRResult(QRect(20, 50, 260, 30), "Offline review and export", .98)]
    return QPixmap.fromImage(image), results


def main():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt":
        # Windows 沙箱离屏平台不枚举系统字体，显式载入保证截图文字可读。
        font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        assert font_id >= 0
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0]))
    evidence = Path(__file__).resolve().parents[1] / ".scratch" / "ocr-workflow-20261011"
    evidence.mkdir(parents=True, exist_ok=True)
    checks = []
    with TemporaryDirectory(dir=evidence) as temporary:
        directory = Path(temporary)
        session_path = directory / "pins" / "session.json"
        with (patch("pin.data_directory", return_value=directory),
              patch("pin.show_message"),
              patch("pin.ask_confirmation", return_value=False) as warning,
              patch("ocr.OCREngine.recognize_image", side_effect=AssertionError("转移和恢复不应推理")) as infer):
            for mode in ("light", "dark"):
                apply_theme(app, mode)
                pixmap, results = sample(mode)
                document = OCRDocument(results, pixmap.toImage())
                source = QObject()
                source_controller = OCRController(source, layer_first=True)
                assert source_controller.adopt_document(document, pixmap)
                image_pin = pin.show_pin(pixmap, document=document)
                text_pin = pin.show_text_pin("旧文字贴图仍可恢复")
                assert image_pin._document is document
                assert image_pin._ocr_controller.document is document
                document.correct((results[0].item_id,), "GridStruct OCR evidence")
                document.set_draft("独立导出草稿")
                document.correct((results[1].item_id,), "离线复核与导出")
                image_pin.zoom(1.5)
                assert image_pin._content.transform().m11() == 1.5
                flush(app, 350)
                saved = json.loads(session_path.read_text(encoding="utf-8"))
                saved_image = next(state for state in saved["pins"] if state["kind"] == "image")
                restored_document = OCRDocument.from_payload(saved_image["ocr"], pixmap.toImage())
                assert restored_document.text() == document.text()
                assert restored_document.draft == "独立导出草稿" and restored_document.draft_stale
                for item in image_pin._text_layer.childItems():
                    result = next(result for result in document.results if result.item_id == item._ids[0])
                    assert item.toPlainText() == result.text
                    bounds = item.sceneBoundingRect()
                    assert abs(bounds.left() - result.box.left()) < .01
                    assert abs(bounds.top() - result.box.top()) < .01
                    assert abs(bounds.width() - result.box.width()) < .01
                    assert abs(bounds.height() - result.box.height()) < .01
                    point = image_pin._content.mapFromScene(bounds.center())
                    assert image_pin._content.itemAt(point) is item
                source.deleteLater()
                flush(app)
                document.correct((results[1].item_id,), "来源关闭后继续校正")
                assert "来源关闭后继续校正" in image_pin._document.text()
                image_pin._recognize()
                flush(app)
                assert image_pin._ocr_controller._dialogs[-1]._document is document
                assert infer.call_count == 0
                assert image_pin.grab().save(str(evidence / f"pin-ocr-{mode}.png"))
                assert pin.shutdown_pins()
                clear_pins(app)
                assert pin.restore_pins()
                flush(app)
                restored_pin = next(window for window in pin._open_pins if window.kind == "image")
                assert restored_pin._document.text() == document.text()
                assert restored_pin._document.draft == document.draft
                assert restored_pin._document.draft_stale == document.draft_stale
                assert restored_pin._base.toImage() == pixmap.toImage()
                assert restored_pin._scale == 1.5
                assert infer.call_count == 0
                clear_pins(app)
                checks.append(f"{mode}: same-document reuse, correction debounce, source-close review, restart restore, zero inference")

            assert pin.restore_pins()
            flush(app)
            baseline = session_path.read_bytes()
            real_save_file = QSaveFile

            class InterruptedSaveFile(real_save_file):
                def commit(self):
                    if self.fileName().endswith("session.json"):
                        self.cancelWriting()
                        return False
                    return super().commit()

            with patch("pin.QSaveFile", InterruptedSaveFile):
                assert not pin.save_pins()
            assert session_path.read_bytes() == baseline
            checks.append("interrupted atomic write preserves prior JSON")
            clear_pins(app)

            saved = json.loads(baseline)
            for state in saved["pins"]:
                state.pop("ocr", None)
            session_path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            assert pin.restore_pins()
            assert {window.kind for window in pin._open_pins} == {"image", "text"}
            assert next(window for window in pin._open_pins if window.kind == "image")._document is None
            checks.append("legacy version-1 image/text session restores without OCR")
            clear_pins(app)

            for defect in ("version", "image_key", "layout_id"):
                damaged = json.loads(baseline)
                payload = next(state for state in damaged["pins"] if state["kind"] == "image")["ocr"]
                if defect == "layout_id":
                    payload["layout"][0]["id"] = []
                else:
                    payload[defect] = 99 if defect == "version" else "different-image"
                session_path.write_text(json.dumps(damaged, ensure_ascii=False), encoding="utf-8")
                before = session_path.read_bytes()
                warning.reset_mock()
                assert not pin.restore_pins()
                assert warning.called and pin._restore_failed
                assert {window.kind for window in pin._open_pins} == {"image", "text"}
                recovered_pin = next(window for window in pin._open_pins if window.kind == "image")
                assert recovered_pin._document is None and "OCR 数据未恢复" in recovered_pin._ocr_status
                recovered_pin.zoom(1.1)
                flush(app, 350)
                assert not pin.save_pins() and session_path.read_bytes() == before
                checks.append(f"damaged {defect}: picture retained, clear notice, original correction payload protected")
                clear_pins(app)

            pixmap, results = sample("light")
            document = OCRDocument(results, pixmap.toImage())
            changed = pixmap.toImage()
            changed.setPixelColor(0, 0, T.THEMES["light"]["foreground"])
            mismatched_pin = pin.show_pin(QPixmap.fromImage(changed), document=document)
            assert mismatched_pin._document is None and "未复用" in mismatched_pin._ocr_status
            assert infer.call_count == 0
            checks.append("different pixels invalidate supplied OCR without inference or misaligned layer")
            clear_pins(app)

    reference = {**saved_image, "id": "a" * 32}
    missing_id = {key: value for key, value in reference.items() if key != "id"}
    backups = (
        ("no_backup", None, False),
        ("valid_backup", {"version": 1, "pins": [reference]}, False),
        ("invalid_json", "{", True),
        ("invalid_top_level", [], True),
        ("unknown_version", {"version": 2, "pins": [reference]}, True),
        ("pins_mapping", {"version": 1, "pins": {reference["id"]: reference}}, True),
        ("pins_string", {"version": 1, "pins": reference["id"]}, True),
        ("pins_null", {"version": 1, "pins": None}, True),
        ("invalid_entry", {"version": 1, "pins": [None]}, True),
        ("missing_id", {"version": 1, "pins": [missing_id]}, True),
        ("invalid_state", {"version": 1, "pins": [{**reference, "opacity": "bad"}]}, True),
    )
    for name, payload, preserve_all in backups:
        with TemporaryDirectory(dir=evidence) as temporary:
            directory = Path(temporary)
            images = directory / "pins"
            images.mkdir()
            referenced = images / f"{reference['id']}.png"
            orphan = images / f"{'b' * 32}.png"
            unrelated = images / "user-picture.png"
            for path in (referenced, orphan, unrelated):
                assert pixmap.save(str(path), "PNG")
            if name != "no_backup":
                backup = images / "session-backup-probe.json"
                backup.write_text(payload if name == "invalid_json" else json.dumps(payload), encoding="utf-8")
            with patch("pin.data_directory", return_value=directory), patch("pin.show_message") as message:
                current = pin.show_pin(pixmap)
                assert pin.save_pins(), name
                assert (images / f"{current.pin_id}.png").exists(), name
                assert referenced.exists() == (preserve_all or name == "valid_backup"), name
                assert orphan.exists() == preserve_all, name
                assert unrelated.exists(), name
                assert not message.called, name
                clear_pins(app)
            checks.append(f"image cleanup {name}: live pin and unrelated file kept, backup protection respected")

    # 从公开恢复入口走完“保留备份并继续”，后续自动保存仍须保住无法恢复的原图。
    with TemporaryDirectory(dir=evidence) as temporary:
        directory = Path(temporary)
        images = directory / "pins"
        images.mkdir()
        original_image = images / f"{reference['id']}.png"
        assert pixmap.save(str(original_image), "PNG")
        session_path = images / "session.json"
        damaged = json.dumps({"version": 1, "pins": {reference["id"]: reference}})
        session_path.write_text(damaged, encoding="utf-8")
        with (patch("pin.data_directory", return_value=directory), patch("pin.show_message"),
              patch("pin.ask_confirmation", return_value=True) as warning):
            assert not pin.restore_pins()
            assert warning.called and not pin._restore_failed
            copies = list(images.glob("session-backup-*.json"))
            assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == damaged
            assert pin.save_pins()
            assert original_image.exists() and copies[0].exists()
            assert json.loads(session_path.read_text(encoding="utf-8"))["pins"] == []
            clear_pins(app)
        checks.append("unsupported session: continue with backup then save retains original image")
    from ocr.controller import _service
    if _service is not None:
        _service.shutdown()
    for check in checks:
        print("PASS", check)
    print(f"OCR pin smoke: {len(checks)} passed; evidence: {evidence}")


if __name__ == "__main__":
    main()
