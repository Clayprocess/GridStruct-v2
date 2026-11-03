"""表格识别窗口：左边对照原图改网格，右边实时看分格结果并改字。

网格任何改动都立即按 OCR 文字框重新分格（不重新识别），人工改过或确认过的格子不会被覆盖。
v1 分成“网格确认”和“结果校对”两个窗口，这里合成一个，改线和改字随时来回切换。
识别文档（图片/PDF 多页）也用这个窗口：每页自动框出有线表格，可在“框选表格”里调整、删除、补框；
每张表单独改网格，导出时一张表一个工作表，跨页续表由用户勾“接上一页”接到上一页最后一张表后面。
"""

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (
    QDateTime, QEvent, QMargins, QMimeData, QSignalBlocker, QStandardPaths, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSplitter, QStyledItemDelegate,
    QTabBar, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from styles import tokens as T
from ui import BaseDialog, MultilineDelegate, show_message, show_toast, tinted_icon, topmost_button
from . import export
from .editor import ADD_HORIZONTAL, ADD_VERTICAL, DELETE, SELECT, GridCanvas
from .grid import STRATEGY_LABELS
from .model import CORRECTED, PENDING
from .analysis import reading_order
from .pages import FilePage, ImagePage
from .regions import RegionCanvas, region_label
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
MULTI_EXPORT_FILTERS = "Excel 工作簿 (*.xlsx);;网页 (*.html)"
REGION_HINT = "拖动空白处画新框，拖框移动、拖边角改大小，Delete 删除选中的框；改完再点“框选表格”重新分析改过的框"


def _key(rect):
    return None if rect is None else (rect.x(), rect.y(), rect.width(), rect.height())


@dataclass
class _TableState:
    """页里一张表的分析结果与校对进度。rect 为它在整页里的位置；review 为 None 表示还在分析。"""
    rect: object = None
    review: TableReview | None = None
    suggestion: object = None
    message: str = ""
    scheme: int = 0
    cell: tuple = ()
    joined: bool = False  # 接上一页：导出时接在上一页最后一张表后面，同一个工作表


@dataclass
class _PageState:
    """一页的分析结果；翻页时画布重建，这些留着。"""
    tokens: tuple | None = None  # 整页文字框；改框后重新分析只跑结构，不重跑 OCR
    tables: list = field(default_factory=list)
    current: int = 0
    stage: str = STAGES["recognizing"]
    error: str = ""
    queued: bool = False
    regions: list | None = None  # 等着按新框重新分析的区域

    @property
    def table(self):
        return self.tables[self.current] if self.tables else None

    @property
    def analyzed(self):
        return self.tokens is not None


def _ready(table):
    return table is not None and table.review is not None


class TableDialog(BaseDialog):
    # 用户改了表格框，有页面要重新分析；后台服务空闲时取走（table/controller.py）。
    analysisRequested = Signal()

    def __init__(self, source, parent=None):
        """source 为一张 QImage（截图/贴图），或识别文档的页面列表（table/pages.py）。"""
        super().__init__(parent)
        self.pages = [ImagePage(source)] if isinstance(source, QImage) else list(source)
        self._states = [_PageState() for _page in self.pages]
        self._index = 0
        # 识别文档的工作表按“第N页-表M”命名；截图、贴图只有一张图，按“表M”。
        self._document = any(isinstance(page, FilePage) for page in self.pages)
        self._regions_mode = False
        self.setWindowTitle("表格识别 · 本地" if len(self.pages) == 1 else "识别文档 · 本地")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(1180, 700 if len(self.pages) == 1 else 760)
        self._mode = QApplication.instance().property("theme_mode") or "light"
        self._canvas = None
        self._syncing = False
        self._syncing_tabs = False
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

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(T.SPACE_1)
        right_layout.addLayout(self._build_table_bar())
        right_layout.addWidget(self._table, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._area)
        splitter.addWidget(right)
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
        QShortcut(QKeySequence("R"), self._area, self._regions_button.click, context=Qt.WidgetWithChildrenShortcut)
        if len(self.pages) > 1:
            QShortcut(QKeySequence("Ctrl+PgUp"), self, lambda: self.show_page(self._index - 1))
            QShortcut(QKeySequence("Ctrl+PgDown"), self, lambda: self.show_page(self._index + 1))
        self.show_page(0)

    @property
    def _state(self):
        return self._states[self._index]

    @property
    def _current(self):
        return self._state.table

    @property
    def _review(self):
        table = self._current
        return None if table is None or self._regions_mode else table.review

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
            # 输入页码回车跳转；几十页的扫描件不用一页页翻。
            self._page_input = QLineEdit()
            self._page_input.setObjectName("TablePageInput")
            self._page_input.setAlignment(Qt.AlignCenter)
            self._page_input.setFixedWidth(44)
            self._page_input.setToolTip("输入页码后回车跳转")
            self._page_input.setAccessibleName("页码")
            self._page_input.returnPressed.connect(self._jump_to_page)
            self._page_total = QLabel(f"/ {len(self.pages)}")
            for widget in (self._previous_page, self._page_input, self._page_total, self._next_page):
                row.addWidget(widget)
            row.addWidget(self._separator())
        self._regions_button = QToolButton()
        self._regions_button.setText("框选表格")
        self._regions_button.setIcon(tinted_icon("square-dashed", T.THEMES[self._mode]["foreground"]))
        self._regions_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._regions_button.setToolTip("在原图上调整、删除或补画表格框；框外的文字不进表格 (R)")
        self._regions_button.setAutoRaise(True)
        self._regions_button.setCheckable(True)
        self._regions_button.setProperty("class", "ghost")
        self._regions_button.toggled.connect(self._toggle_regions)
        row.addWidget(self._regions_button)
        row.addWidget(self._separator())
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
        row.addWidget(self._separator())
        row.addWidget(topmost_button(self, "window/table_topmost"))
        return row

    def _build_table_bar(self):
        """一张图里各张表的切换；多页文档另有“接上一页”。"""
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        self._tabs = QTabBar()
        self._tabs.setObjectName("TableTabs")
        self._tabs.setDrawBase(False)
        self._tabs.setExpanding(False)
        self._tabs.currentChanged.connect(self._select_table)
        row.addWidget(self._tabs)
        row.addStretch()
        self._join = QCheckBox("接上一页")
        self._join.setToolTip("跨页的续表：接在上一页最后一张表后面，导出到同一个工作表；"
                              "与那张表首行相同的重复表头自动去掉")
        self._join.toggled.connect(self._join_toggled)
        self._join.setVisible(len(self.pages) > 1)
        row.addWidget(self._join)
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
        if state is self._state and not _ready(self._current) and not self._regions_mode:
            self._status.setText(self._page_prefix() + state.stage)

    def page_request(self, index):
        """后台分析这一页要用的 (已有文字框, 框选区域)；首次分析两者都是 None。"""
        state = self._states[index]
        return state.tokens, state.regions

    def apply_analysis(self, tokens, tables, page=None):
        """tables 为 analysis.TableResult 序列：首次分析是自动框出的全部表，改框后是改过的那几张。"""
        index = self._index if page is None else page
        state = self._states[index]
        results = [self._table_state(result, bool(tokens)) for result in tables]
        if state.regions is None:
            state.tables = results
            state.current = 0
        else:
            # 只重算了改过的框，按顺序填回占位，没改的表连同改字一起保留。
            filled = iter(results)
            state.tables = [next(filled, table) if table.review is None else table for table in state.tables]
            state.regions = None
        state.tokens = tokens
        if index == self._index and not self._regions_mode:
            self._show_table()
        self._update_progress()

    @staticmethod
    def _table_state(result, has_text):
        suggestion = result.suggestion
        message = suggestion.message if has_text else "未检测到文字，可手动划线后直接导出空表"
        if result.tokens == () and has_text:
            message = "框里没有识别到文字，可手动划线后直接导出空表"
        if result.note and suggestion.selected.strategy != "structure":
            message += f"（结构模型：{result.note}）"
        review = TableReview(result.tokens, suggestion.selected.grid)
        pending = review.pending_cells()
        return _TableState(result.rect, review, suggestion, message, cell=pending[0] if pending else (0, 0))

    def show_error(self, message, page=None):
        index = self._index if page is None else page
        state = self._states[index]
        if state.regions is not None:
            # 改框后的重新分析失败：撤掉还没结果的框，已有的表不受影响。
            state.tables = [table for table in state.tables if table.review is not None]
            state.current = min(state.current, max(0, len(state.tables) - 1))
            state.regions = None
            if index == self._index and not self._regions_mode:
                self._show_table()
                self._status.setText(f"{self._page_prefix()}重新分析失败：{message}")
            return
        state.error = message
        if index == self._index:
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
        ready = any(_ready(table) for state in self._states for table in state.tables)
        self._export.setEnabled(ready and not self._regions_mode)
        self._copy_button.setEnabled(enabled)
        self._canvas.setEnabled(enabled or self._regions_mode)
        state = self._state
        self._regions_button.setEnabled(state.analyzed and state.regions is None)
        # 框选时切表会重建画布、丢掉没应用的框，先锁住。
        self._tabs.setEnabled(not self._regions_mode)
        current = self._current
        # 只有本页第一张表能接上一页：它上面紧挨着的就是上一页最后一张表。
        with QSignalBlocker(self._join):
            self._join.setChecked(current is not None and current.joined)
        self._join.setEnabled(enabled and self._index > 0 and state.current == 0)
        self._refresh_actions()

    # ---- 翻页与切换表格 ----

    def _page_prefix(self):
        if len(self.pages) == 1 and len(self._state.tables) < 2:
            return ""
        prefix = self.pages[self._index].label if len(self.pages) > 1 else ""
        if len(self._state.tables) > 1:
            prefix += ("·" if prefix else "") + region_label(self._state.current)
        return f"{prefix} · "

    def _jump_to_page(self):
        text = self._page_input.text().strip()
        total = len(self.pages)
        self._page_input.setText(str(self._index + 1))
        if not text.isdigit():
            self._status.setText(f"页码无效：请输入 1–{total} 之间的数字")
            return
        number = int(text)
        if not 1 <= number <= total:
            self._status.setText(f"没有第 {number} 页：共 {total} 页")
            return
        self.show_page(number - 1)
        self._canvas.setFocus()

    def show_page(self, index):
        if not 0 <= index < len(self.pages) or (self._canvas is not None and index == self._index):
            return
        if self._regions_mode:
            self._regions_button.setChecked(False)
        self._save_position()
        self._index = index
        if len(self.pages) > 1:
            self._page_input.setText(str(index + 1))
            self._page_input.setToolTip(f"{self.pages[index].label}；输入页码后回车跳转")
            self._previous_page.setEnabled(index > 0)
            self._next_page.setEnabled(index < len(self.pages) - 1)
        self._show_table()

    def _save_position(self):
        if self._canvas is not None and self._review is not None:
            self._commit_editor()
            self._current.cell = (self._table.currentRow(), self._table.currentColumn())

    def _select_table(self, index):
        if self._syncing_tabs or index < 0 or index == self._state.current:
            return
        self._save_position()
        self._state.current = index
        self._show_table()

    def _sync_tabs(self):
        state = self._state
        self._syncing_tabs = True
        while self._tabs.count():
            self._tabs.removeTab(0)
        for index, table in enumerate(state.tables):
            self._tabs.addTab(region_label(index) + ("" if table.review is not None else " …"))
            if table.joined and index == 0:
                self._tabs.setTabToolTip(index, "接在上一页最后一张表后面")
        self._tabs.setCurrentIndex(state.current)
        self._syncing_tabs = False
        self._tabs.setVisible(len(state.tables) > 1)

    def _show_table(self):
        """按当前页、当前表重建画布和右侧表格。"""
        state = self._state
        self._sync_tabs()
        self._build_canvas()
        if _ready(self._current):
            self._activate()
            return
        self._schemes.clear()
        with QSignalBlocker(self._table):
            self._table.clearSpans()
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
        self._set_enabled(False)
        if state.error:
            status = f"识别失败：{state.error}"
        elif state.analyzed and not state.tables:
            status = "本页没有框选表格；点“框选表格”在原图上画框"
        else:
            status = state.stage
        self._status.setText(self._page_prefix() + status)

    def _build_canvas(self):
        state = self._state
        try:
            image = self.pages[self._index].render()
        except ValueError as exc:
            state.error = state.error or str(exc)
            image = QImage(64, 64, QImage.Format_RGB32)
            image.fill(QColor(T.THEMES[self._mode]["background"]))
        if self._regions_mode:
            canvas = RegionCanvas(image, [table.rect for table in state.tables if table.rect is not None])
        else:
            current = self._current
            if current is not None and current.rect is not None:
                image = image.copy(current.rect)
            canvas = GridCanvas(image)
            canvas.set_tool(self._canvas.tool if isinstance(self._canvas, GridCanvas) else SELECT)
            canvas.gridChanged.connect(self._grid_changed)
            canvas.selectionChanged.connect(self._refresh_actions)
            canvas.cellSelected.connect(self._select_in_table)
        self._canvas = canvas
        # setWidget 会销毁上一张画布。
        self._area.setWidget(canvas)
        self._fit = True
        QTimer.singleShot(0, self._fit_canvas)

    def _activate(self):
        table = self._current
        self._schemes.clear()
        for candidate in (table.suggestion.selected, *table.suggestion.alternatives):
            self._schemes.addItem(STRATEGY_LABELS[candidate.strategy], candidate.grid)
        if table.suggestion.selected.strategy != "manual":
            # 新画布的初始网格就是整张图一个格子，即“手动划线”的起点。
            self._schemes.addItem(STRATEGY_LABELS["manual"], self._canvas.grid)
        self._schemes.setCurrentIndex(table.scheme)
        self._set_enabled(True)
        self._canvas.set_grid(table.review.document.grid)
        self._refresh()
        row, column = table.cell
        grid = table.review.document.grid
        if row < 0 or row >= grid.row_count or column >= grid.column_count:
            row = column = 0
        self._focus_cell(row, column)

    def _update_progress(self):
        if len(self.pages) > 1:
            finished = sum(state.analyzed or bool(state.error) for state in self._states)
            self._progress.setValue(finished)
            self._progress.setToolTip(f"已分析 {finished} / {len(self.pages)} 页")
            self._progress.setVisible(finished < len(self.pages))
        if not self._regions_mode:
            self._set_enabled(self._review is not None)

    # ---- 框选表格 ----

    def _toggle_regions(self, checked):
        if checked:
            self._save_position()
            self._regions_mode = True
            self._build_canvas()
            self._canvas.regionsChanged.connect(self._regions_edited)
            self._canvas.setFocus()
            self._set_enabled(False)
            self._status.setText(self._page_prefix() + REGION_HINT)
            return
        regions = self._canvas.regions
        self._regions_mode = False
        self._apply_regions(regions)

    def _regions_edited(self):
        self._status.setText(f"{self._page_prefix()}已框 {len(self._canvas.regions)} 张表 · {REGION_HINT}")

    def _apply_regions(self, regions):
        """没动过的框保留表格和改字；新框、改过的框重新分析结构（不重跑 OCR）。"""
        state = self._state
        current = state.table
        kept = {_key(table.rect): table for table in state.tables}
        tables, pending = [], []
        for rect in reading_order(regions):
            table = kept.pop(_key(rect), None)
            if table is None:
                table = _TableState(rect)
                pending.append(rect)
            tables.append(table)
        state.tables = tables
        state.current = next((index for index, table in enumerate(tables) if table is current), 0)
        if pending:
            state.regions = pending
            state.queued = False
            state.stage = STAGES["structure"]
            self.analysisRequested.emit()
        self._show_table()

    def _join_toggled(self, checked):
        if self._current is not None:
            self._current.joined = checked
            self._sync_tabs()

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
        self._status.setText(f"{self._page_prefix()}{self._current.message} · {grid.row_count} 行 × {grid.column_count} 列"
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
        if not self._syncing and row >= 0 and self._review is not None:
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
        scale = self._canvas.scale
        self._area.ensureVisible(round(rect.center_x * scale), round(rect.center_y * scale),
                                 round(rect.width * scale / 2) + 20, round(rect.height * scale / 2) + 20)

    # ---- 操作 ----

    def _scheme_chosen(self, index):
        self._current.scheme = index
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

    def _sheets(self):
        """已分析的表按页序排成工作表：[(名称, 文档, [(页, 表), ...])]。还没出结果的表不等。

        勾了“接上一页”的表接在上一个工作表末尾，与它首行相同的重复表头去掉。
        """
        groups = []
        for page, state in enumerate(self._states):
            for index, table in enumerate(state.tables):
                if table.review is None:
                    continue
                if table.joined and index == 0 and groups:
                    groups[-1][2].append((page, index))
                    continue
                if self._document:
                    name = f"第{page + 1}页-{region_label(index)}"
                else:
                    name = region_label(index) if len(state.tables) > 1 else export.SHEET_NAME
                groups.append((name, None, [(page, index)]))
        sheets = []
        for name, _document, members in groups:
            documents = [self._states[page].tables[index].review.document for page, index in members]
            document = documents[0] if len(documents) == 1 else export.stack(documents, True)
            sheets.append((name, document, members))
        return sheets

    def _pending_note(self, sheets, whole=True):
        # 不拦输出：xlsx 会标黄加批注，复制和其他格式只在提示里带上剩余数量。
        count = sum(document.pending_count for _name, document, _members in sheets)
        note = f"；还有 {count} 处待复核" if count else ""
        if not whole:
            return note
        waiting = sum(not state.analyzed and not state.error for state in self._states)
        waiting_tables = sum(table.review is None for state in self._states for table in state.tables)
        failed = sum(bool(state.error) for state in self._states)
        if waiting:
            note += f"；{waiting} 页还在分析，未包含"
        if waiting_tables:
            note += f"；{waiting_tables} 张表还在分析，未包含"
        if failed:
            note += f"；{failed} 页识别失败，未包含"
        return note

    def _copy(self):
        """复制当前这张表；跨页接起来的续表一起复制。"""
        if self._review is None:
            return
        self._commit_editor()
        sheets = self._sheets()
        position = (self._index, self._state.current)
        sheet = next(sheet for sheet in sheets if position in sheet[2])
        name, document, members = sheet
        data = QMimeData()
        data.setText(export.to_tsv(document))
        data.setHtml(export.to_html(document))
        QApplication.clipboard().setMimeData(data)
        what = f"「{name}」" if len(sheets) > 1 or len(members) > 1 else "表格"
        show_toast(f"已复制{what}，可直接粘贴到 Excel / WPS" + self._pending_note([sheet], whole=False))

    def _export_file(self):
        self._commit_editor()
        sheets = self._sheets()
        if not sheets:
            return
        from settings import get_settings
        settings = get_settings()
        directory = settings.value("table/export_dir", "") or QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        filename = f"表格_{QDateTime.currentDateTime().toString('yyyy-MM-dd_HH-mm-ss')}.xlsx"
        # 多张表时不给 CSV：一个 CSV 只装得下一张表。
        filters = EXPORT_FILTERS if len(sheets) == 1 else MULTI_EXPORT_FILTERS
        path, chosen = QFileDialog.getSaveFileName(self, "导出表格", str(Path(directory) / filename), filters)
        if not path:
            return
        if not Path(path).suffix:
            path += "." + chosen.split("*.")[1].rstrip(")")
        try:
            export.save([(name, document) for name, document, _members in sheets], path)
        except ValueError as exc:
            show_message(self, "导出失败", str(exc))
            return
        except OSError as exc:
            show_message(self, "导出失败", f"无法写入文件：{path}\n{exc}")
            return
        settings.setValue("table/export_dir", str(Path(path).parent))
        count = f" {len(sheets)} 张表" if len(sheets) > 1 else ""
        # 同截图保存：不动剪贴板，提示里可点复制路径。
        show_toast(f"已导出{count}" + self._pending_note(sheets),
                   ("复制路径", lambda: QApplication.clipboard().setText(path)))

    # ---- 缩放 ----

    def eventFilter(self, watched, event):
        # Ctrl+滚轮缩放原图，便于对齐细线；不按 Ctrl 时照常滚动。
        if event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self._fit = False
            self._canvas.set_scale(self._canvas.scale * factor)
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
