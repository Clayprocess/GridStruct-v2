"""表格识别窗口：左边对照原图改网格，右边实时看分格结果并改字。

网格任何改动都立即按 OCR 文字框重新分格（不重新识别），人工改过或确认过的格子不会被覆盖。
v1 分成“网格确认”和“结果校对”两个窗口，这里合成一个，改线和改字随时来回切换。
"""

from pathlib import Path

from PySide6.QtCore import QDateTime, QEvent, QMargins, QMimeData, QSignalBlocker, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout,
)

from styles import tokens as T
from ui import BaseDialog, ask_confirmation, show_message, show_toast, tinted_icon
from . import export
from .editor import ADD_HORIZONTAL, ADD_VERTICAL, DELETE, SELECT, GridCanvas
from .grid import STRATEGY_LABELS
from .model import CORRECTED, PENDING
from .review import TableReview

TOOLS = (
    (SELECT, "选择与拖线", "mouse-pointer-2", "S"),
    (ADD_HORIZONTAL, "添加横线（也可 Shift+单击）", "table-rows-split", "H"),
    (ADD_VERTICAL, "添加竖线（也可 Ctrl+单击）", "table-columns-split", "V"),
    (DELETE, "删除线（也可 Alt+单击）", "eraser", "D"),
)
STAGES = {
    "loading": "首次加载本地文字模型…",
    "recognizing": "正在识别文字…",
    "structure": "正在分析表格结构…",
}
EXPORT_FILTERS = "Excel 工作簿 (*.xlsx);;CSV (*.csv);;网页 (*.html)"


class _CellDelegate(QStyledItemDelegate):
    """同 Excel：Enter 提交，Alt+Enter 在格内换行。"""

    def createEditor(self, parent, option, index):
        editor = QPlainTextEdit(parent)
        editor.setTabChangesFocus(True)
        editor.installEventFilter(self)
        return editor

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(Qt.EditRole) or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect
        rect.setHeight(max(rect.height(), editor.fontMetrics().lineSpacing() * 3))
        editor.setGeometry(rect)

    def eventFilter(self, editor, event):
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.AltModifier:
                editor.insertPlainText("\n")
            else:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QStyledItemDelegate.EditNextItem)
            return True
        return super().eventFilter(editor, event)


class TableDialog(BaseDialog):
    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.setWindowTitle("表格识别 · 本地")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(1180, 700)
        self._mode = QApplication.instance().property("theme_mode") or "light"
        self._review = None
        self._suggestion = None
        self._syncing = False
        self._fit = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_3, T.SPACE_3, T.SPACE_3, T.SPACE_3)
        layout.setSpacing(T.SPACE_2)
        layout.addLayout(self._build_toolbar())

        self._canvas = GridCanvas(image)
        self._canvas.gridChanged.connect(self._grid_changed)
        self._canvas.selectionChanged.connect(self._refresh_actions)
        self._canvas.cellSelected.connect(self._select_in_table)
        self._area = QScrollArea()
        self._area.setObjectName("TableCanvasArea")
        self._area.setAlignment(Qt.AlignCenter)
        self._area.setWidget(self._canvas)
        self._area.viewport().installEventFilter(self)

        self._table = QTableWidget()
        self._table.setItemDelegate(_CellDelegate(self._table))
        self._table.setWordWrap(True)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                    | QAbstractItemView.AnyKeyPressed)
        self._table.itemChanged.connect(self._item_changed)
        self._table.currentCellChanged.connect(self._table_cell_changed)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._area)
        splitter.addWidget(self._table)
        splitter.setSizes([640, 520])
        layout.addWidget(splitter, 1)
        layout.addLayout(self._build_footer())

        for sequence, slot in ((QKeySequence.Undo, self._undo), (QKeySequence.Redo, self._redo),
                               (QKeySequence("Ctrl+Shift+C"), self._copy), (QKeySequence("Ctrl+Return"), self._confirm)):
            QShortcut(sequence, self, slot)
        self._next_shortcut = QShortcut(QKeySequence("F8"), self, self._next_pending)
        for tool, _label, _icon, key in TOOLS:
            QShortcut(QKeySequence(key), self._canvas, lambda value=tool: self._tool_buttons[value].click(),
                      context=Qt.WidgetShortcut)
        self._set_enabled(False)
        self.set_stage("recognizing")

    # ---- 构造 ----

    def _tool_button(self, label, icon_name, slot=None, checkable=False):
        button = QToolButton()
        button.setIcon(tinted_icon(icon_name, T.THEMES[self._mode]["foreground"]))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setAutoRaise(True)
        button.setCheckable(checkable)
        button.setProperty("class", "ghost")
        if slot is not None:
            button.clicked.connect(slot)
        return button

    @staticmethod
    def _separator():
        line = QFrame()
        line.setObjectName("TableToolSeparator")
        line.setFrameShape(QFrame.VLine)
        return line

    def _build_toolbar(self):
        row = QHBoxLayout()
        row.setSpacing(2)
        self._tools = QButtonGroup(self)
        self._tool_buttons = {}
        for tool, label, icon_name, key in TOOLS:
            button = self._tool_button(f"{label} ({key})", icon_name, checkable=True)
            button.clicked.connect(lambda _checked=False, value=tool: self._canvas.set_tool(value))
            self._tools.addButton(button)
            self._tool_buttons[tool] = button
            row.addWidget(button)
        self._tool_buttons[SELECT].setChecked(True)
        row.addWidget(self._separator())
        self._merge = self._tool_button("合并选中格子", "table-cells-merge", lambda: self._canvas.merge_selected())
        self._split = self._tool_button("取消合并", "table-cells-split", lambda: self._canvas.split_selected())
        row.addWidget(self._merge)
        row.addWidget(self._split)
        row.addWidget(self._separator())
        self._undo_button = self._tool_button("撤销 (Ctrl+Z)", "undo-2", self._undo)
        self._redo_button = self._tool_button("重做 (Ctrl+Y)", "redo-2", self._redo)
        row.addWidget(self._undo_button)
        row.addWidget(self._redo_button)
        row.addStretch()
        row.addWidget(QLabel("网格方案"))
        self._schemes = QComboBox()
        self._schemes.setMinimumWidth(140)
        self._schemes.setToolTip("自动分析给出的候选网格；切换后可撤销")
        self._schemes.activated.connect(self._scheme_chosen)
        row.addWidget(self._schemes)
        return row

    def _build_footer(self):
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        self._status = QLabel()
        self._status.setObjectName("TableStatus")
        row.addWidget(self._status, 1)
        self._pending = QLabel()
        self._pending.setObjectName("TablePending")
        self._pending.setToolTip("文字模型置信度较低的格子；改字或确认后消失，导出 xlsx 时标黄并加批注")
        self._position = QLabel()
        row.addWidget(self._position)
        row.addWidget(self._pending)
        self._next = QPushButton("下一处")
        self._next.setProperty("class", "ghost")
        self._next.setToolTip("跳到下一处待复核格子 (F8)")
        self._next.clicked.connect(self._next_pending)
        self._confirm_button = QPushButton("确认并下一处")
        self._confirm_button.setProperty("class", "ghost")
        self._confirm_button.setToolTip("提交当前修改或确认识别正确，再跳到下一处 (Ctrl+Enter)")
        self._confirm_button.clicked.connect(self._confirm)
        row.addWidget(self._next)
        row.addWidget(self._confirm_button)
        row.addSpacing(T.SPACE_2)
        self._export = QPushButton("导出…")
        self._export.setProperty("class", "outline")
        self._export.clicked.connect(self._export_file)
        self._copy_button = QPushButton("复制表格")
        self._copy_button.setProperty("class", "primary")
        self._copy_button.setToolTip("粘贴到 Excel / WPS 时保留合并单元格 (Ctrl+Shift+C)")
        self._copy_button.clicked.connect(self._copy)
        row.addWidget(self._export)
        row.addWidget(self._copy_button)
        return row

    # ---- 分析结果 ----

    def set_stage(self, stage):
        self._status.setText(STAGES[stage])

    def apply_analysis(self, tokens, suggestion, note):
        self._suggestion = suggestion
        self._review = TableReview(tokens, suggestion.selected.grid)
        self._schemes.clear()
        for candidate in (suggestion.selected, *suggestion.alternatives):
            self._schemes.addItem(STRATEGY_LABELS[candidate.strategy], candidate.grid)
        if suggestion.selected.strategy != "manual":
            self._schemes.addItem(STRATEGY_LABELS["manual"], self._canvas.grid)
        self._base_message = suggestion.message if tokens else "未检测到文字，可手动划线后直接导出空表"
        if note and suggestion.selected.strategy != "structure":
            self._base_message += f"（结构模型：{note}）"
        self._set_enabled(True)
        self._canvas.set_grid(self._review.document.grid)
        self._refresh()
        target = self._review.pending_cells()[0] if self._review.pending_cells() else (0, 0)
        self._focus_cell(*target)

    def show_error(self, message):
        self._status.setText(f"识别失败：{message}")
        show_message(self, "表格识别失败", message)

    def _set_enabled(self, enabled):
        for widget in (*self._tool_buttons.values(), self._schemes, self._table, self._export, self._copy_button):
            widget.setEnabled(enabled)
        self._canvas.setEnabled(enabled)
        self._refresh_actions()

    # ---- 状态同步 ----

    def _grid_changed(self, grid, final):
        self._review.set_grid(grid, final=final)
        self._refresh()

    def _refresh(self):
        document = self._review.document
        grid = document.grid
        table = self._table
        self._syncing = True
        with QSignalBlocker(table):
            current = (table.currentRow(), table.currentColumn())
            table.clearSpans()
            reshaped = (table.rowCount(), table.columnCount()) != (grid.row_count, grid.column_count)
            table.setRowCount(grid.row_count)
            table.setColumnCount(grid.column_count)
            table.setHorizontalHeaderLabels([_column_name(index) for index in range(grid.column_count)])
            pending = QColor(T.THEMES[self._mode]["pending"])
            for row, values in enumerate(document.cells):
                for column, cell in enumerate(values):
                    item = table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem()
                        table.setItem(row, column, item)
                    item.setText(cell.text)
                    covered = grid.is_covered(row, column)
                    item.setFlags(Qt.NoItemFlags if covered else Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    item.setData(Qt.BackgroundRole, pending if cell.status == PENDING else None)
                    item.setToolTip(_cell_tip(cell))
            for merge in grid.merges:
                table.setSpan(merge.top, merge.left, merge.bottom - merge.top, merge.right - merge.left)
            if 0 <= current[0] < grid.row_count and 0 <= current[1] < grid.column_count:
                table.setCurrentCell(*grid.anchor(*current))
        if reshaped:
            # 只在行列数变化时重排列宽，拖线和改字时列宽不跳。
            table.resizeColumnsToContents()
            for column in range(grid.column_count):
                table.setColumnWidth(column, max(64, min(240, table.columnWidth(column) + T.SPACE_3)))
        table.resizeRowsToContents()
        self._syncing = False
        self._status.setText(f"{self._base_message} · {grid.row_count} 行 × {grid.column_count} 列"
                             + (f" · {len(grid.merges)} 处合并" if grid.merges else ""))
        self._refresh_actions()

    def _refresh_actions(self):
        review = self._review
        self._merge.setEnabled(review is not None and self._canvas.can_merge)
        self._split.setEnabled(review is not None and self._canvas.can_split)
        self._undo_button.setEnabled(review is not None and review.can_undo)
        self._redo_button.setEnabled(review is not None and review.can_redo)
        count = review.document.pending_count if review is not None else 0
        self._pending.setText(f"待复核 {count} 处")
        self._next_shortcut.setEnabled(count > 0)
        row, column = self._table.currentRow(), self._table.currentColumn()
        self._position.setText(f"当前 {_column_name(column)}{row + 1}" if row >= 0 else "")
        for widget in (self._pending, self._next, self._confirm_button):
            widget.setVisible(count > 0)

    def _item_changed(self, item):
        if self._syncing:
            return
        self._review.edit(item.row(), item.column(), item.text())
        self._refresh()

    def _table_cell_changed(self, row, column, _previous_row, _previous_column):
        if not self._syncing and row >= 0:
            self._canvas.select_cell(row, column)
            self._reveal(row, column)
            self._refresh_actions()

    def _select_in_table(self, row, column):
        self._syncing = True
        self._table.setCurrentCell(row, column)
        self._syncing = False
        self._refresh_actions()

    def _reveal(self, row, column):
        rect = self._review.document.grid.cell_rect(row, column)
        scale = self._canvas.width() / self._canvas.grid.vertical_lines[-1]
        self._area.ensureVisible(round(rect.center_x * scale), round(rect.center_y * scale),
                                 round(rect.width * scale / 2) + 20, round(rect.height * scale / 2) + 20)

    # ---- 操作 ----

    def _scheme_chosen(self, index):
        self._canvas.set_grid(self._schemes.itemData(index))
        self._review.set_grid(self._canvas.grid)
        self._refresh()

    def _undo(self):
        if self._review is not None and self._review.can_undo:
            self._review.undo()
            self._canvas.set_grid(self._review.document.grid)
            self._refresh()

    def _redo(self):
        if self._review is not None and self._review.can_redo:
            self._review.redo()
            self._canvas.set_grid(self._review.document.grid)
            self._refresh()

    def _next_pending(self):
        self._commit_editor()
        cells = self._review.pending_cells()
        if not cells:
            return
        current = (self._table.currentRow(), self._table.currentColumn())
        target = next((cell for cell in cells if cell > current), cells[0])
        self._focus_cell(*target)

    def _focus_cell(self, row, column):
        self._table.setCurrentCell(row, column)
        self._table.scrollToItem(self._table.currentItem())
        self._table.setFocus()

    def _commit_editor(self):
        # 快捷键会先于编辑器收键，输出和确认前先保存仍在输入框里的改字。
        editor = QApplication.focusWidget()
        if isinstance(editor, QPlainTextEdit) and self._table.isAncestorOf(editor):
            delegate = self._table.itemDelegate()
            delegate.commitData.emit(editor)
            delegate.closeEditor.emit(editor, QStyledItemDelegate.NoHint)
            self._table.setFocus()

    def _confirm(self):
        self._commit_editor()
        row, column = self._table.currentRow(), self._table.currentColumn()
        if self._review is not None and row >= 0:
            if self._review.confirm(row, column):
                self._refresh()
            if self._review.pending_cells():
                self._next_pending()
            else:
                self._table.setFocus()

    def _allow_output(self, action):
        self._commit_editor()
        count = self._review.document.pending_count
        if not count:
            return True
        accepted = ask_confirmation(self, "仍有待复核单元格",
                                    f"还有 {count} 处文字尚未复核。可返回校对，也可以保留待复核状态继续{action}。",
                                    confirm_text=f"仍然{action}", cancel_text="返回校对")
        if not accepted:
            self.activateWindow()
            self._focus_cell(*self._review.pending_cells()[0])
        return accepted

    def _copy(self):
        if self._review is None:
            return
        if not self._allow_output("复制"):
            return
        data = QMimeData()
        data.setText(export.to_tsv(self._review.document))
        data.setHtml(export.to_html(self._review.document))
        QApplication.clipboard().setMimeData(data)
        show_toast("已复制表格，可直接粘贴到 Excel / WPS")

    def _export_file(self):
        if not self._allow_output("导出"):
            return
        from settings import get_settings
        settings = get_settings()
        directory = settings.value("table/export_dir", "") or QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        name = f"表格_{QDateTime.currentDateTime().toString('yyyy-MM-dd_HH-mm-ss')}.xlsx"
        path, chosen = QFileDialog.getSaveFileName(self, "导出表格", str(Path(directory) / name), EXPORT_FILTERS)
        if not path:
            return
        if not Path(path).suffix:
            path += "." + chosen.split("*.")[1].rstrip(")")
        try:
            export.save(self._review.document, path)
        except OSError as exc:
            show_message(self, "导出失败", f"无法写入文件：{path}\n{exc}")
            return
        settings.setValue("table/export_dir", str(Path(path).parent))
        QApplication.clipboard().setText(path)
        show_toast("已导出，文件路径已复制")

    # ---- 缩放 ----

    def eventFilter(self, watched, event):
        # Ctrl+滚轮缩放原图，便于对齐细线；不按 Ctrl 时照常滚动。
        if event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self._fit = False
            self._canvas.set_scale(self._canvas.width() / self._canvas.grid.vertical_lines[-1] * factor)
            return True
        if event.type() == QEvent.Resize and self._fit:
            QTimer.singleShot(0, self._fit_canvas)
        return super().eventFilter(watched, event)

    def _fit_canvas(self):
        size = self._area.viewport().size().shrunkBy(QMargins(T.SPACE_2, T.SPACE_2, T.SPACE_2, T.SPACE_2))
        self._canvas.set_scale(self._canvas.fit_scale(size))



def _column_name(index):
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell_tip(cell):
    if cell.status == PENDING:
        return f"待复核：模型置信度 {cell.weighted_confidence:.0%}，改字或点“确认并下一处”"
    if cell.status == CORRECTED:
        return f"已人工修改；原识别：{cell.original_text or '（空）'}"
    return ""
