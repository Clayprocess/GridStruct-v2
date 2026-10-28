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
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
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


def main():
    app = QApplication.instance() or QApplication([])
    check_model()
    check_grid_edits()
    check_review()
    document = check_analysis()
    check_export(document)
    check_dialog(app)
    print(f"表格识别自检通过；截图和导出样例在 {OUTPUT}")
    # 没进事件循环不会发 aboutToQuit，手动等后台线程收尾。
    from table import controller
    controller._service.shutdown()


if __name__ == "__main__":
    main()
