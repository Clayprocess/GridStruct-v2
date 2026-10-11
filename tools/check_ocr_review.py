"""OCR 复核联动及明暗主题离屏验收；不使用用户截图。"""

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import QApplication, QGraphicsScene
from PySide6.QtTest import QTest
from styles import apply_theme, tokens as T
from ocr import OCRDocument, OCRResult, OCRResultDialog, add_text_layer


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def check_pending_and_focus(app):
    image = QImage(400, 300, QImage.Format_ARGB32)
    image.fill(QColor(T.LIGHT["background"]))
    records = [OCRResult(QRect(10, 10, 100, 20), "line one", .9),
               OCRResult(QRect(10, 34, 100, 20), "line two", .9),
               OCRResult(QRect(10, 200, 100, 20), "other block", .9)]
    document = OCRDocument(records, image)
    first = OCRResultDialog(document)
    second = OCRResultDialog(document)
    first.show()
    second.show()
    flush(app)
    identifier = records[0].item_id
    QTest.mouseClick(first._items.viewport(), Qt.LeftButton,
                     pos=first._items.visualItemRect(first._items.item(0)).center())
    assert document.focused_ids == (identifier,)
    assert sum(item._focused for item in first._layer.childItems()) == 1
    first._editor.setPlainText("pending local correction")
    second._text.setPlainText("new independent draft")
    assert first._editor.toPlainText() == "pending local correction"
    document.correct((records[2].item_id,), "other edited")
    assert first._editor.toPlainText() == "pending local correction"
    assert "其他位置修改" not in first._metadata.text()
    document.correct((identifier,), "external same item correction")
    assert first._editor.toPlainText() == "pending local correction"
    assert "其他位置修改" in first._metadata.text()
    output = Path(__file__).resolve().parents[1] / ".scratch" / "ocr-review-20261011"
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        flush(app)
        assert first.grab().save(str(output / f"pending-conflict-{mode}.png"))
    # 原图定位同一条与切换其他条目不能吞掉尚未应用的输入。
    document.focus((identifier,))
    assert first._editor.toPlainText() == "pending local correction"
    document.focus((records[2].item_id,))
    document.focus((identifier,))
    assert first._editor.toPlainText() == "pending local correction"
    first._unit.setCurrentIndex(1)
    assert document.focused_ids == first._ids() == document.groups()[0]
    assert sum(item._focused for item in first._layer.childItems()) == 2
    first._unit.setCurrentIndex(0)
    assert first._editor.toPlainText() == "pending local correction"
    assert document.focused_ids == first._ids() == (identifier,)
    first._apply_correction()
    assert first._editor.toPlainText() == "pending local correction"
    assert (identifier,) not in first._pending
    assert "有待应用校正" not in first._metadata.text()
    document.correct((identifier,), "applied value changed externally")
    assert first._editor.toPlainText() == "applied value changed externally"
    paragraph = document.groups()[0]
    first._unit.setCurrentIndex(1)
    first._editor.setPlainText("merged paragraph")
    first._apply_correction()
    first._unit.setCurrentIndex(0)
    assert document.focused_ids == first._ids() == paragraph
    first._reset_selected()
    assert document.focused_ids == first._ids() == (identifier,)
    assert sum(item._focused for item in first._layer.childItems()) == 1
    assert second._ids() == (identifier,)
    assert first._editor.toPlainText() == "line one"
    first.deleteLater()
    second.deleteLater()
    flush(app)


def main():
    app = QApplication.instance() or QApplication([])
    for filename in ("arial.ttf", "msyh.ttc"):
        QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    image = QImage(900, 560, QImage.Format_ARGB32)
    image.fill(QColor(T.LIGHT["background"]))
    image.setDevicePixelRatio(1.5)
    painter = QPainter(image)
    painter.setPen(QColor(T.LIGHT["foreground"]))
    painter.drawText(45, 65, "Invoice 2026 / Local OCR review")
    painter.drawText(45, 115, "Amount: 120.00")
    painter.drawText(45, 160, "Tax: 6.00")
    painter.end()
    records = [
        OCRResult(QRect(30, 25, 290, 24), "Invoice 2026", .98),
        OCRResult(QRect(30, 65, 180, 24), "Amount: 12O.OO", .65),
        OCRResult(QRect(30, 92, 180, 24), "Tax: 6.00", .99),
        OCRResult(QRect(330, 180, 120, 80), "Rotated", .9,
                  polygon=((500, 310), (650, 270), (670, 310), (520, 350))),
    ]
    document = OCRDocument(records, image)
    first = OCRResultDialog(document)
    second = OCRResultDialog(document)
    first.show()
    second.show()
    flush(app)
    identifier = records[1].item_id
    document.focus((identifier,))
    assert first._ids() == second._ids() == (identifier,)
    first._editor.setPlainText("Amount: 120.00")
    first._apply_correction()
    assert second._editor.toPlainText() == "Amount: 120.00"
    assert document.raw_results[1].text == "Amount: 12O.OO"
    item = next(item for item in first._layer.childItems() if identifier in item._ids)
    assert item.toPlainText() == "Amount: 120.00"
    cursor = item.textCursor()
    cursor.select(QTextCursor.Document)
    item.setTextCursor(cursor)
    item._emit_selection()
    first._copy_selected()
    assert QApplication.clipboard().text() == "Amount: 120.00"
    first._copy_all()
    assert QApplication.clipboard().text() == document.text()
    first._text.setPlainText("Export draft: freely merged content")
    assert document.draft == "Export draft: freely merged content"
    assert second._text.toPlainText() == document.draft
    document.correct((identifier,), "Amount: 121.00")
    assert document.draft_stale and first._text.toPlainText() == document.draft
    first._copy_draft()
    assert QApplication.clipboard().text() == document.draft
    first._copy_all()
    assert "121.00" in QApplication.clipboard().text()
    assert QApplication.clipboard().text() != document.draft
    document.relayout()
    assert "121.00" in document.text()
    assert any(item._focused for item in first._layer.childItems() if identifier in item._ids)
    rotated = next(item for item in first._layer.childItems() if records[3].item_id in item._ids)
    mapped = rotated.mapToScene(rotated.boundingRect().topLeft())
    assert abs(mapped.x() - 500 / 1.5) < .01 and abs(mapped.y() - 310 / 1.5) < .01
    assert abs(rotated.transform().m12()) > .01
    paragraph = next(group for group in document.groups() if identifier in group)
    assert len(paragraph) >= 2
    first._unit.setCurrentIndex(1)
    document.focus(paragraph)
    first._editor.setPlainText("Amount and tax checked\nTotal: 127.00")
    first._apply_correction()
    merged = [item for item in document.results if item.source_ids == paragraph]
    assert len(merged) == 1 and merged[0].text == "Amount and tax checked\nTotal: 127.00"
    assert len([item for item in first._layer.childItems() if set(paragraph).intersection(item._ids)]) == 1
    assert not any(item.toPlainText() == "Tax: 6.00" for item in first._layer.childItems())
    # 选字、定位和校正不会污染原始像素或 scene.render 的原图导出。
    rendered = QImage(image.size(), QImage.Format_ARGB32)
    rendered.setDevicePixelRatio(image.devicePixelRatio())
    rendered.fill(Qt.transparent)
    painter = QPainter(rendered)
    first._preview.scene().render(painter, first._preview.sceneRect(), first._preview.sceneRect())
    painter.end()
    assert rendered == image
    output = Path(__file__).resolve().parents[1] / ".scratch" / "ocr-review-20261011"
    output.mkdir(parents=True, exist_ok=True)
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        dialog = OCRResultDialog(document)
        dialog.show()
        flush(app)
        assert dialog.grab().save(str(output / f"review-{mode}.png"))
        dialog._tabs.setCurrentIndex(1)
        assert dialog.grab().save(str(output / f"draft-{mode}.png"))
        dialog.close()
        dialog.deleteLater()
        flush(app)
    scene = QGraphicsScene()
    layer = add_text_layer(scene, document)
    scene.removeItem(layer)
    layer.deleteLater()
    first.close()
    first.deleteLater()
    flush(app)
    document.correct((records[0].item_id,), "Invoice corrected after source closed")
    assert "Invoice corrected after source closed" in second._document.text()
    assert second._items.item(0).text().find("Invoice corrected") >= 0
    second.close()
    second.deleteLater()
    flush(app)
    document.correct((records[0].item_id,), "Document remains valid")
    legacy = OCRResultDialog([OCRResult(QRect(0, 0, 50, 20), "legacy", .9)])
    assert not legacy._show_image.isEnabled()
    legacy._copy_all()
    assert QApplication.clipboard().text() == "legacy"
    legacy.deleteLater()
    empty = OCRResultDialog([])
    assert not empty._apply.isEnabled()
    empty._copy_all()
    assert QApplication.clipboard().text() == ""
    empty.deleteLater()
    flush(app)
    check_pending_and_focus(app)
    print("PASS: shared corrections, draft separation, paragraph geometry, rotated polygon, lifecycle, legacy/empty")
    print("PASS: pending edits survive shared changes, explicit conflict notice, first-row click focus, paragraph focus after reset")
    print(f"Review screenshots: {output}")


if __name__ == "__main__":
    main()
