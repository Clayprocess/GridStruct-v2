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


def check_analysis():
    from table import export
    from table.analysis import analyze
    from table.review import TableReview
    expected_rows = tuple(tuple(value or ("Q1" if row == 0 and column > 1 else "") for column, value in enumerate(values))
                          for row, values in enumerate(DATA))
    for name, wired, merged in (("有线", True, False), ("无线", False, False), ("合并", True, True)):
        started = time.perf_counter()
        tokens, suggestion, note = analyze(synthetic(wired, merged))
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



def check_selection_after_grid_change(app):
    from table.model import GridSpec
    from table.dialog import TableDialog
    from table.grid import Candidate, Suggestion
    image = QImage(100, 100, QImage.Format_RGB32)
    image.fill(QColor(T.LIGHT['background']))
    dialog = TableDialog(image)
    grid = GridSpec((0, 50, 100), (0, 50, 100))
    dialog.apply_analysis((), Suggestion(Candidate(grid, 'structure', 1.0), (), 1.0, 'probe'), '')
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
    from table.model import GridSpec
    from table.review import TableReview

    with TemporaryDirectory() as folder:
        settings = QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
        settings.setValue("table/export_dir", "unchanged")
        image = QImage(200, 100, QImage.Format_RGB32)
        image.fill(QColor(T.LIGHT["background"]))
        dialog = TableDialog(image)
        dialog._review = TableReview((), GridSpec((0, 100), (0, 200)))
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
    from table.grid import Candidate, Suggestion
    from table.model import CellMerge, GridSpec, Rect, Token

    image = QImage(180, 80, QImage.Format_RGB32)
    image.fill(QColor(T.LIGHT["background"]))
    grid = GridSpec((0, 40, 80), (0, 60, 120, 180), (CellMerge(0, 0, 1, 2),))
    tokens = (Token("甲", .5, Rect(5, 5, 25, 25)), Token("乙", .99, Rect(125, 5, 145, 25)),
              Token("丙", .5, Rect(5, 45, 25, 65)), Token("丁", .5, Rect(65, 45, 85, 65)))
    dialog = TableDialog(image)
    dialog.show()
    dialog.apply_analysis(tokens, Suggestion(Candidate(grid, "structure", 1), (), 1, "校对导航"), "")
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


def main():
    app = QApplication.instance() or QApplication([])
    if sys.platform == "win32":
        # Windows offscreen 不自动枚举字体，否则合成表格只有方框，无法验证真实识别链路。
        for name in ("arial.ttf", "msyh.ttc"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))
    check_model()
    check_grid_edits()
    check_review()
    check_correction_merges()
    check_selection_after_grid_change(app)
    check_ocr_reuse(app)
    check_xlsx_failure(app)
    check_review_navigation(app)
    document = check_analysis()
    check_export(document)
    check_dialog(app)
    print(f"表格识别自检通过；截图和导出样例在 {OUTPUT}")
    # 没进事件循环不会发 aboutToQuit，手动等后台线程收尾。
    app.aboutToQuit.emit()


if __name__ == "__main__":
    main()
