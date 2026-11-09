"""离屏验收：表格校对被打断或失败时不丢改字、提示不误导。

- 拖网格线时被打断（切工具、翻页、改字），下一次改网格不会从拖动前的旧状态算起、盖掉中间的改字。
- 导出时选保存位置期间后台刚好分析完一页：提示和导出内容按同一时刻算。
- 改了表格外框、重新分析失败：恢复改框前的表格（连同改字），不让这张表消失。

用法：python -X utf8 tools/check_table_interrupt.py。只用合成图和手写分析结果，不跑模型。
"""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication


def result(rect, text):
    from table.analysis import TableResult
    from table.grid import Candidate, Suggestion
    from table.model import GridSpec, Rect, Token
    grid = GridSpec((0, 40), (0, 100))
    token = Token(text, .99, Rect(5, 5, 60, 30))
    return TableResult(rect, (token,), Suggestion(Candidate(grid, "structure", 1), (), 1, "probe"))


def blank():
    image = QImage(400, 200, QImage.Format_RGB32)
    image.fill(QColor("#ffffff"))
    return image


def check_drag_interrupted():
    from table.model import GridSpec, Rect, Token
    from table.review import TableReview
    tokens = (Token("甲", 0.99, Rect(5, 5, 25, 15)), Token("乙", 0.99, Rect(45, 5, 65, 15)),
              Token("丙", 0.99, Rect(5, 25, 25, 35)), Token("丁", 0.99, Rect(45, 25, 65, 35)))
    review = TableReview(tokens, GridSpec((0, 20, 40), (0, 40, 80)))
    # 拖线拖到一半被打断（没有收到松开），接着改字，再拖一次线。
    review.set_grid(GridSpec((0, 20, 40), (0, 38, 80)), final=False)
    review.edit(1, 1, "丁改")
    review.set_grid(GridSpec((0, 20, 40), (0, 36, 80)), final=False)
    review.set_grid(GridSpec((0, 20, 40), (0, 36, 80)))
    assert review.document.rows[1][1] == "丁改", f"打断后的改字被下一次拖线盖掉：{review.document.rows}"
    # 撤销一步只撤掉第二次拖线，改字还在。
    review.undo()
    assert review.document.rows[1][1] == "丁改" and review.document.grid.vertical_lines == (0, 38, 80)


def check_canvas_tool_switch(app):
    from table.editor import GridCanvas, SELECT, ADD_VERTICAL
    from table.model import GridSpec
    canvas = GridCanvas(blank(), None)
    canvas.set_grid(GridSpec((0, 100, 200), (0, 200, 400)))
    changes = []
    canvas.gridChanged.connect(lambda grid, final: changes.append(final))
    canvas.set_tool(SELECT)
    canvas._dragging = ("v", 1)
    canvas.set_tool(ADD_VERTICAL)
    assert changes == [True], "拖线时切工具应按松开提交这次拖动"
    assert canvas._dragging is None
    canvas.set_tool(SELECT)
    assert changes == [True], "没在拖线时切工具不发提交"


def check_export_note_snapshot(app):
    from table.dialog import TableDialog
    from table.pages import ImagePage
    pages = [ImagePage(blank(), label=f"第 {index + 1} 页") for index in range(2)]
    dialog = TableDialog(pages)
    dialog.show()
    dialog.apply_analysis((), (result(QRect(0, 0, 100, 40), "p1"),), page=0)
    app.processEvents()
    with TemporaryDirectory() as folder:
        destination = str(Path(folder) / "out.xlsx")

        def choose(*_args):
            # 用户在选保存位置时，后台把第 2 页分析完了；导出内容已在打开对话框前定下。
            dialog.apply_analysis((), (result(QRect(0, 0, 100, 40), "p2"),), page=1)
            return destination, "Excel 工作簿 (*.xlsx)"

        with patch("table.dialog.QFileDialog.getSaveFileName", side_effect=choose), \
                patch("table.dialog.show_toast") as toast:
            dialog._export_file()
        message = toast.call_args.args[0]
        assert "1 页还在分析" in message, f"导出的是旧快照（不含第 2 页），提示却没说：{message}"
    dialog.close()
    app.processEvents()


def check_regions_failure_restores(app):
    from table.dialog import TableDialog
    dialog = TableDialog(blank())
    dialog.show()
    dialog.apply_analysis((), (result(QRect(0, 0, 100, 40), "original"),))
    app.processEvents()
    dialog._review.edit(0, 0, "人工改过")
    # 把外框挪一下：这张表要按新框重新分析。
    dialog._regions_button.click()
    dialog._canvas.regions[0] = QRect(0, 0, 120, 50)
    dialog._regions_button.click()
    assert dialog._state.regions is not None
    with patch("table.dialog.show_message"):
        dialog.show_error("结构模型出错")
    app.processEvents()
    assert dialog._review is not None, "重新分析失败后这张表直接消失了"
    assert dialog._review.document.rows[0][0] == "人工改过", dialog._review.document.rows
    assert dialog._state.table.rect == QRect(0, 0, 100, 40), "恢复的是改框前的表格和外框"
    assert "重新分析失败" in dialog._status.text()
    # 恢复后可以再改框重试。
    assert dialog._regions_button.isEnabled()
    dialog.close()
    app.processEvents()


def main():
    app = QApplication.instance() or QApplication([])
    from styles import apply_theme
    apply_theme(app, "light")
    with TemporaryDirectory() as folder, \
            patch("settings.storage.data_directory", return_value=Path(folder)):
        try:
            check_drag_interrupted()
            check_canvas_tool_switch(app)
            check_export_note_snapshot(app)
            check_regions_failure_restores(app)
        finally:
            app.aboutToQuit.emit()
    print("PASS: an interrupted line drag never resurrects the pre-drag state over later edits")
    print("PASS: export note is computed from the same snapshot as the exported content")
    print("PASS: failed re-analysis after editing a frame restores the previous table and its edits")


if __name__ == "__main__":
    main()
