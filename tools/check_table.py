"""表格识别离屏自检：合成表格跑完整链路（OCR + SLANet_plus + 规则网格），
再验证网格编辑、人工结果保护、撤销、导出，并输出明暗主题截图。不使用用户截图。

用法：QT_QPA_PLATFORM=offscreen python tools/check_table.py
截图输出到 .scratch/table-check/。
"""

import hashlib
import os
import sys
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from styles import apply_theme, tokens as T

OUTPUT = ROOT / ".scratch" / "table-check"
COLUMNS = (0, 180, 360, 540, 720)
ROWS = (0, 52, 104, 156, 208, 260)
DATA = (("项目", "第一季度", "", ""), ("收入", "1,200", "1,350", "1,410"), ("成本", "800", "820", "860"),
        ("利润", "400", "530", "550"), ("备注", "季度数据", "", "未审计"))


def synthetic(wired, merged):
    """5 行 4 列；merged 时首行 B1:D1 合并。"""
    image = QImage(720, 260, QImage.Format_RGB32)
    image.fill(QColor(T.LIGHT["background"]))
    painter = QPainter(image)
    font = QFont()
    font.setPixelSize(20)
    painter.setFont(font)
    painter.setPen(QColor(T.LIGHT["foreground"]))
    for row, values in enumerate(DATA):
        for column, text in enumerate(values):
            if merged and row == 0 and column == 1:
                painter.drawText(QRect(COLUMNS[1], 0, COLUMNS[4] - COLUMNS[1], 52), Qt.AlignCenter, text)
            elif not (merged and row == 0 and column > 1):
                text = text or ("Q1" if row == 0 else "")
                painter.drawText(QRect(COLUMNS[column] + 10, ROWS[row], 160, 52), Qt.AlignVCenter, text)
    if wired:
        painter.setPen(QPen(QColor(T.LIGHT["foreground"]), 1))
        for y in ROWS:
            painter.drawLine(0, min(y, 259), 719, min(y, 259))
        for x in COLUMNS:
            top = 52 if merged and x in (360, 540) else 0
            painter.drawLine(min(x, 719), top, min(x, 719), 259)
    painter.end()
    return image


def wait_for(app, dialog):
    deadline = time.time() + 120
    while dialog._review is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert dialog._review is not None, dialog._status.text()


def check_model():
    from table.slanet import MODEL_PATH, MODEL_SHA256
    assert hashlib.sha256(Path(MODEL_PATH).read_bytes()).hexdigest() == MODEL_SHA256


def check_grid_edits():
    from table.model import CellMerge, GridSpec
    grid = GridSpec((0, 10, 20, 30), (0, 10, 20, 30), (CellMerge(0, 0, 2, 2),))
    added = grid.with_line("h", 5)
    assert added.horizontal_lines == (0, 5, 10, 20, 30) and added.merges == (CellMerge(0, 0, 3, 2),)
    below = grid.with_line("h", 25)
    assert below.merges == grid.merges
    removed = grid.without_line("v", 1)
    assert removed.vertical_lines == (0, 20, 30) and removed.merges == (CellMerge(0, 0, 2, 1),)
    assert removed.without_line("h", 1).merges == ()  # 只剩 1×1 时自动拆掉
    assert grid.without_line("h", 2).merges == grid.merges
    assert grid.with_merge(CellMerge(1, 1, 3, 3)).merges == (CellMerge(1, 1, 3, 3),)
    assert grid.without_merges({(1, 1)}).merges == ()


def check_review():
    from table.model import CONFIRMED, CORRECTED, PENDING, GridSpec, Rect, Token
    from table.review import TableReview
    tokens = (Token("甲", 0.99, Rect(5, 5, 25, 15)), Token("乙", 0.99, Rect(45, 5, 65, 15)),
              Token("丙", 0.40, Rect(5, 25, 25, 35)), Token("丁", 0.99, Rect(45, 25, 65, 35)))
    review = TableReview(tokens, GridSpec((0, 20, 40), (0, 40, 80)))
    assert review.document.rows == (("甲", "乙"), ("丙", "丁"))
    assert review.pending_cells() == [(1, 0)] and review.document.cell(1, 0).status == PENDING
    review.edit(0, 1, "乙改")
    assert review.document.cell(0, 1).status == CORRECTED
    # 拖线过程中划过别的格子再停下，人工改字仍回到原位置。
    for x in (70, 30, 50):
        review.set_grid(GridSpec((0, 20, 40), (0, x, 80)), final=False)
    review.set_grid(GridSpec((0, 20, 40), (0, 42, 80)))
    assert review.document.rows[0] == ("甲", "乙改")
    # 加一条竖线：改字跟着原格中心落到新格。
    review.set_grid(review.document.grid.with_line("v", 75))
    assert review.document.rows[0] == ("甲", "乙改", "")
    assert review.confirm(1, 0) and review.document.cell(1, 0).status == CONFIRMED
    review.undo()
    assert review.document.cell(1, 0).status == PENDING
    review.undo()
    assert review.document.grid.vertical_lines == (0, 42, 80)
    review.redo()
    assert review.document.grid.vertical_lines == (0, 42, 75, 80)
    review.edit(0, 1, "乙")
    assert review.document.cell(0, 1).status == ""


def check_cell_line_join():
    from table.model import GridSpec, Rect, Token
    from table.review import TableReview, join_lines
    # 格内换行：两边英文字母补空格，数字、中文不补；连字符保留。
    cases = {("Total", "amount"): "Total amount", ("应收", "账款"): "应收账款", ("1,234", "567"): "1,234567",
             ("Hello,", "world"): "Hello, world", ("co-", "operate"): "co-operate", ("净额", "Net"): "净额Net",
             ("Ref", "2026"): "Ref2026"}
    for lines, expected in cases.items():
        assert join_lines(lines) == expected, (lines, join_lines(lines))
    tokens = (Token("Net", 0.99, Rect(5, 5, 30, 15)), Token("amount", 0.99, Rect(5, 22, 40, 32)),
              Token("应收", 0.99, Rect(60, 5, 80, 15)), Token("账款", 0.99, Rect(60, 22, 80, 32)))
    review = TableReview(tokens, GridSpec((0, 40), (0, 50, 100)))
    assert review.document.rows == (("Net amount", "应收账款"),), review.document.rows
    # 人工改字里主动敲的换行保留。
    review.edit(0, 0, "Net\namount")
    assert review.document.rows[0][0] == "Net\namount"


def check_correction_merges():
    from table.model import CellMerge, GridSpec, Rect, Token
    from table.review import TableReview
    grid = GridSpec((0, 40), (0, 40, 80))
    tokens = (Token("A", .99, Rect(5, 5, 25, 15)), Token("B", .99, Rect(45, 5, 65, 15)))
    review = TableReview(tokens, grid)
    review.edit(0, 0, "A corrected")
    review.edit(0, 1, "B corrected")
    review.set_grid(grid.with_merge(CellMerge(0, 0, 1, 2)))
    assert review.document.rows == (("A corrected B corrected", ""),), review.document.rows
    review.set_grid(grid)
    assert review.document.rows == (("A corrected", "B corrected"),), review.document.rows
    review.undo()
    assert review.document.rows == (("A corrected B corrected", ""),)
    review.redo()
    assert review.document.rows == (("A corrected", "B corrected"),)
    review.set_grid(grid.without_line("v", 1))
    assert review.document.rows == (("A corrected B corrected",),)
    review.set_grid(grid)
    assert review.document.rows == (("A corrected", "B corrected"),)
    review.edit(0, 1, "B")
    review.set_grid(grid.with_merge(CellMerge(0, 0, 1, 2)))
    assert review.document.rows == (("A corrected B", ""),), "合并不得丢掉未修改格的文字"
    review.edit(0, 0, "whole replacement")
    review.set_grid(grid)
    assert "A corrected" not in str(review.document.rows), "整格重写后不得复活旧校正"
    assert "whole replacement" in str(review.document.rows)
    review = TableReview(tokens, grid)
    review.edit(0, 0, "A corrected")
    review.set_grid(grid.with_line("v", 18))
    assert review.document.rows == (("", "A corrected", "B"),), "加线不得复活已被校正替代的原文"
    review = TableReview((tokens[0], Token("B", .5, tokens[1].box)), grid)
    review.edit(0, 0, "A corrected")
    review.set_grid(grid.with_merge(CellMerge(0, 0, 1, 2)))
    assert review.confirm(0, 0)
    review.set_grid(grid)
    assert review.document.rows == (("A corrected", "B"),), "确认合并格不得丢失组成文字的原位置"
    assert not review.pending_cells(), "已确认文字拆分后不得重新待复核"
    print("PASS correction merge/split/delete/undo/redo preserves text and positions")


def check_content_crop():
    import numpy as np
    from table.analysis import content_rect
    from table.model import Rect, Token

    tokens = (Token("内容", .99, Rect(50, 50, 80, 70)),)
    for background, line in ((255, 0), (210, 0), (255, 200), (255, 245), (210, 200)):
        pixels = np.full((300, 500, 3), background, dtype=np.uint8)
        for x in (20, 100, 300, 480):
            pixels[20:282, x:x + 2] = line
        for y in (20, 100, 180, 280):
            pixels[y:y + 2, 20:482] = line
        pixels[50:70, 50:80] = 0
        # 只有左上格有文字，空白外行列和未识别文字的表格仍需保留完整边线。
        expected = QRect(18, 18, 466, 266)
        assert content_rect(tokens, pixels) == expected
        assert content_rect((), pixels) == expected
        wider_text = (Token("内容", .99, Rect(10, 10, 490, 290)),)
        assert content_rect(wider_text, pixels) == QRect(8, 8, 484, 284)
        # 边角只差一个灰度值也保留，不把噪声阈值当成可删除内容的依据。
        pixels[0, 0] = background - 1
        pixels[-1, -1] = background - 1
        assert content_rect(tokens, pixels) == QRect(0, 0, 500, 300)
        pixels.fill(background)
        assert content_rect((), pixels) == QRect(0, 0, 500, 300)
        assert content_rect(tokens, pixels) == QRect(0, 0, 500, 300)
    print("PASS: document crop retains faint grid lines, outer rows/columns, OCR boxes, edge noise and blank pages")


def check_analysis():
    from table import export
    from table.analysis import analyze
    from table.review import TableReview
    expected_rows = tuple(tuple(value or ("Q1" if row == 0 and column > 1 else "") for column, value in enumerate(values))
                          for row, values in enumerate(DATA))
    for name, wired, merged in (("有线", True, False), ("无线", False, False), ("合并", True, True)):
        started = time.perf_counter()
        _tokens, tables = analyze(synthetic(wired, merged))
        # 有线表贴着图边：框住整张表、不切掉边线；无线表退回整块内容。
        assert len(tables) == 1, tables
        assert tables[0].rect.contains(QRect(0, 0, 720, 260).adjusted(2, 2, -2, -2)) if wired else \
            tables[0].rect.width() < 720, tables[0].rect
        tokens, suggestion, note = tables[0].tokens, tables[0].suggestion, tables[0].note
        grid = suggestion.selected.grid
        document = TableReview(tokens, grid).document
        print(f"{name}: {time.perf_counter() - started:.2f}s {suggestion.selected.strategy} "
              f"{grid.row_count}×{grid.column_count} 合并 {len(grid.merges)} {note}")
        assert (grid.row_count, grid.column_count) == (5, 4)
        if merged:
            assert [(m.top, m.left, m.bottom, m.right) for m in grid.merges] == [(0, 1, 1, 4)]
            assert document.rows[0] == ("项目", "第一季度", "", "")
            assert 'colspan="3"' in export.to_html(document)
        else:
            assert document.rows == expected_rows, document.rows
    return document


def check_stacked_output():
    from table.export import stack
    from table.model import CellMerge, GridSpec, TableCell, TableDocument

    grid = GridSpec((0, 1, 2), (0, 1, 2))
    first = TableDocument(grid, ((TableCell("甲"), TableCell("10")), (TableCell("乙"), TableCell("20"))))
    second = TableDocument(grid, ((TableCell("甲"), TableCell("10")), (TableCell("丙"), TableCell("30"))))
    assert stack((first, second)).rows == (*first.rows, *second.rows), "无表头重复记录默认不得删除"
    assert stack((first, second), True).rows == (*first.rows, second.rows[1])
    merged = TableDocument(GridSpec((0, 1, 2), (0, 1, 2), (CellMerge(0, 0, 2, 1),)),
                           (second.cells[0], (TableCell(), TableCell("30"))))
    combined = stack((first, merged), True)
    assert combined.rows == (*first.rows, *merged.rows), "去重不得拆掉跨行表头"
    assert combined.grid.merges == (CellMerge(2, 0, 4, 1),)
    print("PASS: stacked output retains repeated records by default and preserves merged headers")


def check_export(document):
    from table import export
    from table.model import PENDING
    from dataclasses import replace
    document = document.with_cell(4, 3, replace(document.cell(4, 3), text="多行\n内容", status=PENDING))
    assert '"多行\n内容"' in export.to_tsv(document)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for suffix in ("xlsx", "csv", "html"):
        export.save(document, str(OUTPUT / f"table.{suffix}"))
    assert (OUTPUT / "table.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    with zipfile.ZipFile(OUTPUT / "table.xlsx") as workbook:
        sheet = workbook.read("xl/worksheets/sheet1.xml").decode()
        strings = workbook.read("xl/sharedStrings.xml").decode()
        assert '<mergeCell ref="B1:D1"/>' in sheet and "第一季度" in strings and "1,200" in strings
        assert any(name.startswith("xl/comments") for name in workbook.namelist())


def check_dialog(app):
    from table import open_table
    from table.model import CORRECTED
    image = synthetic(True, True)
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        app.setProperty("theme_mode", mode)
        dialog = open_table(image)
        wait_for(app, dialog)
        app.processEvents()
        canvas, table = dialog._canvas, dialog._table
        assert (table.rowCount(), table.columnCount()) == (5, 4) and table.columnSpan(0, 1) == 3
        # 拖第 2 条竖线到 C 列中间：360→450 后“1,350”仍在 C 列（中心 410 < 450）。
        scale = canvas.width() / 720
        start = QPoint(round(360 * scale), round(130 * scale))
        QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(canvas, QPoint(round(450 * scale), start.y()))
        QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(round(450 * scale), start.y()))
        assert dialog._review.document.grid.vertical_lines[2] == 450, dialog._review.document.grid
        assert dialog._review.can_undo
        dialog._undo()
        assert dialog._review.document.grid.vertical_lines[2] == 360
        # 改字后再改网格，人工结果保留。
        table.item(2, 1).setText("801")
        assert dialog._review.document.cell(2, 1).status == CORRECTED
        QTest.mouseClick(canvas, Qt.LeftButton, Qt.ShiftModifier, QPoint(round(600 * scale), round(80 * scale)))
        assert dialog._review.document.grid.row_count == 6
        assert dialog._review.document.rows[3][1] == "801", dialog._review.document.rows
        # 待复核样式：把一格降成低置信度，看底色和提示条。
        from dataclasses import replace
        from table.model import PENDING
        review = dialog._review
        review.document = review.document.with_cell(4, 3, replace(review.document.cell(4, 3), status=PENDING))
        dialog._refresh()
        canvas.select_cell(1, 1)
        dialog._select_in_table(1, 1)
        app.processEvents()
        assert dialog._pending.isVisible() or not dialog.isVisible()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        assert dialog.grab().save(str(OUTPUT / f"dialog-{mode}.png"))
        dialog.close()
        app.processEvents()



def check_screenshot_regions(app):
    """截图表格识别：自动框出表格、标题不进表；框选补一张表后按“表1/表2”分工作表。"""
    from unittest.mock import patch
    from PySide6.QtCore import QCoreApplication, QEvent
    from table import open_table
    image = QImage(800, 420, QImage.Format_RGB32)
    image.fill(QColor(T.LIGHT["background"]))
    painter = QPainter(image)
    font = QFont()
    font.setPixelSize(22)
    painter.setFont(font)
    painter.setPen(QColor(T.LIGHT["foreground"]))
    painter.drawText(20, 40, "季度经营数据汇总")
    painter.drawImage(QPoint(40, 70), synthetic(True, False))
    painter.drawText(40, 380, "备注说明 数据未经审计")
    painter.end()
    dialog = open_table(image)
    wait_for(app, dialog)
    state = dialog._state
    assert len(state.tables) == 1 and state.tables[0].rect.contains(QRect(41, 71, 718, 258)), state.tables[0].rect
    texts = " ".join(token.text for token in state.tables[0].review._tokens)
    assert "汇总" not in texts and "备注说明" not in texts, texts
    assert dialog._regions_button.isEnabled() and dialog._join.isHidden()
    dialog._regions_button.click()
    canvas = dialog._canvas
    scale = canvas.scale
    start, end = QPoint(round(30 * scale), round(350 * scale)), QPoint(round(400 * scale), round(395 * scale))
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(canvas, end)
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, end)
    dialog._regions_button.click()
    deadline = time.time() + 60
    while dialog._state.regions is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert [name for name, _document, _members in dialog._sheets()] == ["表1", "表2"]
    assert dialog._tabs.count() == 2
    dialog._tabs.setCurrentIndex(1)
    with patch("table.dialog.show_toast"):
        dialog._copy()
    assert "备注说明" in app.clipboard().text().replace(" ", ""), app.clipboard().text()
    dialog.close()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.clipboard().clear()
    print("PASS: screenshot tables auto-boxed without surrounding text; extra box becomes a second sheet")


def check_reanalysis_races(app):
    """改框后的后台重算不打断正在输入的改字；接上一页只接紧挨着的上一页末表。"""
    from unittest.mock import patch
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QPlainTextEdit
    from table.analysis import TableResult
    from table.dialog import TableDialog
    from table.grid import Candidate, Suggestion
    from table.model import GridSpec, Rect, Token
    from table.pages import ImagePage

    def result(rect, text):
        grid = GridSpec((0, 40), (0, 100))
        token = Token(text, .99, Rect(5, 5, 60, 30))
        return TableResult(rect, (token,), Suggestion(Candidate(grid, "structure", 1), (), 1, "probe"))

    image = QImage(400, 200, QImage.Format_RGB32)
    image.fill(QColor(T.LIGHT["background"]))
    dialog = TableDialog(image)
    dialog.show()
    first = QRect(0, 0, 100, 40)
    dialog.apply_analysis((), (result(first, "original"),))
    app.processEvents()
    # 补一个框并应用：新框排队重算，原表照常可改。
    dialog._regions_button.click()
    dialog._canvas.regions.append(QRect(200, 100, 100, 40))
    dialog._regions_button.click()
    assert dialog._state.regions is not None and dialog._review is not None
    table = dialog._table
    table.setCurrentCell(0, 0)
    table.editItem(table.currentItem())
    app.processEvents()
    editor = app.focusWidget()
    assert isinstance(editor, QPlainTextEdit)
    editor.setPlainText("user change in progress")
    dialog.apply_analysis((), (result(QRect(200, 100, 100, 40), "new"),))
    app.processEvents()
    assert app.focusWidget() is editor and editor.toPlainText() == "user change in progress", "重算完成不得销毁输入框"
    dialog._commit_editor()
    assert dialog._review.document.rows[0][0] == "user change in progress", dialog._review.document.rows
    assert dialog._tabs.count() == 2 and dialog._tabs.tabText(1) == "表2"
    # 新框重算失败：撤掉新框，旧表里正在输入的字同样保留。新框排在当前表前面时当前表不换。
    dialog._tabs.setCurrentIndex(1)
    dialog._regions_button.click()
    dialog._canvas.regions.append(QRect(0, 50, 100, 40))
    dialog._regions_button.click()
    assert dialog._state.regions is not None and dialog._state.current == 2, dialog._state.current
    table.setCurrentCell(0, 0)
    table.editItem(table.currentItem())
    app.processEvents()
    editor = app.focusWidget()
    assert isinstance(editor, QPlainTextEdit)
    editor.setPlainText("typing while it fails")
    with patch("table.dialog.show_message"):
        dialog.show_error("结构模型出错")
    app.processEvents()
    assert app.focusWidget() is editor and editor.toPlainText() == "typing while it fails", "重算失败不得销毁输入框"
    assert dialog._state.current == 1 and "重新分析失败" in dialog._status.text()
    dialog._commit_editor()
    assert dialog._review.document.rows[0][0] == "typing while it fails", dialog._review.document.rows
    assert dialog._tabs.count() == 2
    dialog.close()

    pages = [ImagePage(image.copy(), label=f"第 {index + 1} 页") for index in range(3)]
    dialog = TableDialog(pages)
    for index in range(3):
        dialog.apply_analysis((), (result(first, f"p{index + 1}"),), page=index)
    dialog.show_page(2)
    dialog._join.setChecked(True)
    assert [members for _name, _document, members in dialog._sheets()] == [[(0, 0)], [(1, 0), (2, 0)]]
    # 第 2 页框全删：第 3 页不得跨过它接到第 1 页上，单独导出并提示。
    dialog.show_page(1)
    dialog._apply_regions([])
    dialog.show_page(2)
    assert [members for _name, _document, members in dialog._sheets()] == [[(0, 0)], [(2, 0)]]
    assert "上一页暂无可接的表" in dialog._join.text()
    assert "第 3 页勾了接上一页" in dialog._pending_note(dialog._sheets())
    # 上一页还在重算或识别失败时同样不接。
    dialog._states[1].tables = [dialog._states[0].tables[0].__class__(first)]
    dialog._states[1].regions = [first]
    assert [members for _name, _document, members in dialog._sheets()] == [[(0, 0)], [(2, 0)]]
    dialog._states[1].regions = None
    dialog._states[1].tables = []
    dialog._states[1].error = "坏页"
    dialog._states[1].tokens = None
    assert [members for _name, _document, members in dialog._sheets()] == [[(0, 0)], [(2, 0)]]
    dialog.close()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    print("PASS: re-analysis keeps in-progress cell edits; join only attaches to the adjacent previous page")


def check_selection_after_grid_change(app):
    from table.model import GridSpec
    from table.dialog import TableDialog
    from table.grid import Candidate, Suggestion
    from table.analysis import TableResult
    image = QImage(100, 100, QImage.Format_RGB32)
    image.fill(QColor(T.LIGHT['background']))
    dialog = TableDialog(image)
    grid = GridSpec((0, 50, 100), (0, 50, 100))
    dialog.apply_analysis((), (TableResult(None, (), Suggestion(Candidate(grid, 'structure', 1.0), (), 1.0, 'probe')),))
    canvas = dialog._canvas
    canvas.select_cell(1, 1)
    dialog._scheme_chosen(1)
    QTest.keyClick(canvas, Qt.Key_Right, Qt.ShiftModifier)
    assert canvas._selected == {(0, 0)}, ('scheme selection', canvas._selected)
    assert not canvas.can_merge
    assert not canvas.grab().isNull()
    dialog._undo()
    canvas.select_cell(1, 1)
    QTest.keyClick(canvas, Qt.Key_Right, Qt.ControlModifier)
    QTest.keyClick(canvas, Qt.Key_Delete)
    QTest.keyClick(canvas, Qt.Key_Right, Qt.ShiftModifier)
    QTest.keyClick(canvas, Qt.Key_Down, Qt.ShiftModifier)
    assert canvas._selected == {(0, 0), (1, 0)}, ('delete column', canvas._selected)
    assert canvas.can_merge
    QTest.keyClick(canvas, Qt.Key_Down, Qt.ControlModifier)
    QTest.keyClick(canvas, Qt.Key_Delete)
    QTest.keyClick(canvas, Qt.Key_Right, Qt.ShiftModifier)
    assert canvas._selected == {(0, 0)}, ('delete row', canvas._selected)
    assert not canvas.can_merge
    assert not canvas.grab().isNull()
    dialog.close()



def check_ocr_reuse(app):
    from tempfile import TemporaryDirectory
    from unittest.mock import patch
    from PySide6.QtCore import QPoint, QSettings, QCoreApplication, QEvent
    from PySide6.QtGui import QPixmap
    from annotate import AnnotationWindow
    from pin import PinWindow
    from ocr import OCRDocument, OCRResult, OCRResultDialog
    from table.analysis import tokens_from_results

    with TemporaryDirectory() as folder:
        settings = QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
        settings.setValue("config_version", 1)
        settings.sync()
        with patch("settings.storage.data_directory", return_value=Path(folder)):
            for paragraph in (False, True):
                image = QImage(200, 100, QImage.Format_RGB32)
                image.fill(QColor(T.LIGHT["background"]))
                raw = (OCRResult(QRect(5, 5, 60, 20), "120O", .6),
                       OCRResult(QRect(5, 28, 60, 20), "total", .9))
                document = OCRDocument(raw, image)
                ids = document.groups()[0] if paragraph else (raw[0].item_id,)
                document.correct(ids, "1200 total" if paragraph else "1200")
                expected = ["1200 total"] if paragraph else ["1200", "total"]
                pixmap = QPixmap.fromImage(image)
                annotation = AnnotationWindow(pixmap, QPoint())
                annotation._ocr.adopt_document(document, pixmap)
                pin = PinWindow(pixmap, document=document)
                dialog = OCRResultDialog(document)
                for entry in (lambda: annotation._on_action("table"), pin._recognize_table, dialog._open_table):
                    with patch("table.open_table") as opened:
                        entry()
                        passed_image, results = opened.call_args.args
                        passed_image = passed_image.toImage() if isinstance(passed_image, QPixmap) else passed_image
                        tokens = tokens_from_results(results, passed_image)
                        assert [token.text for token in tokens] == expected
                        if paragraph:
                            assert len(tokens) == 1 and tokens[0].box.top == 5 and tokens[0].box.bottom == 48
                pin._allow_close = True
                pin.close()
                dialog.close()
                dialog.deleteLater()
                app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    print("PASS: corrected OCR reused by screenshot, pin and OCR result entries")


def check_xlsx_failure(app):
    from tempfile import TemporaryDirectory
    from unittest.mock import patch
    from PySide6.QtCore import QSettings, QCoreApplication, QEvent
    from table import TableDialog
    from table.dialog import _TableState
    from table.model import GridSpec
    from table.review import TableReview

    with TemporaryDirectory() as folder:
        settings = QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
        settings.setValue("table/export_dir", "unchanged")
        image = QImage(200, 100, QImage.Format_RGB32)
        image.fill(QColor(T.LIGHT["background"]))
        dialog = TableDialog(image)
        dialog._state.tokens = ()
        dialog._state.tables = [_TableState(review=TableReview((), GridSpec((0, 100), (0, 200))))]
        # 目录不能作为 XLSX 文件写入，走真实 XlsxWriter 文件失败路径。
        destination = Path(folder) / "unwritable.xlsx"
        destination.mkdir()
        app.clipboard().setText("keep clipboard")
        with patch("settings.get_settings", return_value=settings), \
                patch("table.dialog.QFileDialog.getSaveFileName", return_value=(str(destination), "Excel 工作簿 (*.xlsx)")), \
                patch("table.dialog.show_message") as message:
            dialog._export_file()
            assert message.call_args.args[1] == "导出失败"
        assert app.clipboard().text() == "keep clipboard"
        assert settings.value("table/export_dir") == "unchanged"
        dialog.close()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.clipboard().clear()
    print("PASS: XLSX write failure reports error and preserves clipboard/settings")


def check_review_navigation(app):
    from unittest.mock import patch
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QPlainTextEdit
    from table.dialog import TableDialog
    from table.analysis import TableResult
    from table.grid import Candidate, Suggestion
    from table.model import CellMerge, GridSpec, Rect, Token

    image = QImage(180, 80, QImage.Format_RGB32)
    image.fill(QColor(T.LIGHT["background"]))
    grid = GridSpec((0, 40, 80), (0, 60, 120, 180), (CellMerge(0, 0, 1, 2),))
    tokens = (Token("甲", .5, Rect(5, 5, 25, 25)), Token("乙", .99, Rect(125, 5, 145, 25)),
              Token("丙", .5, Rect(5, 45, 25, 65)), Token("丁", .5, Rect(65, 45, 85, 65)))
    dialog = TableDialog(image)
    dialog.show()
    dialog.apply_analysis(tokens, (TableResult(None, tokens, Suggestion(Candidate(grid, "structure", 1), (), 1, "校对导航")),))
    app.processEvents()
    table = dialog._table
    assert (table.currentRow(), table.currentColumn()) == (0, 0)
    assert table.hasFocus() and dialog._canvas._selected == {(0, 0)}
    assert dialog._position.text() == "当前 A1" and "3" in dialog._pending.text()
    QTest.keyClick(table, Qt.Key_F8)
    assert (table.currentRow(), table.currentColumn()) == (1, 0)
    QTest.keyClick(table, Qt.Key_Return, Qt.ControlModifier)
    assert (table.currentRow(), table.currentColumn()) == (1, 1)
    assert dialog._review.document.pending_count == 2
    table.editItem(table.currentItem())
    app.processEvents()
    editor = app.focusWidget()
    assert isinstance(editor, QPlainTextEdit)
    editor.setPlainText("丁改")
    QTest.keyClick(editor, Qt.Key_Return, Qt.ControlModifier)
    app.processEvents()
    assert dialog._review.document.rows[1][1] == "丁改"
    assert (table.currentRow(), table.currentColumn()) == (0, 0)
    assert table.hasFocus() and dialog._review.document.pending_count == 1
    # 合并格后的 Enter/Tab 导航直接到下一锚点，不落进被覆盖的格子。
    table.editItem(table.currentItem())
    app.processEvents()
    editor = app.focusWidget()
    editor.setPlainText("甲改")
    QTest.keyClick(editor, Qt.Key_Return, Qt.AltModifier)
    QTest.keyClicks(editor, "next")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    assert dialog._review.document.rows[0][0] == "\nnext甲改"
    assert (table.currentRow(), table.currentColumn()) == (0, 2)
    QTest.keyClick(app.focusWidget(), Qt.Key_Backtab, Qt.ShiftModifier)
    app.processEvents()
    assert (table.currentRow(), table.currentColumn()) == (0, 0)
    dialog._commit_editor()
    assert dialog._review.document.pending_count == 0 and not dialog._next_shortcut.isEnabled()
    with patch("table.dialog.show_toast") as toast:
        dialog._copy()
        assert "丁改" in app.clipboard().text() and "待复核" not in toast.call_args.args[0]
    dialog._undo()
    assert dialog._review.document.pending_count == 1
    # 仍有待复核时照常输出，只在提示里带上剩余数量，不弹窗拦截。
    with patch("table.dialog.show_toast") as toast:
        dialog._copy()
        assert "丁改" in app.clipboard().text() and "还有 1 处待复核" in toast.call_args.args[0]
    dialog._redo()
    assert dialog._review.document.pending_count == 0
    dialog.close()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.clipboard().clear()
    print("PASS: pending navigation, merged-cell editing, output with pending note and undo/redo")


def _write_pdf(path, pages):
    """每页把一张合成表格画进 A4，模拟扫描件 PDF。"""
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter
    writer = QPdfWriter(str(path))
    writer.setResolution(200)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    painter = QPainter(writer)
    for index, image in enumerate(pages):
        if index:
            writer.newPage()
        painter.drawImage(QPoint(120, 160), image)
    painter.end()


def _document_page(*tables, text=()):
    """合成整页：若干张表（位置, 图）加几行正文，正文不在任何表格里。"""
    page = QImage(1400, 2000, QImage.Format_RGB32)
    page.fill(QColor(T.LIGHT["background"]))
    painter = QPainter(page)
    font = QFont()
    font.setPixelSize(26)
    painter.setFont(font)
    painter.setPen(QColor(T.LIGHT["foreground"]))
    for position, image in tables:
        painter.drawImage(position, image)
    for position, line in text:
        painter.drawText(position, line)
    painter.end()
    return page


def check_table_regions():
    """整页找有线表：多张表按阅读顺序，只有外框的文本框不算表，无表页面返回空。"""
    from table.analysis import image_to_bgr, reading_order, table_regions
    page = _document_page((QPoint(40, 300), synthetic(True, False)), (QPoint(40, 900), synthetic(True, True)),
                          (QPoint(800, 300), synthetic(True, False).scaled(560, 200)),
                          text=((QPoint(40, 120), "第一部分 财务摘要"), (QPoint(40, 700), "正文段落不属于表格")))
    painter = QPainter(page)
    painter.setPen(QPen(QColor(T.LIGHT["foreground"]), 2))
    painter.drawRect(30, 1300, 1300, 150)  # 提示框：只有外框
    painter.end()
    regions = table_regions(image_to_bgr(page))
    assert [(rect.left() // 10, rect.top() // 10) for rect in regions] == [(3, 29), (79, 29), (3, 89)], regions
    assert all(rect.contains(QRect(41, 301, 718, 258)) for rect in regions[:1]), regions[0]
    blank = _document_page(text=((QPoint(40, 120), "只有正文"),))
    assert table_regions(image_to_bgr(blank)) == []
    shuffled = [regions[2], regions[1], regions[0]]
    assert reading_order(shuffled) == regions
    print("PASS: page table regions found in reading order; framed text boxes and plain pages yield no table")


def check_multi_sheet_export():
    from table import export
    from table.model import GridSpec, TableCell, TableDocument
    grid = GridSpec((0, 1, 2), (0, 1, 2))
    first = TableDocument(grid, ((TableCell("甲"), TableCell("10")), (TableCell("乙"), TableCell("20"))))
    second = TableDocument(grid, ((TableCell("丙"), TableCell("30")), (TableCell("丁"), TableCell("40"))))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sheets = [("第1页-表1", first), ("第2页-表1", second)]
    export.save(sheets, str(OUTPUT / "sheets.xlsx"))
    with zipfile.ZipFile(OUTPUT / "sheets.xlsx") as workbook:
        names = workbook.read("xl/workbook.xml").decode()
        assert 'name="第1页-表1"' in names and 'name="第2页-表1"' in names, names
        assert "丁" in workbook.read("xl/sharedStrings.xml").decode()
    export.save(sheets, str(OUTPUT / "sheets.html"))
    html = (OUTPUT / "sheets.html").read_text(encoding="utf-8")
    assert html.count("<table>") == 2 and "<h2>第2页-表1</h2>" in html
    try:
        export.save(sheets, str(OUTPUT / "sheets.csv"))
    except ValueError as exc:
        assert "CSV" in str(exc)
    else:
        raise AssertionError("多张表不得写成一个 CSV")
    export.save(first, str(OUTPUT / "single.csv"))
    print("PASS: one worksheet per table, HTML lists every table, CSV refuses several tables")


def _wait_document(app, dialog):
    deadline = time.time() + 240
    while time.time() < deadline and any(
            (not state.analyzed and not state.error) or state.regions is not None for state in dialog._states):
        app.processEvents()
        time.sleep(0.02)
    assert all(state.analyzed for state in dialog._states), [state.error for state in dialog._states]


def check_document(app, mode):
    """识别文档：整页自动框表、一页多表、改框只重算改过的表、接上一页、一表一个工作表、页码跳转。"""
    from unittest.mock import patch
    from table import open_document
    folder = OUTPUT / "document"
    folder.mkdir(parents=True, exist_ok=True)
    plain, merged = synthetic(True, False), synthetic(True, True)
    first = _document_page((QPoint(40, 300), plain), (QPoint(40, 900), merged),
                           text=((QPoint(40, 120), "第一部分 财务摘要"), (QPoint(40, 1600), "表格外的说明文字")))
    # 第 2 页是第 1 页第二张表的续表：表头一模一样。
    second = _document_page((QPoint(40, 200), merged), text=((QPoint(40, 120), "续表"),))
    _write_pdf(folder / "报表.pdf", [first, second])
    synthetic(True, False).save(str(folder / "附页.png"))
    (folder / "坏图.png").write_bytes(b"not an image")
    paths = [str(folder / name) for name in ("附页.png", "坏图.png", "报表.pdf")]
    # 图标按创建时的主题染色，先切主题再开窗口。
    apply_theme(app, mode)
    with patch("table.controller.QFileDialog.getOpenFileNames", return_value=(paths, "")), \
            patch("table.controller.show_message") as message:
        dialog = open_document()
    assert "坏图.png" in message.call_args.args[2]
    # 按文件名排序，PDF 两页依次展开。
    assert [page.label for page in dialog.pages] == ["报表.pdf 第 1 页", "报表.pdf 第 2 页", "附页.png"], \
        [page.label for page in dialog.pages]
    _wait_document(app, dialog)
    assert dialog._progress.isHidden()
    page_one, page_two, page_three = dialog._states
    assert len(page_one.tables) == 2 and len(page_two.tables) == 1 and len(page_three.tables) == 1, \
        [len(state.tables) for state in dialog._states]
    for table in (*page_one.tables, *page_two.tables):
        assert table.suggestion.selected.strategy == "structure", table.suggestion.selected
        assert table.review.document.grid.row_count == 5, table.review.document.rows
    # 框外的标题和说明不进任何表格。
    texts = {token.text for table in page_one.tables for token in table.review._tokens}
    assert not any("财务摘要" in text or "说明文字" in text for text in texts), texts
    assert dialog._tabs.isVisible() or not dialog.isVisible()
    assert dialog._tabs.count() == 2

    # 改第 1 页表 1 一格，切表、翻页再回来仍在。
    dialog._review.edit(1, 1, "1,201")
    dialog._refresh()
    dialog._tabs.setCurrentIndex(1)
    assert dialog._state.current == 1 and dialog._review.document.grid.merges
    dialog.show_page(2)
    dialog.show_page(0)
    dialog._tabs.setCurrentIndex(0)
    assert dialog._review.document.rows[1][1] == "1,201"
    assert dialog._table.item(1, 1).text() == "1,201"

    # 默认不合并跨页表：一张表一个工作表，按“第N页-表M”命名。
    assert [name for name, _document, _members in dialog._sheets()] == \
        ["第1页-表1", "第1页-表2", "第2页-表1", "第3页-表1"]
    # 第 2 页勾“接上一页”：接到第 1 页表 2 后面，重复表头去掉。
    dialog.show_page(1)
    assert dialog._join.isEnabled()
    QTest.mouseClick(dialog._join, Qt.LeftButton)
    sheets = dialog._sheets()
    assert [name for name, _document, _members in sheets] == ["第1页-表1", "第1页-表2", "第3页-表1"]
    joined = sheets[1][1]
    assert joined.grid.row_count == 5 + 4 and joined.rows.count(joined.rows[0]) == 1, joined.rows
    with patch("table.dialog.show_toast") as toast:
        dialog._copy()
        assert "第1页-表2" in toast.call_args.args[0]
    assert app.clipboard().text().count("项目") == 1
    destination = folder / f"导出-{mode}.xlsx"
    with patch("table.dialog.QFileDialog.getSaveFileName", return_value=(str(destination), "")) as chooser, \
            patch("table.dialog.show_toast"):
        dialog._export_file()
        assert "CSV" not in chooser.call_args.args[3]
    with zipfile.ZipFile(destination) as workbook:
        names = workbook.read("xl/workbook.xml").decode()
        assert names.count("<sheet ") == 3 and 'name="第1页-表2"' in names, names

    # 页码跳转：无效、越界只提示，不翻页。
    for text, expected in (("abc", "页码无效"), ("9", "没有第 9 页"), ("0", "没有第 0 页")):
        dialog._page_input.setText(text)
        dialog._jump_to_page()
        assert dialog._index == 1 and expected in dialog._status.text(), dialog._status.text()
        assert dialog._page_input.text() == "2"
    dialog._page_input.setText(" 1 ")
    dialog._jump_to_page()
    assert dialog._index == 0

    # 框选：删掉表 2、给说明文字补一个框；表 1 没动，改字保留，只重算新框。
    dialog._regions_button.click()
    canvas = dialog._canvas
    assert len(canvas.regions) == 2 and not dialog._copy_button.isEnabled()
    canvas.selected = 1
    canvas.remove_selected()
    scale = canvas.scale
    # PDF 里整页图画在 (120, 160)：说明文字的基线在 (160, 1760)。
    start, end = QPoint(round(140 * scale), round(1715 * scale)), QPoint(round(720 * scale), round(1790 * scale))
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(canvas, QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
    QTest.mouseMove(canvas, end)
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, end)
    assert len(canvas.regions) == 2, canvas.regions
    app.processEvents()
    assert dialog.grab().save(str(OUTPUT / f"document-regions-{mode}.png"))
    dialog._regions_button.click()
    assert dialog._state.regions is not None and dialog._tabs.tabText(1).endswith("…")
    _wait_document(app, dialog)
    tables = dialog._state.tables
    assert len(tables) == 2 and tables[0].review.document.rows[1][1] == "1,201"
    assert any("说明文字" in token.text for token in tables[1].review._tokens), tables[1].review._tokens
    # 第 2 页仍接在上一页最后一张表（现在是表 2，说明文字那张）后面。
    assert [name for name, _document, _members in dialog._sheets()] == ["第1页-表1", "第1页-表2", "第3页-表1"]

    # 一张都不框的页：不出表，提示去框选。
    dialog.show_page(2)
    dialog._regions_button.click()
    dialog._canvas.remove_selected()
    dialog._regions_button.click()
    assert not dialog._state.tables and "本页没有框选表格" in dialog._status.text()
    assert [name for name, _document, _members in dialog._sheets()] == ["第1页-表1", "第1页-表2"]
    dialog.show_page(0)
    app.processEvents()
    assert dialog.grab().save(str(OUTPUT / f"document-{mode}.png"))
    dialog.close()
    app.processEvents()
    app.clipboard().clear()
    print("PASS: document pages auto-split into tables, region edits re-analyse only changed boxes, "
          "join to previous page, one sheet per table and page jump validation")


def main():
    app = QApplication.instance() or QApplication([])
    if sys.platform == "win32":
        # Windows offscreen 不自动枚举字体，否则合成表格只有方框，无法验证真实识别链路。
        for name in ("arial.ttf", "msyh.ttc"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))
    check_model()
    check_grid_edits()
    check_review()
    check_cell_line_join()
    check_correction_merges()
    check_selection_after_grid_change(app)
    check_ocr_reuse(app)
    check_xlsx_failure(app)
    check_review_navigation(app)
    check_content_crop()
    check_stacked_output()
    check_table_regions()
    check_multi_sheet_export()
    document = check_analysis()
    check_export(document)
    check_dialog(app)
    check_screenshot_regions(app)
    check_reanalysis_races(app)
    for mode in ("light", "dark"):
        check_document(app, mode)
    print(f"表格识别自检通过；截图和导出样例在 {OUTPUT}")
    # 没进事件循环不会发 aboutToQuit，手动等后台线程收尾。
    app.aboutToQuit.emit()


if __name__ == "__main__":
    main()
