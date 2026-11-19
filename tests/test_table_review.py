"""表格复核回归；旧版离屏脚本复用这些场景。"""

import csv
import zipfile
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree as ET

from PySide6.QtCore import QRect, QSettings
from PySide6.QtGui import QImage

from ocr import OCRResult
from table import export
from table.analysis import _shift, tokens_from_results
from table.dialog import TableDialog, _cell_tip, _TableState
from table.model import CONFIRMED, CORRECTED, PENDING, CellMerge, GridSpec, Rect, Token
from table.review import TableReview, join_lines


def test_grid_edits():
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


def test_review():
    tokens = (
        Token("甲", 0.99, Rect(5, 5, 25, 15)),
        Token("乙", 0.99, Rect(45, 5, 65, 15)),
        Token("丙", 0.40, Rect(5, 25, 25, 35)),
        Token("丁", 0.99, Rect(45, 25, 65, 35)),
    )
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


def test_cell_line_join():
    # 格内换行：两边英文字母补空格，数字、中文不补；连字符保留。
    cases = {
        ("Total", "amount"): "Total amount",
        ("应收", "账款"): "应收账款",
        ("1,234", "567"): "1,234567",
        ("Hello,", "world"): "Hello, world",
        ("co-", "operate"): "co-operate",
        ("净额", "Net"): "净额Net",
        ("Ref", "2026"): "Ref2026",
    }
    for lines, expected in cases.items():
        assert join_lines(lines) == expected, (lines, join_lines(lines))
    tokens = (
        Token("Net", 0.99, Rect(5, 5, 30, 15)),
        Token("amount", 0.99, Rect(5, 22, 40, 32)),
        Token("应收", 0.99, Rect(60, 5, 80, 15)),
        Token("账款", 0.99, Rect(60, 22, 80, 32)),
    )
    review = TableReview(tokens, GridSpec((0, 40), (0, 50, 100)))
    assert review.document.rows == (("Net amount", "应收账款"),), review.document.rows
    # 人工改字里主动敲的换行保留。
    review.edit(0, 0, "Net\namount")
    assert review.document.rows[0][0] == "Net\namount"


def _cross_column_sample():
    grid = GridSpec((0, 30), (0, 49, 100))
    joined = Token(
        "0001 某单位",
        0.99,
        Rect(8, 5, 88, 25),
        chars=tuple((ch, 10 + i * 10, 18 + i * 10) for i, ch in enumerate("0001某单位")),
    )
    return grid, joined


def test_cross_column_regrid_preserves_text():
    """复核 #54：人工结果遇到重新切分时，任何原字都不能丢失或复活。"""
    grid, joined = _cross_column_sample()
    for token in (joined, replace(joined, chars=())):
        original = TableReview((token,), grid).document.rows[0]
        for edit in (False, True):
            for both in (False, True):
                for target in [GridSpec((0, 30), (0, x, 100)) for x in range(1, 100)] + [
                    grid.with_merge(CellMerge(0, 0, 1, 2)),
                    GridSpec((0, 30), (0, 100)),
                ]:
                    review = TableReview((token,), grid)
                    for column in range(2 if both else 1):
                        review.edit(0, column, f"改{column}") if edit else review.confirm(0, column)
                    before = review.document
                    expected = (
                        ("改0" + ("改1" if both else original[1])) if edit else "".join(original)
                    )
                    review.set_grid(target)
                    actual = "".join(review.document.rows[0]).replace(" ", "")
                    assert actual == expected.replace(" ", ""), (
                        edit,
                        both,
                        target,
                        actual,
                        expected,
                    )
                    if review.document != before:
                        review.undo()
                        assert review.document == before
                        review.redo()
                    review.set_grid(grid)
                    assert "".join(review.document.rows[0]).replace(" ", "") == expected.replace(
                        " ", ""
                    )


def test_cross_column_character_positions():
    grid, _ = _cross_column_sample()
    # 检测框中心偏左，但所有字都在右列；以及跨线宽字框的中心不能二次改变归列。
    side = Token("AB", 0.99, Rect(0, 0, 100, 20), chars=(("A", 70, 80), ("B", 80, 90)))
    assert TableReview((side,), grid).document.rows == (("", "AB"),)
    overlap = Token(
        "ABC", 0.99, Rect(0, 0, 100, 20), chars=(("A", 0, 10), ("B", 20, 88), ("C", 55, 57))
    )
    assert TableReview((overlap,), GridSpec((0, 30), (0, 55, 100))).document.rows == (("AB", "C"),)
    for chars in (
        (("A", 0, 10), ("B", float("nan"), 90)),
        (("A", 80, 90), ("B", 10, 20)),
        (("A", 10, 0), ("B", 70, 80)),
        (("A", 1000, 1010), ("B", 1020, 1030)),
    ):
        assert TableReview((replace(side, chars=chars),), grid).document.rows == (("A", "B"),)


def test_cross_column_copy_and_export(qapp):
    grid, joined = _cross_column_sample()
    # OCR 坐标是物理像素；DPR 和区域平移不应再次缩放逐字位置。
    image = QImage(200, 60, QImage.Format_RGB32)
    image.setDevicePixelRatio(2)
    raw = OCRResult(QRect(4, 2, 40, 10), joined.text, 0.99, chars=joined.chars)
    token = tokens_from_results((raw,), image)[0]
    assert token.chars == joined.chars
    shifted = _shift(token, 8, 4)
    assert shifted.chars[0] == ("0", 2, 10)

    low = TableReview((replace(joined, score=0.5),), grid).document
    assert all("切开" in _cell_tip(cell) and "置信度" in _cell_tip(cell) for cell in low.cells[0])
    review = TableReview((joined,), grid)
    review.confirm(0, 0)
    review.set_grid(GridSpec((0, 30), (0, 69, 100)))
    assert review.document.rows == (("0001 某单", "位"),)
    assert review.document.pending_count == 2
    documents = [review.document, low]
    review.set_grid(GridSpec((0, 30), (0, 100)))
    assert review.document.rows == (("0001 某单位",),)
    assert review.document.pending_count == 1
    documents.append(review.document)
    confirmed = TableReview((joined,), grid)
    confirmed.confirm(0, 0)
    confirmed.set_grid(grid)
    confirmed.edit(0, 0, "改")
    confirmed.edit(0, 0, "0001")
    assert confirmed.document.cell(0, 0).status == PENDING
    # 置顶按钮会读取便携配置；冒烟只访问临时目录。
    with (
        TemporaryDirectory() as folder,
        patch(
            "settings.get_settings",
            return_value=QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat),
        ),
    ):
        for index, document in enumerate(documents):
            dialog = TableDialog(image)
            active = TableReview((), document.grid)
            active.document = document
            dialog._state.tables = [_TableState(review=active)]
            with patch("table.dialog.show_toast") as toast:
                dialog._copy()
                mime = qapp.clipboard().mimeData()
                assert (
                    tuple(tuple(row) for row in csv.reader(StringIO(mime.text()), delimiter="\t"))
                    == document.rows
                )
                assert mime.html() == export.to_html(document)
                assert "待复核" in toast.call_args.args[0]
            dialog.close()
            qapp.processEvents()
            path = str(Path(folder) / f"split-{index}.xlsx")
            export.save(document, path)
            with zipfile.ZipFile(path) as zipped:
                ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                strings = [
                    "".join(node.itertext())
                    for node in ET.fromstring(zipped.read("xl/sharedStrings.xml"))
                ]
                sheet = ET.fromstring(zipped.read("xl/worksheets/sheet1.xml"))
                values = [
                    strings[int(cell.find("s:v", ns).text)] for cell in sheet.findall(".//s:c", ns)
                ]
                assert values == list(document.rows[0]), values
                assert all(
                    cell.get("t") == "s" and cell.find("s:f", ns) is None
                    for cell in sheet.findall(".//s:c", ns)
                )
                comments = ET.fromstring(zipped.read("xl/comments1.xml"))
                assert len(comments.findall(".//s:comment", ns)) == document.pending_count
                assert "切开" in "".join(comments.itertext())
                if index == 1:
                    assert "置信度" in "".join(comments.itertext())
    qapp.clipboard().clear()


def test_correction_merges():
    grid = GridSpec((0, 40), (0, 40, 80))
    tokens = (Token("A", 0.99, Rect(5, 5, 25, 15)), Token("B", 0.99, Rect(45, 5, 65, 15)))
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
    review = TableReview((tokens[0], Token("B", 0.5, tokens[1].box)), grid)
    review.edit(0, 0, "A corrected")
    review.set_grid(grid.with_merge(CellMerge(0, 0, 1, 2)))
    assert review.confirm(0, 0)
    review.set_grid(grid)
    assert review.document.rows == (("A corrected", "B"),), "确认合并格不得丢失组成文字的原位置"
    assert not review.pending_cells(), "已确认文字拆分后不得重新待复核"
