"""文字识别窗口：一份全文、排版切换、全文与原图双向定位、低置信度标记，及明暗主题离屏截图；不使用用户截图。"""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from styles import apply_theme, tokens as T
from ocr import OCRDocument, OCRResult, OCRResultDialog
from ocr.dialog import find_spans, span_at

OUTPUT = Path(__file__).resolve().parents[1] / ".scratch" / "ocr-review"


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def span_of(dialog, identifier):
    return next(span for span in dialog._spans if span[2].item_id == identifier)


def check_spans():
    records = [OCRResult(QRect(0, 0, 10, 10), "X", .9), OCRResult(QRect(0, 20, 10, 10), "Excel 文件", .9),
               OCRResult(QRect(0, 40, 10, 10), "𠀀 生僻", .5)]
    original = "X\nExcel 文件\n𠀀 生僻"
    spans = find_spans("Excel 文件\n𠀀 生僻", original, records)
    # “X” 被删了：后面 “Excel” 里的 X 不算它；生僻字按 UTF-16 占两位。
    assert [(start, end, item.text) for start, end, item in spans] == [(0, 8, "Excel 文件"), (9, 14, "𠀀 生僻")], spans
    # 两处相同的字：改了第一处，第二处仍归第二条，不被改过的那条占走。
    same = [OCRResult(QRect(0, 0, 10, 10), "100", .4), OCRResult(QRect(20, 0, 10, 10), "100", .99)]
    spans = find_spans("200 100", "100 100", same)
    assert [(start, end, item.score) for start, end, item in spans] == [(4, 7, .99)], spans
    # 中文合行时前一条终点就是下一条起点：光标在起点归下一条，在末尾归最后一条。
    lines = [OCRResult(QRect(10, 10, 90, 20), "第一行", .99), OCRResult(QRect(10, 35, 90, 20), "第二行", .4)]
    spans = find_spans("第一行第二行", "第一行第二行", lines)
    assert span_at(spans, 3)[2].text == "第二行" and span_at(spans, 0)[2].text == "第一行"
    assert span_at(spans, 6)[2].text == "第二行" and span_at(spans, 7) is None


def main():
    app = QApplication.instance() or QApplication([])
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("arial.ttf", "msyh.ttc"):
        QFontDatabase.addApplicationFont(str(fonts / filename))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    check_spans()
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
    identifier = records[1].item_id
    with TemporaryDirectory() as folder:
        settings = QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
        with patch("settings.get_settings", return_value=settings):
            document = OCRDocument(records, image)
            first, second = OCRResultDialog(document), OCRResultDialog(document)
            first.show()
            second.show()
            flush(app)
            # 默认“段落”：一段的几行合成一行，英文之间补空格；没写满的一行（Amount 那行）后面分段。
            assert document.layout == "paragraph" and first._layout_buttons["paragraph"].isChecked()
            assert first._text.toPlainText() == document.text()
            assert document.text().startswith("Invoice 2026 Amount: 12O.OO\nTax: 6.00"), document.text()
            assert len(first._spans) == 4
            # 低置信度：波浪线标记 + 计数，F8 跳过去并在原图高亮那一行。
            assert first._pending.text() == "低置信度 1 处" and first._pending.isVisible()
            assert any(selection.format.underlineStyle() == QTextCharFormat.WaveUnderline
                       for selection in first._text.extraSelections())
            first._next_pending()
            start, end, _item = span_of(first, identifier)
            assert first._text.textCursor().position() == start
            assert document.focused_ids == (identifier,)
            assert [item._focused for item in first._layer.childItems()] == [False, True, False, False]
            # 另一个窗口跟着定位；“点原图上的字”同样让光标跳到全文里对应位置。
            assert second._text.textCursor().position() == start
            document.focus((records[2].item_id,))
            assert first._text.textCursor().position() == span_of(first, records[2].item_id)[0]
            # 在全文里移动光标，原图跟着高亮。
            cursor = first._text.textCursor()
            cursor.setPosition(start + 2)
            first._text.setTextCursor(cursor)
            assert document.focused_ids == (identifier,)
            # 改字：只改全文，原图文字层与识别结果不变；改过的那条不再标低置信度。
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            first._text.setTextCursor(cursor)
            first._text.setFocus()
            QTest.keyClicks(first._text, "Amount: 120.00")
            assert "Amount: 120.00" in document.draft and second._text.toPlainText() == document.draft
            assert document.results[1].text == "Amount: 12O.OO"
            assert next(item for item in first._layer.childItems() if identifier in item._ids).toPlainText() == "Amount: 12O.OO"
            assert first._pending_spans() == [] and not first._pending.isVisible()
            assert first._status.text().startswith("已修改")
            assert len(first._spans) == 3
            first._copy_all()
            assert QApplication.clipboard().text() == document.draft
            first._no_spaces.setChecked(True)
            first._copy_all()
            assert " " not in QApplication.clipboard().text()
            first._no_spaces.setChecked(False)
            # 改过再换排版要确认：取消则什么都不变，确认则按识别结果重排、记住选择。
            with patch("ocr.dialog.ask_confirmation", return_value=False) as asked:
                first._layout_buttons["lines"].setChecked(True)
                assert asked.called and first._layout_buttons["paragraph"].isChecked()
                assert document.layout == "paragraph" and "Amount: 120.00" in document.text()
            with patch("ocr.dialog.ask_confirmation", return_value=True):
                first._layout_buttons["lines"].setChecked(True)
            assert document.layout == "lines" and document.draft is None
            assert second._layout_buttons["lines"].isChecked() and second._text.toPlainText() == document.text()
            assert "Invoice 2026\nAmount: 12O.OO\nTax: 6.00" in document.text()
            assert settings.value("ocr/layout") == "lines"
            # 没改过时换排版不用确认。
            with patch("ocr.dialog.ask_confirmation", side_effect=AssertionError("asked without edits")):
                first._layout_buttons["single"].setChecked(True)
                assert "\n" not in document.text()
                first._layout_buttons["paragraph"].setChecked(True)
            with patch("table.open_table") as open_table:
                first._open_table()
                assert open_table.call_args.args[1][1].text == "Amount: 12O.OO"
            rotated = next(item for item in first._layer.childItems() if records[3].item_id in item._ids)
            mapped = rotated.mapToScene(rotated.boundingRect().topLeft())
            assert abs(mapped.x() - 500 / 1.5) < .01 and abs(mapped.y() - 310 / 1.5) < .01
            assert abs(rotated.transform().m12()) > .01
            # 选字、定位不会污染原始像素或 scene.render 的原图导出。
            rendered = QImage(image.size(), QImage.Format_ARGB32)
            rendered.setDevicePixelRatio(image.devicePixelRatio())
            rendered.fill(Qt.transparent)
            painter = QPainter(rendered)
            first._preview.scene().render(painter, first._preview.sceneRect(), first._preview.sceneRect())
            painter.end()
            assert rendered == image
            for dialog in (first, second):
                dialog.close()
                dialog.deleteLater()
            flush(app)
            # 改过的全文跟着文档走：重新打开仍是改过的，排版保持改的时候那种。
            document.set_layout("lines")
            document.set_draft(document.text().replace("12O.OO", "120.00"))
            settings.setValue("ocr/layout", "single")
            reopened = OCRResultDialog(document)
            assert reopened._layout_buttons["lines"].isChecked() and "120.00" in reopened._text.toPlainText()
            reopened.deleteLater()
            OUTPUT.mkdir(parents=True, exist_ok=True)
            for mode in ("light", "dark"):
                apply_theme(app, mode)
                app.setProperty("theme_mode", mode)
                shown = OCRDocument(records, image)
                dialog = OCRResultDialog(shown)
                dialog.show()
                flush(app)
                dialog._next_pending()
                flush(app)
                assert dialog.grab().save(str(OUTPUT / f"review-{mode}.png"))
                dialog.close()
                dialog.deleteLater()
                flush(app)
            adjacent = OCRDocument([OCRResult(QRect(10, 10, 90, 20), "第一行", .99),
                                    OCRResult(QRect(10, 35, 90, 20), "第二行", .4)], image)
            dialog = OCRResultDialog(adjacent)
            assert dialog._text.toPlainText() == "第一行第二行"
            dialog._next_pending()
            assert adjacent.focused_ids == (adjacent.results[1].item_id,)
            dialog.deleteLater()
            legacy = OCRResultDialog([OCRResult(QRect(0, 0, 50, 20), "legacy", .9)])
            assert legacy._preview.isHidden() and not legacy._to_table.isEnabled()
            legacy._copy_all()
            assert QApplication.clipboard().text() == "legacy"
            legacy.deleteLater()
            empty = OCRResultDialog([])
            assert empty._text.toPlainText() == "" and empty._status.text() == "未检测到文字"
            empty._copy_all()
            assert QApplication.clipboard().text() == ""
            empty.deleteLater()
            flush(app)
    print("PASS: one editable full text, layouts (paragraph/lines/single), confirm before re-layout drops edits")
    print("PASS: text <-> image locate both ways, low-confidence wave marks + F8, edits never touch image layer")
    print("PASS: rotated polygon, clean scene render, reopen keeps edits, legacy/empty")
    print(f"Review screenshots: {OUTPUT}")


if __name__ == "__main__":
    main()
