"""真实本地模型通过串行服务的集成验收；不读取桌面、不访问网络。"""

import json
import os
from pathlib import Path
import sys
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from ocr import OCRController, OCRDocument, OCREngine
from ocr.controller import _get_service
from styles import tokens as T


def main():
    app = QApplication([])
    QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / "arial.ttf"))
    owner = QWidget()
    controller = OCRController(owner, layer_first=True)
    pixmap = QPixmap(601, 180)
    pixmap.fill(QColor(T.LIGHT["background"]))
    with QPainter(pixmap) as painter:
        painter.setPen(QColor(T.LIGHT["foreground"]))
        painter.setFont(QFont("Arial", 26))
        painter.drawText(20, 60, "GridStruct 123")
        painter.drawText(20, 120, "Hello World")
    output = ROOT / ".scratch" / "ocr-stages-bcd" / "local"
    output.mkdir(parents=True, exist_ok=True)
    phases, runs = [], []
    controller.stateChanged.connect(lambda state: phases.append((state, monotonic())))
    assert OCREngine()._engine is None
    service = _get_service()
    try:
        settings = SimpleNamespace(value=lambda key, default=False, **kwargs: default)
        with patch("requests.sessions.Session.request", side_effect=AssertionError("network attempted")), \
             patch("settings.get_settings", return_value=settings):
            for expected in (["queued", "loading", "recognizing", "completed"],
                             ["queued", "recognizing", "completed"]):
                phases.clear()
                start = monotonic()
                controller.start(pixmap)
                while controller.running and monotonic() - start < 65:
                    QTest.qWait(5)
                assert [phase for phase, _ in phases] == expected, phases
                document = controller.document
                assert document.text() == "GridStruct 123\nHello World", document.text()
                assert all(len(item.polygon) == 4 for item in document.raw_results)
                identifier = document.results[0].item_id
                document.correct((identifier,), "GridStruct corrected")
                restored = OCRDocument.from_payload(document.to_payload(), pixmap.toImage())
                assert restored.text() == document.text()
                timings = {phase: round((stamp - start) * 1000, 3) for phase, stamp in phases}
                runs.append(timings)
                print("PASS local OCR", timings, flush=True)
            phases.clear()
            start = monotonic()
            controller.start(pixmap)
            while controller.state != "recognizing" and monotonic() - start < 60:
                QTest.qWait(5)
            assert controller.state == "recognizing"
            start = monotonic()
            controller.cancel()
            cancel_ms = round((monotonic() - start) * 1000, 3)
            assert not controller.running and controller.state == "cancelled"
            start = monotonic()
            service.shutdown()
            running_shutdown_ms = round((monotonic() - start) * 1000, 3)
            app.processEvents()
            assert controller.document is None
    finally:
        start = monotonic()
        service.shutdown()
        shutdown_ms = round((monotonic() - start) * 1000, 3)
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    (output / "results.json").write_text(json.dumps({"runs_ms": runs, "idle_shutdown_ms": shutdown_ms,
                                                   "running_cancel_ms": cancel_ms,
                                                   "running_shutdown_ms": running_shutdown_ms,
                                                   "network_attempts": 0}, indent=2), encoding="utf-8")
    print(f"PASS: offline real worker; polygons/correction; cancel {cancel_ms}ms; running shutdown {running_shutdown_ms}ms; idle shutdown {shutdown_ms}ms")


if __name__ == "__main__":
    main()
