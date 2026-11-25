"""合成输入离屏验收：同窗文字/表格切换、中断结算、快捷键互斥及明暗截图。

走公开窗口入口，模型边界注入确定结果；不把这些检查当作 Windows/真实 OCR 验收。
"""
import os
from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTableWidgetSelectionRange
from ocr import OCRDocument, OCRResult, OCRResultDialog
from styles import apply_theme, tokens as T
from table import TableDialog, open_table
from table.analysis import TableResult, tokens_from_results
from table.grid import Candidate, Suggestion
from table.model import GridSpec, Rect, Token
from table.pages import ImagePage

OUTPUT = ROOT / ".scratch" / "recognition-workbench"


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def wait(app, condition, message):
    end = monotonic() + 4
    while not condition() and monotonic() < end:
        QTest.qWait(10)
        flush(app)
    assert condition(), message


def sample(label="Item"):
    image = QImage(1200, 800, QImage.Format_RGB32)
    image.fill(QColor(T.PAPER_BACKGROUND))
    records = (OCRResult(QRect(140, 150, 160, 30), label, .99),
               OCRResult(QRect(340, 150, 160, 30), "12O", .6),
               OCRResult(QRect(140, 230, 160, 30), "Amount", .99),
               OCRResult(QRect(340, 230, 160, 30), "1.234,56", .99))
    painter = QPainter(image)
    painter.setPen(QColor(T.LIGHT["foreground"]))
    painter.setFont(QFont("Arial", 20))
    for record in records:
        painter.drawText(record.box, Qt.AlignVCenter, record.text.replace("12O", "120"))
    painter.end()
    return image, records


def analyzed(image, records):
    tokens = tokens_from_results(records, image)
    grid = GridSpec((0, 80, 160), (0, 200, 400))
    local = tuple(Token(t.text, t.score, Rect(t.box.left - 120, t.box.top - 130,
                                             t.box.right - 120, t.box.bottom - 130)) for t in tokens)
    table = TableResult(QRect(120, 130, 400, 160), local,
                        Suggestion(Candidate(grid, "structure", 1), (), 1, "合成验收"))
    return tokens, (table,)


def fake_analyze(image, results=None, on_stage=None, tokens=None, regions=None, on_results=None):
    records = results if results is not None else sample()[1]
    if on_results is not None:
        on_results(records)
    return analyzed(image, records)


def ready_dialog(app, pages=1):
    sources = [ImagePage(*sample(f"Page{index + 1}"), label=f"第 {index + 1} 页") for index in range(pages)]
    dialog = TableDialog(sources)
    for index, page in enumerate(sources):
        dialog.apply_analysis(*analyzed(page.image, page.results), page=index)
    dialog.resize(1500, 850)
    dialog.show()
    dialog.activateWindow()
    flush(app)
    return dialog


def close(app, dialog):
    dialog.close()
    dialog.deleteLater()
    flush(app)


def check_entry_and_same_window(app):
    image, records = sample()
    document = OCRDocument(records, image)
    dialog = OCRResultDialog(document)
    assert isinstance(dialog, TableDialog), "OCR 入口仍打开独立文字窗口，没有进入共用 TableDialog"
    assert dialog.mode == "text" and dialog.text_pane._document is document
    dialog.show()
    dialog.activateWindow()
    flush(app)
    windows = {id(w) for w in app.topLevelWidgets() if isinstance(w, TableDialog)}
    native_id = dialog.winId()
    dialog.set_mode("table")
    wait(app, lambda: dialog._review is not None, "从文字切表格未通过后台入口得到分析结果")
    dialog.set_mode("text")
    assert dialog.winId() == native_id
    assert windows == {id(w) for w in app.topLevelWidgets() if isinstance(w, TableDialog)}, "模式切换额外创建窗口"
    assert dialog.text_pane._document is document
    close(app, dialog)
    document.set_draft("trusted draft")
    dialog = open_table(image, records, text_document=document)
    assert isinstance(dialog, TableDialog) and dialog.mode == "table"
    wait(app, lambda: dialog._review is not None, "真实表格入口没有完成分析")
    dialog.set_mode("text")
    assert [r.text for r in dialog.text_pane._document.results] == [r.text for r in records]
    assert dialog.text_pane._document is document and document.draft == "trusted draft"
    close(app, dialog)


def check_interrupted_edit_and_shortcuts(app):
    dialog = ready_dialog(app)
    table = dialog._table
    item = table.item(0, 1)
    table.setCurrentItem(item)
    dialog._result_view.focus_table()
    table.editItem(item)
    flush(app)
    editor = table.focusWidget()
    assert isinstance(editor, QPlainTextEdit), "没有进入真实格内编辑器"
    editor.setPlainText("120")
    dialog.set_mode("text")
    assert dialog._states[0].table.review.document.cell(0, 1).text == "120", "切模式丢失未提交格内改字"
    text = dialog.text_pane._text
    text.setFocus()
    cursor = text.textCursor()
    cursor.movePosition(QTextCursor.End)
    text.setTextCursor(cursor)
    QTest.keyClicks(text, " draft")
    draft = dialog.text_pane._document.text()
    QApplication.clipboard().setText("sentinel")
    QTest.keyClick(text, Qt.Key_C, Qt.ControlModifier | Qt.ShiftModifier)
    assert QApplication.clipboard().text() == draft, "文字复制快捷键被表格模式抢走或产生歧义"
    QTest.keyClick(text, Qt.Key_F8)
    assert dialog.text_pane._document.focused_ids, "文字 F8 没有定位"
    table_state = dialog._states[0].table.review.document
    QTest.keyClick(text, Qt.Key_Return, Qt.ControlModifier)
    assert dialog._states[0].table.review.document == table_state, "文字 Ctrl+Enter 修改了后台表格"
    dialog.set_mode("table")
    assert table.item(0, 1).text() == "120"
    table.setFocus()
    QApplication.clipboard().setText("sentinel")
    QTest.keyClick(table, Qt.Key_C, Qt.ControlModifier | Qt.ShiftModifier)
    assert "120" in QApplication.clipboard().text() and "draft" not in QApplication.clipboard().text()
    dialog.set_mode("text")
    assert " draft" in dialog.text_pane._document.text()
    close(app, dialog)



def check_switch_focus_shortcuts_and_arrows(app):
    dialog = ready_dialog(app)
    button = dialog._mode_bar._buttons["text"]
    QTest.mouseClick(button, Qt.LeftButton)
    flush(app)
    assert dialog.mode == "text"
    pane = dialog.text_pane
    pane._document.focus(())
    # 真正点击开关后直接按键；不能先把焦点放入正文，掩盖窗口快捷键失效。
    QTest.keyClick(app.focusWidget(), Qt.Key_F8)
    assert pane._document.focused_ids, ("顶部切换后 F8 未响应", app.focusWidget())
    before = pane._document.text()
    start = pane._text.pending_spans()[0][0]
    assert pane._text.textCursor().position() == start
    # 给真正获得键盘焦点的控件输入，避免直接给编辑器发事件掩盖焦点仍在模式栏。
    QTest.keyClicks(app.focusWidget(), "X")
    assert pane._document.text() == before[:start] + "X" + before[start:], \
        ("F8 后输入未写入待复核文字起点", app.focusWidget(), pane._document.text())
    assert pane._document.raw_results[1].text == "12O", "全文改字污染原始识别证据"
    QApplication.clipboard().setText("sentinel")
    QTest.keyClick(app.focusWidget(), Qt.Key_C, Qt.ControlModifier | Qt.ShiftModifier)
    assert QApplication.clipboard().text() == pane._document.text(), "顶部切换后复制全文快捷键未响应"
    pane._result_view.setFocus()
    pane._text.setFocus()
    cursor = pane._text.textCursor()
    cursor.setPosition(0)
    pane._text.setTextCursor(cursor)
    QTest.keyClick(pane._text, Qt.Key_Right)
    assert dialog.mode == "text" and pane._text.textCursor().position() == 1, "正文方向键被模式开关截获"
    QTest.mouseClick(dialog._mode_bar._buttons["table"], Qt.LeftButton)
    flush(app)
    dialog._table.setCurrentCell(0, 1)
    dialog._result_view.focus_table()
    QTest.keyClick(dialog._table, Qt.Key_Left)
    assert dialog.mode == "table" and dialog._table.currentColumn() == 0, "表格方向键被模式开关截获"
    close(app, dialog)


def check_grid_region_and_amount_interrupt(app):
    dialog = ready_dialog(app)
    review = dialog._review
    grid = review.document.grid.with_moved_line("v", 1, 190)
    dialog._canvas.grid = grid
    dialog._canvas._dragging = ("v", 1)
    dialog._grid_changed(grid, False)
    dialog.set_mode("text")
    dialog.set_mode("table")
    assert dialog._review is review and review.document.grid == grid
    review.edit(1, 1, "human")
    review.set_grid(grid.with_moved_line("v", 1, 180))
    assert review.document.cell(1, 1).text == "human", "切模式打断拖线后复活了旧状态"
    dialog._refresh()
    dialog._table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 1), True)
    dialog._preview_amounts()
    snapshot = review.document
    dialog.set_mode("text")
    assert not dialog._amount_preview and review.document == snapshot, "切模式应用或遗留金额预览"
    dialog.set_mode("table")
    dialog._regions_button.click()
    canvas = dialog._canvas
    count = len(canvas.regions)
    start = QPoint(round(700 * canvas.scale), round(400 * canvas.scale))
    end = QPoint(round(1000 * canvas.scale), round(600 * canvas.scale))
    QTest.mousePress(canvas, Qt.LeftButton, pos=start)
    QTest.mouseMove(canvas, end)
    assert canvas._drag is not None and canvas._drag[0] == "new", "没有进入真实新框拖动"
    pending = QRect(canvas._drag[2])
    assert len(canvas.regions) == count, "鼠标未松开前不应提前提交新框"
    # 故意没有 mouseRelease：模式切换要按同一份预览框结算。
    dialog.set_mode("text")
    assert not dialog._regions_mode, "切文字模式遗留框选状态"
    assert dialog._state.regions is not None and pending in dialog._state.regions, \
        ("切模式丢失正在拖的新表格框", pending, dialog._state.regions)
    assert len(dialog._state.tables) == count + 1
    close(app, dialog)


def check_pages_late_callback_and_view(app):
    dialog = ready_dialog(app, pages=2)
    dialog._splitter.setSizes([600, 850])
    dialog._zoom.set_scale(1.2)
    dialog.set_mode("text")
    first = dialog.text_pane._document
    first.set_draft(first.text() + " page-one-draft")
    dialog.show_page(1)
    second = dialog.text_pane._document
    assert second is not first and "Page2" in second.text()
    second.set_draft(second.text() + " page-two-draft")
    # 另一页的迟到结构回调不能改当前模式、焦点文档和草稿。
    page = dialog.pages[0]
    dialog.apply_analysis(*analyzed(page.image, page.results), page=0)
    assert dialog.mode == "text" and dialog.text_pane._document is second
    assert "page-two-draft" in second.text()
    dialog.show_page(0)
    assert dialog.text_pane._document is first and "page-one-draft" in first.text()
    dialog.set_mode("table")
    assert abs(dialog._zoom.scale - 1.2) < .01, "模式往返丢失原图比例"
    sizes = dialog._splitter.sizes()
    assert abs(sizes[0] / sum(sizes) - 600 / 1450) < .08, ("分栏比例丢失", sizes)
    capsule = dialog._mode_bar._capsule
    assert abs(capsule.geometry().center().x() - dialog._mode_bar.rect().center().x()) <= 1
    close(app, dialog)



def check_multiple_tables_and_dpr(app):
    image, records = sample()
    image.setDevicePixelRatio(1.5)
    dialog = TableDialog([ImagePage(image, records)])
    tokens, tables = analyzed(image, records)
    dialog.apply_analysis(tokens, (tables[0], replace(tables[0], rect=QRect(650, 130, 400, 160))))
    dialog.resize(1500, 850)
    dialog.show()
    dialog.activateWindow()
    flush(app)
    dialog._select_table(1)
    dialog._review.edit(0, 0, "second-table-edit")
    dialog._refresh()
    dialog._zoom.set_scale(1.6)
    for bar in (dialog._area.horizontalScrollBar(), dialog._area.verticalScrollBar()):
        bar.setValue(round(bar.maximum() * .55))
    center = dialog.to_content(dialog._area.viewport().rect().center())
    dialog.set_mode("text")
    flush(app)
    pane = dialog.text_pane
    actual = pane.preview.mapToScene(pane.preview.viewport().rect().center()) * image.devicePixelRatio()
    assert abs(pane.zoom.scale / image.devicePixelRatio() - 1.6) < .02, "DPR跨模式缩放错误"
    assert (actual - center).manhattanLength() < 12, ("DPR跨模式中心漂移", center, actual)
    pane._document.set_draft(pane._document.text() + " shared-page-draft")
    dialog.set_mode("table")
    assert dialog._state.current == 1 and dialog._review.document.cell(0, 0).text == "second-table-edit"
    dialog._select_table(0)
    dialog.set_mode("text")
    assert dialog.text_pane is pane and "shared-page-draft" in pane._document.text(), "切表丢失整页文字草稿"
    close(app, dialog)


def check_legacy_and_render(app):
    legacy = OCRResultDialog([OCRResult(QRect(0, 0, 100, 20), "legacy", .99)])
    assert legacy.mode == "text" and legacy.text_pane._preview.isHidden()
    assert not legacy._mode_bar._buttons["table"].isEnabled(), "纯文字历史开放了无原图表格模式"
    legacy.text_pane._copy_all()
    assert QApplication.clipboard().text() == "legacy"
    close(app, legacy)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        apply_theme(app, theme)
        app.setProperty("theme_mode", theme)
        dialog = ready_dialog(app)
        for mode in ("table", "text"):
            dialog.set_mode(mode)
            flush(app)
            assert dialog.grab().save(str(OUTPUT / f"{theme}-{mode}.png"))
        close(app, dialog)


def main():
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    with TemporaryDirectory() as folder, patch("settings.storage.data_directory", return_value=Path(folder)), \
            patch("table.controller.analyze", side_effect=fake_analyze):
        try:
            for check in (check_entry_and_same_window, check_interrupted_edit_and_shortcuts,
                          check_switch_focus_shortcuts_and_arrows, check_grid_region_and_amount_interrupt,
                          check_pages_late_callback_and_view,
                          check_multiple_tables_and_dpr, check_legacy_and_render):
                check(app)
                print("PASS:", check.__name__, flush=True)
        finally:
            app.aboutToQuit.emit()
    print("Screenshots:", OUTPUT)


if __name__ == "__main__":
    main()
