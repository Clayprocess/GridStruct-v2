"""表格识别窗口：左边对照原图改网格，右边实时看分格结果并改字。

网格任何改动都立即按 OCR 文字框重新分格（不重新识别），人工改过或确认过的格子不会被覆盖。
v1 分成“网格确认”和“结果校对”两个窗口，这里合成一个，改线和改字随时来回切换。
识别文档（图片/PDF 多页）也用这个窗口：每页单独改网格，复制和导出时上下接成一张表。
"""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QDateTime, QEvent, QMargins, QMimeData, QSignalBlocker, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSplitter, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout,
)

from styles import tokens as T
from ui import BaseDialog, MultilineDelegate, show_message, show_toast, tinted_icon
from . import export
from .editor import ADD_HORIZONTAL, ADD_VERTICAL, DELETE, SELECT, GridCanvas
from .grid import STRATEGY_LABELS
from .model import CORRECTED, PENDING
from .pages import ImagePage
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


@dataclass
class _PageState:
    """一页的分析结果与校对进度；翻页时画布重建，这些留着。"""
    review: TableReview | None = None
    suggestion: object = None
    message: str = ""
    stage: str = STAGES["recognizing"]
    error: str = ""
    queued: bool = False
    scheme: int = 0
    cell: tuple = ()
    crop: object = None  # 识别文档裁掉空白页边，翻页渲染后按它裁


class TableDialog(BaseDialog):
    def __init__(self, source, parent=None):
        """source 为一张 QImage（截图/贴图），或识别文档的页面列表（table/pages.py）。"""
        super().__init__(parent)
        self.pages = [ImagePage(source)] if isinstance(source, QImage) else list(source)
        self._states = [_PageState() for _page in self.pages]
        self._index = 0
        self.setWindowTitle("表格识别 · 本地" if len(self.pages) == 1 else "识别文档 · 本地")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(1180, 700 if len(self.pages) == 1 else 760)
        self._mode = QApplication.instance().property("theme_mode") or "light"
        self._canvas = None
        self._syncing = False
        self._fit = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_3, T.SPACE_3, T.SPACE_3, T.SPACE_3)
        layout.setSpacing(T.SPACE_2)
        layout.addLayout(self._build_toolbar())

        self._area = QScrollArea()
        self._area.setObjectName("TableCanvasArea")
        self._area.setAlignment(Qt.AlignCenter)
        self._area.viewport().installEventFilter(self)

        self._table = QTableWidget()
        self._table.setItemDelegate(MultilineDelegate(self._table))
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
        # 翻页会换画布，工具快捷键挂在外层滚动区，焦点在任何一页的画布上都生效。
        for tool, _label, _icon, key in TOOLS:
            QShortcut(QKeySequence(key), self._area, lambda value=tool: self._tool_buttons[value].click(),
                      context=Qt.WidgetWithChildrenShortcut)
        if len(self.pages) > 1:
            QShortcut(QKeySequence("Ctrl+PgUp"), self, lambda: self.show_page(self._index - 1))
            QShortcut(QKeySequence("Ctrl+PgDown"), self, lambda: self.show_page(self._index + 1))
        self.show_page(0)

    @property
    def _state(self):
        return self._states[self._index]

    @property
    def _review(self):
        return self._state.review

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
        if len(self.pages) > 1:
            self._previous_page = self._tool_button("上一页 (Ctrl+PgUp)", "chevron-left", lambda: self.show_page(self._index - 1))
            self._next_page = self._tool_button("下一页 (Ctrl+PgDown)", "chevron-right", lambda: self.show_page(self._index + 1))
            self._page_label = QLabel()
            self._page_label.setAlignment(Qt.AlignCenter)
            self._page_label.setMinimumWidth(56)
            for widget in (self._previous_page, self._page_label, self._next_page, self._separator()):
                row.addWidget(widget)
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
        if len(self.pages) > 1:
            self._remove_headers = QCheckBox("去除重复表头")
            self._remove_headers.setToolTip("仅影响复制和导出：后续页首行与首个已完成页首行相同时去除；无表头数据请勿勾选")
            row.addWidget(self._remove_headers)
            row.addWidget(self._separator())
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
        if len(self.pages) > 1:
            # 页数已知，按页给真实进度。
            self._progress = QProgressBar()
            self._progress.setProperty("class", "slim")
            self._progress.setTextVisible(False)
            self._progress.setFixedWidth(120)
            self._progress.setRange(0, len(self.pages))
            row.addWidget(self._progress)
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

    def set_stage(self, stage, page=None):
        state = self._states[self._index if page is None else page]
        state.stage = STAGES[stage]
        if state is self._state and state.review is None:
            self._status.setText(self._page_prefix() + state.stage)

    def apply_analysis(self, tokens, suggestion, note, rect=None, page=None):
        state = self._states[self._index if page is None else page]
        state.crop = rect
        state.suggestion = suggestion
        state.review = TableReview(tokens, suggestion.selected.grid)
        state.message = suggestion.message if tokens else "未检测到文字，可手动划线后直接导出空表"
        if note and suggestion.selected.strategy != "structure":
            state.message += f"（结构模型：{note}）"
        pending = state.review.pending_cells()
        state.cell = pending[0] if pending else (0, 0)
        if state is self._state:
            if rect is not None:
                self._build_canvas()
            self._activate()
        self._update_progress()

    def show_error(self, message, page=None):
        state = self._states[self._index if page is None else page]
        state.error = message
        if state is self._state:
            self._status.setText(f"{self._page_prefix()}识别失败：{message}")
        self._update_progress()
        if len(self.pages) == 1:
            show_message(self, "表格识别失败", message)

    def take_next_page(self):
        """后台逐页分析：从当前页往后排，翻到哪页哪页先出结果。"""
        order = [*range(self._index, len(self.pages)), *range(self._index)]
        index = next((index for index in order if not self._states[index].queued), None)
        if index is not None:
            self._states[index].queued = True
        return index

    def _set_enabled(self, enabled):
        for widget in (*self._tool_buttons.values(), self._schemes, self._table):
            widget.setEnabled(enabled)
        # 多页时已出结果的页就能先复制、导出，不等其余页。
        ready = any(state.review is not None for state in self._states)
        self._export.setEnabled(ready)
        self._copy_button.setEnabled(ready)
        self._canvas.setEnabled(enabled)
        self._refresh_actions()

    # ---- 翻页 ----

    def _page_prefix(self):
        return f"{self.pages[self._index].label} · " if len(self.pages) > 1 else ""

    def show_page(self, index):
        if not 0 <= index < len(self.pages) or (self._canvas is not None and index == self._index):
            return
        if self._canvas is not None and self._review is not None:
            self._commit_editor()
            self._state.cell = (self._table.currentRow(), self._table.currentColumn())
        self._index = index
        state = self._state
        self._build_canvas()
        if len(self.pages) > 1:
            self._page_label.setText(f"{index + 1} / {len(self.pages)}")
            self._page_label.setToolTip(self.pages[index].label)
            self._previous_page.setEnabled(index > 0)
            self._next_page.setEnabled(index < len(self.pages) - 1)
        if state.review is not None:
            self._activate()
            return
        self._schemes.clear()
        with QSignalBlocker(self._table):
            self._table.clearSpans()
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
        self._set_enabled(False)
        self._status.setText(self._page_prefix() + (f"识别失败：{state.error}" if state.error else state.stage))

    def _build_canvas(self):
        state = self._state
        try:
            image = self.pages[self._index].render()
        except ValueError as exc:
            state.error = state.error or str(exc)
            image = QImage(64, 64, QImage.Format_RGB32)
            image.fill(QColor(T.THEMES[self._mode]["background"]))
        if state.crop is not None:
            image = image.copy(state.crop)
        tool = self._canvas.tool if self._canvas is not None else SELECT
        self._canvas = GridCanvas(image)
        self._canvas.set_tool(tool)
        self._canvas.gridChanged.connect(self._grid_changed)
        self._canvas.selectionChanged.connect(self._refresh_actions)
        self._canvas.cellSelected.connect(self._select_in_table)
        # setWidget 会销毁上一页的画布。
        self._area.setWidget(self._canvas)
        self._fit = True
        QTimer.singleShot(0, self._fit_canvas)

    def _activate(self):
        state = self._state
        self._schemes.clear()
        for candidate in (state.suggestion.selected, *state.suggestion.alternatives):
            self._schemes.addItem(STRATEGY_LABELS[candidate.strategy], candidate.grid)
        if state.suggestion.selected.strategy != "manual":
            # 新画布的初始网格就是整张图一个格子，即“手动划线”的起点。
            self._schemes.addItem(STRATEGY_LABELS["manual"], self._canvas.grid)
        self._schemes.setCurrentIndex(state.scheme)
        self._set_enabled(True)
        self._canvas.set_grid(state.review.document.grid)
        self._refresh()
        row, column = state.cell
        grid = state.review.document.grid
        if row < 0 or row >= grid.row_count or column >= grid.column_count:
            row = column = 0
        self._focus_cell(row, column)

    def _update_progress(self):
        if len(self.pages) == 1:
            return
        finished = sum(state.review is not None or bool(state.error) for state in self._states)
        self._progress.setValue(finished)
        self._progress.setToolTip(f"已分析 {finished} / {len(self.pages)} 页")
        self._progress.setVisible(finished < len(self.pages))
        self._set_enabled(self._review is not None)

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
        self._status.setText(f"{self._page_prefix()}{self._state.message} · {grid.row_count} 行 × {grid.column_count} 列"
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
        self._state.scheme = index
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

    def _output(self):
        """多页时把已分析的页上下接成一张表；还没出结果的页不等。"""
        documents = [state.review.document for state in self._states if state.review is not None]
        return documents[0] if len(self.pages) == 1 else export.stack(documents, self._remove_headers.isChecked())

    def _pending_note(self):
        # 不拦输出：xlsx 会标黄加批注，复制和其他格式只在提示里带上剩余数量。
        count = sum(state.review.document.pending_count for state in self._states if state.review is not None)
        note = f"；还有 {count} 处待复核" if count else ""
        waiting = sum(state.review is None and not state.error for state in self._states)
        failed = sum(bool(state.error) for state in self._states)
        if waiting:
            note += f"；{waiting} 页还在分析，未包含"
        if failed:
            note += f"；{failed} 页识别失败，未包含"
        return note

    def _copy(self):
        if not any(state.review is not None for state in self._states):
            return
        self._commit_editor()
        document = self._output()
        data = QMimeData()
        data.setText(export.to_tsv(document))
        data.setHtml(export.to_html(document))
        QApplication.clipboard().setMimeData(data)
        show_toast("已复制表格，可直接粘贴到 Excel / WPS" + self._pending_note())

    def _export_file(self):
        if not any(state.review is not None for state in self._states):
            return
        self._commit_editor()
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
            export.save(self._output(), path)
        except OSError as exc:
            show_message(self, "导出失败", f"无法写入文件：{path}\n{exc}")
            return
        settings.setValue("table/export_dir", str(Path(path).parent))
        QApplication.clipboard().setText(path)
        show_toast("已导出，文件路径已复制" + self._pending_note())

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
