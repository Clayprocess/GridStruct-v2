"""#70：离屏核对复制表格的 HTML 和 TSV，不代替 Excel / WPS 实机粘贴。"""

import os
import sys
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from table import export
from table.dialog import TableDialog, _TableState
from table.grid import STRUCTURE, Candidate, Suggestion
from table.model import CellMerge, GridSpec, TableCell, TableDocument
from table.review import TableReview


def main():
    app = QApplication([])
    marked = ("1234567890123456", "+123456789012", "  -123,456,789,012  ",
              "000123", "123456789012", "=备注：<核对>")
    unmarked = ("12345678901", "1,200.50", "A&B", "0", "12,34,567,890,123", "1,200")
    rows = (
        (marked[0], "", marked[1], unmarked[0]),
        ("", "", marked[2], unmarked[1]),
        (marked[3], marked[4], marked[5], unmarked[2]),
        (unmarked[3], unmarked[4], unmarked[5], ""),
    )
    axis = tuple(range(0, 201, 50))
    grid = GridSpec(axis, axis, (CellMerge(0, 0, 2, 2),))
    document = TableDocument(grid, tuple(tuple(TableCell(value) for value in row) for row in rows))
    image = QImage(200, 200, QImage.Format_RGB32)
    image.fill(0)
    dialog = TableDialog(image)
    review = TableReview((), grid)
    review.document = document
    suggestion = Suggestion(Candidate(grid, STRUCTURE, 1.0), (), 1.0, "")
    dialog._state.tables = [_TableState(review=review, suggestion=suggestion, cell=(0, 0))]
    dialog._show_table()

    clipboard = app.clipboard()
    clipboard.clear()
    assert dialog._copy_button.isEnabled()
    with patch("table.dialog.show_toast"):
        dialog._copy_button.click()
    mime = clipboard.mimeData()
    assert set(mime.formats()) == {"text/plain", "text/html"}
    assert mime.text() == "".join("\t".join(row) + "\r\n" for row in rows)

    markup = mime.html()
    text_style = r''' style="mso-number-format:'\@'"'''
    assert markup.count(text_style) == len(marked)
    for value in marked:
        assert f"{text_style}>{escape(value)}</td>" in markup, value
    for value in unmarked:
        assert f"<td>{escape(value)}</td>" in markup, value
    assert f'<td rowspan="2" colspan="2"{text_style}>{marked[0]}</td>' in markup

    normal_markup = markup.replace(text_style, "")
    with TemporaryDirectory() as folder:
        for name, tables, expected in (
            ("single", document, normal_markup),
            ("multi", [("甲", document), ("乙", document)], f"<h2>甲</h2>{normal_markup}<h2>乙</h2>{normal_markup}"),
        ):
            path = Path(folder) / f"{name}.html"
            export.save(tables, str(path))
            assert path.read_text(encoding="utf-8").split("<body>", 1)[1] == expected + "</body></html>"

    dialog.close()
    clipboard.clear()
    print("PASS: Copy Table preserves TSV, marks only targeted HTML cells and spans, leaves HTML files unchanged")


if __name__ == "__main__":
    main()
