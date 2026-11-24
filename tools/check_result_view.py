"""离屏合成验收：右侧画布的编辑、选区、缩放和平移中断。"""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFocusEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTableWidgetItem
from styles import apply_theme
from table.result_view import ResultTable, ResultView
from ui import MultilineDelegate


def main():
    app = QApplication.instance() or QApplication([])
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        table = ResultTable()
        table.setItemDelegate(MultilineDelegate(table))
        table.setRowCount(40)
        table.setColumnCount(8)
        for row in range(40):
            for column in range(8):
                table.setItem(row, column, QTableWidgetItem(f"{row},{column}"))
        view = ResultView(table)
        view.resize(900, 600)
        view.show()
        app.processEvents()
        app.processEvents()
        assert view.fitted
        initial_center = view.mapFromScene(view._proxy.boundingRect().center())
        assert (initial_center - view.viewport().rect().center()).manhattanLength() <= 2
        view.set_scale(1)
        anchor = QPoint(300, 250)
        before = view.mapToScene(anchor)
        view.zoom_by(1.25, anchor)
        after = view.mapToScene(anchor)
        assert (before - after).manhattanLength() <= 2
        view.fit()
        assert view.fitted
        assert (view.mapFromScene(view._proxy.boundingRect().center()) - initial_center).manhattanLength() <= 2

        def cell(row, column):
            local = table.visualItemRect(table.item(row, column)).center() + table.viewport().pos()
            return view.mapFromScene(view._proxy.mapToScene(local))

        # 在变换后的真实视口坐标拖选，验证代理没有吞掉表格事件。
        QTest.mousePress(view.viewport(), Qt.LeftButton, pos=cell(0, 0))
        QTest.mouseMove(view.viewport(), cell(2, 2))
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=cell(2, 2))
        assert len(table.selectedIndexes()) == 9, len(table.selectedIndexes())
        def header_point(header, section):
            if header.orientation() == Qt.Horizontal:
                local = QPoint(header.sectionPosition(section) + header.sectionSize(section) // 2, header.height() // 2)
            else:
                local = QPoint(header.width() // 2, header.sectionPosition(section) + header.sectionSize(section) // 2)
            return view.mapFromScene(view._proxy.mapToScene(local + header.pos()))

        for header, first, last, expected in ((table.horizontalHeader(), 1, 3, 120),
                                               (table.verticalHeader(), 1, 3, 24)):
            a, b = header_point(header, first), header_point(header, last)
            QTest.mousePress(view.viewport(), Qt.LeftButton, pos=a)
            QTest.mouseMove(view.viewport(), b)
            QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=b)
            assert len(table.selectedIndexes()) == expected, (mode, header.orientation(), len(table.selectedIndexes()))
        view.set_scale(1)
        table.setCurrentCell(0, 0)
        table.editItem(table.item(0, 0))
        app.processEvents()
        editor = table.findChild(QPlainTextEdit)
        assert editor is not None
        editor.setPlainText("a")
        editor.moveCursor(QTextCursor.End)
        QTest.keyClick(editor, Qt.Key_Space)
        QTest.keyClicks(editor, "b")
        assert editor.toPlainText() == "a b"
        QTest.keyClick(editor, Qt.Key_Return)
        assert table.item(0, 0).text() == "a b"

        view.fit()
        table.setFocus()
        QTest.keyPress(view, Qt.Key_Space)
        assert view._space
        start = cell(1, 1)
        previous = view.mapToScene(view.viewport().rect().center())
        QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), start + QPoint(70, 40))
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=start + QPoint(70, 40))
        assert (view.mapToScene(view.viewport().rect().center()) - previous).manhattanLength() > 20
        QTest.keyRelease(view, Qt.Key_Space)
        assert not view._space and view._pan is None
        view._pan = (QPoint(1, 1), Qt.LeftButton)
        QApplication.sendEvent(view, QFocusEvent(QEvent.FocusOut))
        assert view._pan is None and not view._scroll_timer.isActive()

        # 放大后从屏内格子拖到视口边缘，计时滚动继续扩大选区。
        view.set_scale(1)
        view.centerOn(view._proxy.boundingRect().topLeft())
        start = cell(0, 0)
        QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), QPoint(start.x() + 40, view.viewport().height() - 2))
        previous_scroll = view.verticalScrollBar().value()
        previous_selection = len(table.selectedIndexes())
        for _ in range(6):
            view._drag_scroll()
        assert view.verticalScrollBar().value() > previous_scroll
        assert len(table.selectedIndexes()) > previous_selection
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=view._selection_point)
        assert not view._scroll_timer.isActive()

        view.set_preview_locked(True)
        previous_scale = view.scale
        view.zoom_by(2)
        assert view.scale == previous_scale
        view.set_preview_locked(False)
        view.fit()
        output = Path(__file__).resolve().parents[1] / '.scratch' / 'result-view'
        output.mkdir(parents=True, exist_ok=True)
        view.grab().save(str(output / f'{mode}.png'))
        view.close()
    print("PASS: fitted centering, anchor zoom, cell/row/column drag, edge autoscroll, editing spaces, Space-pan, interruption, preview lock")


if __name__ == "__main__":
    main()
