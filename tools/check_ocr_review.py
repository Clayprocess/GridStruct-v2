"""文字识别窗口：按行/按段改字联动原图、全文草稿分离，及明暗主题离屏截图；不使用用户截图。"""

import os
from pathlib import Path
import sys
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QTextCursor
from PySide6.QtWidgets import QAbstractItemView, QApplication, QGraphicsScene, QPlainTextEdit
from PySide6.QtTest import QTest
from styles import apply_theme, tokens as T
from ocr import OCRDocument, OCRResult, OCRResultDialog, add_text_layer

OUTPUT = Path(__file__).resolve().parents[1] / ".scratch" / "ocr-review"


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def _edit(dialog, row, text):
    """等同双击改字后按 Enter：委托写回条目文字。"""
    dialog._items.item(row).setText(text)


def _row_of(dialog, identifier):
    return next(row for row in range(dialog._items.count()) if identifier in dialog._items.item(row).data(Qt.UserRole))


def check_output_commits_editor(app):
    image = QImage(400, 300, QImage.Format_ARGB32)
    image.fill(QColor(T.LIGHT["background"]))
    for view in ("line", "paragraph"):
        document = OCRDocument([OCRResult(QRect(10, 10, 100, 20), "original", .5)], image)
        dialog = OCRResultDialog(document)
        dialog._view_buttons[view].setChecked(True)
        dialog.show()
        dialog.activateWindow()
        flush(app)
        assert dialog._pending.text() == "低置信度 1 处"
        assert "无需修改" in dialog._pending.toolTip()
        assert "低置信度" in dialog._next.toolTip()
        for action in ("copy", "table"):
            dialog._items.editItem(dialog._items.item(0))
            flush(app)
            editor = app.focusWidget()
            assert isinstance(editor, QPlainTextEdit)
            text = f"{view} corrected for {action}"
            QTest.keyClicks(editor, text)
            if action == "copy":
                QTest.keyClick(editor, Qt.Key_C, Qt.ControlModifier | Qt.ShiftModifier)
                assert app.clipboard().text() == text
            else:
                with patch("table.open_table") as open_table:
                    dialog._open_table()
                    assert open_table.call_args.args[1][0].text == text
            flush(app)
            assert document.text() == text
            assert dialog._items.state() != QAbstractItemView.EditingState
        dialog.close()
        dialog.deleteLater()
        flush(app)


def check_editing_survives_shared_changes(app):
    image = QImage(400, 300, QImage.Format_ARGB32)
    image.fill(QColor(T.LIGHT["background"]))
    records = [OCRResult(QRect(10, 10, 100, 20), "line one", .9),
               OCRResult(QRect(10, 34, 100, 20), "line two", .9),
               OCRResult(QRect(10, 200, 100, 20), "other block", .9)]
    document = OCRDocument(records, image)
    first, second = OCRResultDialog(document), OCRResultDialog(document)
    first._view_buttons["line"].setChecked(True)
    second._view_buttons["line"].setChecked(True)
    first.show()
    second.show()
    flush(app)
    QTest.mouseClick(first._items.viewport(), Qt.LeftButton,
                     pos=first._items.visualItemRect(first._items.item(0)).center())
    assert document.focused_ids == (records[0].item_id,)
    assert sum(item._focused for item in first._layer.childItems()) == 1
    # 正在改字时别处改了文档：输入框不被列表刷新关掉，关掉后再同步。
    first._items.editItem(first._items.item(0))
    flush(app)
    assert first._items.state() == QAbstractItemView.EditingState
    document.correct((records[2].item_id,), "other edited")
    assert first._items.state() == QAbstractItemView.EditingState and first._stale
    first._items.setFocus()
    first._items.closePersistentEditor(first._items.item(0))
    first._refresh_if_stale()
    assert not first._stale and "other edited" in [first._items.item(row).text() for row in range(first._items.count())]
    # 切到按段：定位跟着换成整段。
    first._view_buttons["paragraph"].setChecked(True)
    assert document.focused_ids == first._ids() == document.groups()[0]
    assert sum(item._focused for item in first._layer.childItems()) == 2
    _edit(first, 0, "merged paragraph")
    first._view_buttons["line"].setChecked(True)
    assert first._ids() == document.groups()[0]
    assert second._items.item(_row_of(second, records[0].item_id)).text() == "merged paragraph"
    # 右键“恢复识别原文”后整段拆回两行。
    document.reset_correction(document.groups()[0])
    assert first._items.item(0).text() == "line one"
    assert second._ids() == (records[0].item_id,)
    for dialog in (first, second):
        dialog.close()
        dialog.deleteLater()
    flush(app)


def main():
    app = QApplication.instance() or QApplication([])
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("arial.ttf", "msyh.ttc"):
        QFontDatabase.addApplicationFont(str(fonts / filename))
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
    first, second = OCRResultDialog(document), OCRResultDialog(document)
    first._view_buttons["line"].setChecked(True)
    second._view_buttons["line"].setChecked(True)
    first.show()
    second.show()
    flush(app)
    identifier = records[1].item_id
    row = _row_of(first, identifier)
    # 低置信度提示仅供参考，F8 跳过去。
    assert first._pending_rows() == [row] and first._pending.isVisible()
    first._items.setCurrentRow(0)
    first._next_pending()
    assert first._ids() == (identifier,) and document.focused_ids == (identifier,)
    assert second._ids() == (identifier,)
    _edit(first, row, "Amount: 120.00")
    assert second._items.item(_row_of(second, identifier)).text() == "Amount: 120.00"
    assert document.raw_results[1].text == "Amount: 12O.OO"
    assert first._pending_rows() == [] and not first._pending.isVisible()
    assert "原识别：Amount: 12O.OO" in first._items.item(row).toolTip()
    item = next(item for item in first._layer.childItems() if identifier in item._ids)
    assert item.toPlainText() == "Amount: 120.00"
    # 改回原文等于撤销人工修改。
    _edit(first, row, "Amount: 12O.OO")
    assert all(result.text == result.original_text for result in document.results)
    _edit(first, _row_of(first, identifier), "Amount: 120.00")
    # 原图滑选后 Ctrl+C 由文字层自己处理；这里只确认选区是校正后的文字。
    item = next(item for item in first._layer.childItems() if identifier in item._ids)
    cursor = item.textCursor()
    cursor.select(QTextCursor.Document)
    item.setTextCursor(cursor)
    assert item.textCursor().selectedText() == "Amount: 120.00"
    first._copy_all()
    assert QApplication.clipboard().text() == document.text()
    first._no_breaks.setChecked(True)
    first._copy_all()
    assert "\n" not in QApplication.clipboard().text()
    first._no_breaks.setChecked(False)
    # 全文是独立草稿：不回写原图；复制跟随当前视图。
    first._view_buttons["full"].setChecked(True)
    first._text.setPlainText("Export draft: freely merged content")
    assert document.draft == "Export draft: freely merged content"
    assert second._text.toPlainText() == document.draft
    document.correct((identifier,), "Amount: 121.00")
    assert document.draft_stale and first._text.toPlainText() == document.draft
    assert "全文未跟着变" in first._status.text()
    first._copy_all()
    assert QApplication.clipboard().text() == document.draft
    first._view_buttons["line"].setChecked(True)
    first._copy_all()
    assert "121.00" in QApplication.clipboard().text()
    document.relayout()
    assert "121.00" in document.text()
    rotated = next(item for item in first._layer.childItems() if records[3].item_id in item._ids)
    mapped = rotated.mapToScene(rotated.boundingRect().topLeft())
    assert abs(mapped.x() - 500 / 1.5) < .01 and abs(mapped.y() - 310 / 1.5) < .01
    assert abs(rotated.transform().m12()) > .01
    paragraph = next(group for group in document.groups() if identifier in group)
    assert len(paragraph) >= 2
    first._view_buttons["paragraph"].setChecked(True)
    document.focus(paragraph)
    _edit(first, first._items.currentRow(), "Amount and tax checked\nTotal: 127.00")
    merged = [item for item in document.results if item.source_ids == paragraph]
    assert len(merged) == 1 and merged[0].text == "Amount and tax checked\nTotal: 127.00"
    assert len([item for item in first._layer.childItems() if set(paragraph).intersection(item._ids)]) == 1
    # 选字、定位和校正不会污染原始像素或 scene.render 的原图导出。
    rendered = QImage(image.size(), QImage.Format_ARGB32)
    rendered.setDevicePixelRatio(image.devicePixelRatio())
    rendered.fill(Qt.transparent)
    painter = QPainter(rendered)
    first._preview.scene().render(painter, first._preview.sceneRect(), first._preview.sceneRect())
    painter.end()
    assert rendered == image
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        app.setProperty("theme_mode", mode)
        dialog = OCRResultDialog(document)
        dialog._view_buttons["line"].setChecked(True)
        dialog.show()
        flush(app)
        assert dialog.grab().save(str(OUTPUT / f"review-{mode}.png"))
        dialog._view_buttons["full"].setChecked(True)
        flush(app)
        assert dialog.grab().save(str(OUTPUT / f"full-{mode}.png"))
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
    second.close()
    second.deleteLater()
    flush(app)
    legacy = OCRResultDialog([OCRResult(QRect(0, 0, 50, 20), "legacy", .9)])
    assert legacy._preview.isHidden() and not legacy._to_table.isEnabled()
    legacy._copy_all()
    assert QApplication.clipboard().text() == "legacy"
    legacy.deleteLater()
    empty = OCRResultDialog([])
    assert empty._items.count() == 0 and empty._status.text() == "未检测到文字"
    empty._copy_all()
    assert QApplication.clipboard().text() == ""
    empty.deleteLater()
    flush(app)
    check_editing_survives_shared_changes(app)
    check_output_commits_editor(app)
    print("PASS: inline corrections sync image/pins, revert-to-original, pending rows + F8, paragraph merge, rotated polygon")
    print("PASS: full-text draft separate from corrections, copy follows view, editing survives shared changes, legacy/empty")
    print("PASS: copy shortcut and table output commit active line/paragraph editors; low-confidence hints remain informational")
    print(f"Review screenshots: {OUTPUT}")


if __name__ == "__main__":
    main()
