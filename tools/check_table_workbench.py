"""合成数据验收：金额预览中断、批量撤销、工具切换和工作台明暗渲染。"""
import os
from dataclasses import replace
import sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidgetSelectionRange
from styles import apply_theme, tokens as T
from table.dialog import TableDialog
from table.pages import ImagePage
from table.analysis import TableResult
from table.grid import Candidate, Suggestion
from table.model import GridSpec, Rect, Token


def sample():
    grid = GridSpec((0, 40, 80, 120), (0, 120, 240, 360))
    data = (('项目', '数量', '金额'), ('纸张', '12O', '1.234,56'), ('签字笔', '2', '1,234'))
    tokens = tuple(Token(value, .6 if value == '12O' else .99,
                         Rect(c * 120 + 8, r * 40 + 8, c * 120 + 110, r * 40 + 32))
                   for r, row in enumerate(data) for c, value in enumerate(row))
    return TableResult(QRect(40, 60, 360, 120), tokens,
                       Suggestion(Candidate(grid, 'structure', 1), (), 1, '合成样本'))


def make_dialog():
    image = QImage(440, 260, QImage.Format_RGB32)
    image.fill(QColor(T.PAPER_BACKGROUND))
    painter = QPainter(image)
    painter.setPen(QColor(T.LIGHT['foreground']))
    painter.setFont(QFont('Arial', 12))
    for token in sample().tokens:
        b = token.box
        painter.drawText(QRect(b.left + 40, b.top + 60, b.width, b.height), Qt.AlignVCenter, token.text.replace('12O', '120'))
    painter.end()
    dialog = TableDialog([ImagePage(image), ImagePage(image)])
    for page in range(2):
        dialog.apply_analysis(sample().tokens, (sample(),), page=page)
    dialog.resize(1600, 900)
    dialog.show()
    app.processEvents()
    return dialog


def check(mode):
    apply_theme(app, mode)
    dialog = make_dialog()
    document = dialog._review.document
    assert (dialog._table.currentRow(), dialog._table.currentColumn()) == (1, 1)
    dialog._result_view.zoom_by(1.25)
    dialog._result_view.focus_table()
    QTest.keyClick(app.focusWidget(), Qt.Key_0, Qt.ControlModifier)
    assert dialog._result_view.fitted, 'Ctrl+0作用于右侧，不与左侧冲突'
    dialog._zoom.zoom_by(1.25)
    dialog._canvas.setFocus()
    QTest.keyClick(dialog._canvas, Qt.Key_0, Qt.ControlModifier)
    assert dialog._zoom.fitted
    dialog._splitter.setSizes([480, 1100])
    app.processEvents()
    assert dialog._source_tools.width() <= dialog._area.viewport().width() - 24
    dialog._splitter.setSizes([1100, 400])
    app.processEvents()
    assert dialog._review_tools.width() <= dialog._result_view.viewport().width() - 24
    dialog._splitter.setSizes([720, 880])
    dialog._table.setRangeSelected(QTableWidgetSelectionRange(1, 2, 2, 2), True)
    dialog._preview_amounts()
    assert dialog._review.document == document
    assert len([v for v in dialog._amount_preview.values() if v.status == 'changed']) == 1
    assert len([v for v in dialog._amount_preview.values() if v.status == 'ambiguous']) == 1
    dialog.show_page(1)
    assert not dialog._amount_preview, '翻页必须丢弃预览而不应用'
    dialog.show_page(0)
    assert dialog._review.document == document
    dialog._table.clearSelection()
    dialog._table.setRangeSelected(QTableWidgetSelectionRange(1, 2, 2, 2), True)
    dialog._preview_amounts()
    app.processEvents()
    output = ROOT / '.scratch' / 'workbench'
    output.mkdir(parents=True, exist_ok=True)
    dialog.grab().save(str(output / f'{mode}-amount.png'))
    dialog._apply_amounts()
    assert dialog._review.document.cell(1, 2).text == '1,234.56'
    assert dialog._review.document.cell(2, 2).text == '1,234'
    dialog._undo()
    assert dialog._review.document == document, '整批转换一次撤销'
    dialog._redo()
    assert dialog._review.document.cell(1, 2).text == '1,234.56'
    changed = dialog._review.document
    dialog._preview_amounts()
    dialog._undo()
    assert not dialog._amount_preview and dialog._review.document == changed, '预览中撤销只取消预览'
    # 角点重分析失败沿用现有恢复路径，不丢改字。
    old_bounds = QRect(dialog._current.rect)
    dialog._canvas.boundsChanged.emit(old_bounds.adjusted(0, 0, 10, 10))
    assert dialog._state.regions and dialog._review is None
    dialog.show_error('synthetic failure')
    assert dialog._review.document == changed and dialog._current.rect == old_bounds
    # 成功重分析也保留人工校对；撤销恢复整个原范围与源文字，而不是只改外框。
    new_bounds = old_bounds.adjusted(-5, -5, 5, 5)
    dialog._canvas.boundsChanged.emit(new_bounds)
    old_result = sample()
    shifted = tuple(replace(t, box=Rect(t.box.left + 5, t.box.top + 5, t.box.right + 5, t.box.bottom + 5)) for t in old_result.tokens)
    new_grid = GridSpec((0, 45, 85, 130), (0, 125, 245, 370))
    new_result = TableResult(new_bounds, shifted, Suggestion(Candidate(new_grid, 'structure', 1), (), 1, '重分析'))
    dialog.apply_analysis(shifted, (new_result,))
    assert dialog._review.document.cell(1, 2).text == changed.cell(1, 2).text
    assert dialog._review.origin == (new_bounds.x(), new_bounds.y())
    dialog._undo()
    assert dialog._review.document == changed and dialog._canvas.bounds == old_bounds
    assert all(dialog._schemes.itemData(i).vertical_lines[-1] == old_bounds.width() for i in range(dialog._schemes.count()))
    dialog._redo()
    assert dialog._canvas.bounds == new_bounds and dialog._review.document.cell(1, 2).text == changed.cell(1, 2).text
    from table.editor import ADD_HORIZONTAL, SELECT
    dialog._tool_buttons[ADD_HORIZONTAL].click()
    assert dialog._canvas.tool == ADD_HORIZONTAL
    dialog._tool_buttons[ADD_HORIZONTAL].click()
    assert dialog._canvas.tool == SELECT
    app.processEvents()
    dialog.grab().save(str(output / f'{mode}.png'))
    dialog.close()
    app.processEvents()


def check_second_table():
    image = QImage(950, 260, QImage.Format_RGB32)
    image.fill(QColor(T.PAPER_BACKGROUND))
    dialog = TableDialog(image)
    first = sample()
    second = replace(first, rect=QRect(500, 60, 360, 120))
    dialog.apply_analysis((), (first, second))
    dialog._select_table(1)
    dialog._review.edit(1, 2, '人工修订')
    bounds = second.rect.adjusted(0, 0, 10, 10)
    dialog._resize_table_bounds(bounds)
    assert dialog._state.current == 1
    grid = GridSpec((0, 40, 80, 130), (0, 120, 240, 370))
    updated = replace(second, rect=bounds, suggestion=Suggestion(Candidate(grid, 'structure', 1), (), 1, '重分析'))
    dialog.apply_analysis((), (updated,))
    assert dialog._state.current == 1 and dialog._review.document.cell(1, 2).text == '人工修订'
    dialog._undo()
    assert dialog._canvas.bounds == second.rect and dialog._schemes.itemData(0).vertical_lines[-1] == 360
    dialog._redo()
    assert dialog._canvas.bounds == bounds and dialog._schemes.itemData(0).vertical_lines[-1] == 370
    dialog._resize_table_bounds(bounds.adjusted(0, 0, 10, 10))
    dialog.show_error('synthetic failure')
    assert dialog._state.current == 1 and dialog._review.document.cell(1, 2).text == '人工修订'
    dialog.close()
    app.processEvents()


def check_table_picker(mode):
    apply_theme(app, mode)
    image = QImage(600, 3000, QImage.Format_RGB32)
    image.fill(QColor(T.PAPER_BACKGROUND))
    dialog = TableDialog([ImagePage(image), ImagePage(image)])
    tables = tuple(replace(sample(), rect=QRect(40, 40 + index * 150, 360, 120)) for index in range(17))
    dialog.apply_analysis((), tables, page=0)
    dialog.apply_analysis((), (sample(),), page=1)
    dialog.resize(920, 900)
    dialog.show()
    app.processEvents()
    picker = dialog._table_picker
    assert picker.isVisible() and dialog._tabs.count() == 17
    assert dialog._review_tools.width() <= dialog._result_view.viewport().width() - 24
    assert not picker.geometry().intersects(dialog._result_view._bar.geometry())
    before = picker.geometry(), picker._back.geometry(), picker._forward.geometry()
    widths = [dialog._tabs.tabRect(index).width() for index in range(17)]
    assert len(set(widths)) == 1
    # 先确认箭头的真实命中区在窗口内，再发送点击，避免QtTest绕过父控件裁切。
    for _ in range(30):
        if not picker._forward.isEnabled():
            break
        button = picker._forward
        assert dialog.childAt(button.mapTo(dialog, button.rect().center())) is button
        QTest.mouseClick(button, Qt.LeftButton)
        app.processEvents()
    assert not picker._forward.isEnabled()
    last = dialog._tabs.tabRect(16)
    assert dialog.childAt(dialog._tabs.mapTo(dialog, last.center())) is dialog._tabs
    QTest.mouseClick(dialog._tabs, Qt.LeftButton, pos=last.center())
    app.processEvents()
    assert dialog._state.current == 16
    assert before == (picker.geometry(), picker._back.geometry(), picker._forward.geometry())
    assert widths == [dialog._tabs.tabRect(index).width() for index in range(17)]
    scroll = picker._scroll.horizontalScrollBar()
    end = scroll.value()
    assert dialog.childAt(picker._back.mapTo(dialog, picker._back.rect().center())) is picker._back
    QTest.mouseClick(picker._back, Qt.LeftButton)
    app.processEvents()
    assert scroll.value() < end and dialog._state.current == 16
    assert before == (picker.geometry(), picker._back.geometry(), picker._forward.geometry())
    dialog._tabs.setFocus()
    for _ in range(16):
        QTest.keyClick(dialog._tabs, Qt.Key_Left)
        app.processEvents()
    assert dialog._state.current == 0 and picker._scroll.horizontalScrollBar().value() == 0
    output = ROOT / '.scratch' / 'workbench-preview'
    output.mkdir(parents=True, exist_ok=True)
    dialog.grab().save(str(output / f'{mode}-many-tables-narrow.png'))
    dialog.resize(1800, 900)
    app.processEvents()
    assert not picker.geometry().intersects(dialog._result_view._bar.geometry())
    dialog.grab().save(str(output / f'{mode}-many-tables-wide.png'))
    dialog.show_page(1)
    assert not picker.isVisible(), '单表不显示表格标签'
    dialog.close()
    app.processEvents()


if __name__ == '__main__':
    app = QApplication([])
    for mode in ('light', 'dark'):
        check(mode)
        check_table_picker(mode)
    check_second_table()
    print('PASS: light/dark amount preview interruption, apply/undo/redo, tool toggle and 17-table narrow/wide navigation')
