"""表格识别窗口：左边对照原图改网格，右边实时看分格结果并改字。

网格任何改动都立即按 OCR 文字框重新分格（不重新识别），人工改过或确认过的格子不会被覆盖。
v1 分成“网格确认”和“结果校对”两个窗口，这里合成一个，改线和改字随时来回切换。
识别文档（图片/PDF 多页）也用这个窗口：每页自动框出有线表格，可在“框选表格”里调整、删除、补框；
每张表单独改网格，导出时一张表一个工作表，跨页续表由用户勾“接上一页”接到上一页最后一张表后面。
"""

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QDateTime, QSize, QTimer, QRect, QMimeData, QPointF, QSignalBlocker, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QScrollArea, QSplitter, QStackedWidget, QStyledItemDelegate,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from styles import tokens as T
from ui import (
    BaseDialog, PreviewZoom, RecognitionModeBar, show_message, show_toast, standard_window, tinted_icon,
)
from . import export
from .editor import ADD_HORIZONTAL, ADD_VERTICAL, DELETE, SELECT, GridCanvas
from .grid import STRATEGY_LABELS
from .model import CORRECTED, PENDING, GridSpec
from .analysis import reading_order
from .pages import FilePage, ImagePage
from .regions import RegionCanvas, region_label
from .review import TableReview
from .amounts import preview_amount
from .workbench import AMOUNT_ROLE, AmountDelegate, FloatingTools, TablePicker, TableTabs
from .result_view import ResultTable, ResultView

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
    scope_suggestions: dict = field(default_factory=dict)
    resized_from: object = None  # 仅直接拖角重分析时保留原表及其校对历史。
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
    restore: list | None = None  # 与 regions 同时有值：改框前的表格，重新分析失败时原样恢复

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

    def __init__(self, source, parent=None, *, text_document=None, initial_mode=None):
        """source 为一张 QImage（截图/贴图），或识别文档的页面列表（table/pages.py）。"""
        super().__init__(parent)
        self.pages = [ImagePage(source)] if isinstance(source, QImage) else list(source)
        self._states = [_PageState() for _page in self.pages]
        self._index = 0
        # 识别文档的工作表按“第N页-表M”命名；截图、贴图只有一张图，按“表M”。
        self._file_document = any(isinstance(page, FilePage) for page in self.pages)
        self._regions_mode = False
        self.entry_document = text_document
        self.mode = initial_mode or ("text" if text_document is not None else "table")
        self.setWindowTitle("识别工作台 · 本地")
        self._text_panes = {}
        self.text_pane = None
        self._table_pane = None
        self._table_dirty = True
        self._split_fraction = .45
        self._source_view = None
        self._shown_once = False
        standard_window(self)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(1600, 900)
        self.setObjectName("TableWorkbench")
        self._amount_preview = {}
        self._mode = QApplication.instance().property("theme_mode") or "light"
        self._canvas = None
        self._syncing = False
        self._syncing_tabs = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_3, T.SPACE_3, T.SPACE_3, T.SPACE_3)
        layout.setSpacing(T.SPACE_2)

        self._mode_bar = RecognitionModeBar(self, initial_mode=self.mode, has_image=bool(self.pages))
        self._mode_bar.modeRequested.connect(self.set_mode)
        layout.addWidget(self._mode_bar)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        self._waiting = QScrollArea()
        self._waiting.setWidgetResizable(True)
        self._wait_label = QLabel()
        self._wait_label.setAlignment(Qt.AlignCenter)
        self._waiting.setWidget(self._wait_label)
        self._stack.addWidget(self._waiting)
        if len(self.pages) > 1:
            QShortcut(QKeySequence("Ctrl+PgUp"), self, lambda: self.show_page(self._index - 1))
            QShortcut(QKeySequence("Ctrl+PgDown"), self, lambda: self.show_page(self._index + 1))
        if self.pages:
            self.show_page(0)
        else:
            self._show_text_page()

    def _build_table_pane(self):
        self._table_pane = QWidget()
        layout = QVBoxLayout(self._table_pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_2)
        self._stack.addWidget(self._table_pane)
        self._area = QScrollArea()
        self._area.setObjectName("TableCanvasArea")
        self._area.setAlignment(Qt.AlignCenter)
        self._zoom = PreviewZoom(self._area, self, self, shortcut=False)

        self._table = ResultTable()
        self._table.setItemDelegate(AmountDelegate(self._table))
        self._table.setWordWrap(True)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                    | QAbstractItemView.AnyKeyPressed)
        self._table.itemChanged.connect(self._item_changed)
        self._table.currentCellChanged.connect(self._table_cell_changed)

        self._result_view = ResultView(self._table)
        self._build_toolbar()
        self._build_table_bar()
        self._build_review_tools()
        self._table.itemSelectionChanged.connect(self._selection_changed)

        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._area)
        self._splitter.addWidget(self._result_view)
        self._area.setMinimumWidth(480)
        self._result_view.setMinimumWidth(400)
        self._splitter.setSizes([720, 880])
        layout.addWidget(self._splitter, 1)
        layout.addLayout(self._build_footer())

        for sequence, slot in ((QKeySequence.Undo, self._undo), (QKeySequence.Redo, self._redo),
                               (QKeySequence("Ctrl+Shift+C"), self._copy), (QKeySequence("Ctrl+Return"), self._confirm)):
            QShortcut(sequence, self._table_pane, slot)
        QShortcut(QKeySequence("Ctrl+0"), self._table_pane, self._fit_focused_view)
        self._cancel_amount_shortcut = QShortcut(QKeySequence("Escape"), self._table_pane, self._cancel_amounts)
        self._cancel_amount_shortcut.setEnabled(False)
        self._next_shortcut = QShortcut(QKeySequence("F8"), self._table_pane, self._next_pending)
        # 翻页会换画布，工具快捷键挂在外层滚动区，焦点在任何一页的画布上都生效。
        for tool, _label, _icon, key in TOOLS:
            QShortcut(QKeySequence(key), self._area, lambda value=tool: self._tool_buttons[value].click(),
                      context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("R"), self._area, self._regions_button.click, context=Qt.WidgetWithChildrenShortcut)

    def _settle_table(self):
        if self._regions_mode:
            self._regions_button.setChecked(False)
        elif isinstance(self._canvas, GridCanvas):
            self._canvas.set_tool(self._canvas.tool)
        self._zoom.stop_pan()
        self._save_position()

    def _view_parts(self):
        if self.mode == "table" and self._canvas is not None:
            return self._splitter, self._zoom, 1.
        if self.mode == "text" and self.text_pane is not None and self.pages:
            return self.text_pane.splitter, self.text_pane.zoom, self._text_dpr
        return None

    def _capture_view(self):
        parts = self._view_parts()
        if parts is not None:
            splitter, zoom, dpr = parts
            sizes = splitter.sizes()
            if sum(sizes):
                self._split_fraction = sizes[0] / sum(sizes)
            self._source_view = zoom.capture_view(dpr)

    def _restore_view(self):
        parts = self._view_parts()
        if parts is not None:
            splitter, zoom, dpr = parts
            width = splitter.width() - splitter.handleWidth()
            left = round(self._split_fraction * width)
            splitter.setSizes([left, width - left])
            zoom.restore_view(self._source_view, dpr)

    def showEvent(self, event):
        super().showEvent(event)
        if not event.spontaneous() and not self._shown_once:
            self._shown_once = True
            self._restore_view()

    def _display_table_pane(self):
        if self._table_pane is None:
            self._build_table_pane()
        if self._table_dirty:
            self._show_table()
            self._table_dirty = False
        self._stack.setCurrentWidget(self._table_pane)
        if len(self.pages) > 1:
            self._page_tools.set_area(self._area)
        self._restore_view()

    def _show_text_page(self, image=None):
        from ocr import OCRDocument, OCRReviewPane
        page = self.pages[self._index] if self.pages else None
        document = self.entry_document if self._index == 0 else None
        pane = self._text_panes.get(self._index)
        if page is not None and document is None and page.results is None:
            state = self._state
            self._show_wait(f"识别失败：{state.error}" if state.error else state.stage)
            return
        pixmap = None
        if page is not None:
            try:
                pixmap = QPixmap.fromImage(image if image is not None else page.render())
            except ValueError as exc:
                self._show_wait(f"无法打开本页原图：{exc}")
                return
            self._text_dpr = pixmap.devicePixelRatio()
        if pane is None:
            if document is None:
                document = OCRDocument(page.results)
            pane = OCRReviewPane(document, pixmap)
            self._text_panes[self._index] = pane
            self._stack.addWidget(pane)
        elif isinstance(page, FilePage):
            pane.set_source_pixmap(pixmap)
        self.text_pane = pane
        self._stack.setCurrentWidget(pane)
        if len(self.pages) > 1:
            self._page_tools.set_area(pane.preview)
        self._restore_view()

    def _show_wait(self, message):
        self.text_pane = None
        self._wait_label.setText(message)
        self._stack.setCurrentWidget(self._waiting)
        if len(self.pages) > 1:
            self._page_tools.set_area(self._waiting)

    def apply_text_results(self, results, page, image):
        """只在正在等文字的那一页创建编辑器，迟到或重试的回调不能覆盖草稿。"""
        self.pages[page].results = results
        if self.mode == "text" and page == self._index and self.text_pane is None:
            self._show_text_page(image)

    def set_mode(self, mode):
        if mode == self.mode or mode not in ("text", "table") or (mode == "table" and not self.pages):
            return
        if self.mode == "table":
            self._settle_table()
        self._capture_view()
        if self.text_pane is not None and self.pages and isinstance(self.pages[self._index], FilePage):
            self.text_pane.set_source_pixmap(None)
        self.mode = mode
        self._mode_bar.set_mode(mode)
        if mode == "text":
            self._show_text_page()
        else:
            self.text_pane = None
            self._display_table_pane()
            if not self._state.queued and not self._state.analyzed:
                self.analysisRequested.emit()

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
        button.setIcon(tinted_icon(icon_name, T.THEMES[self._mode]["table_foreground"]))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setIconSize(QSize(16, 16))
        button.setAutoRaise(True)
        button.setCheckable(checkable)
        button.setProperty("class", "ghost")
        if slot is not None:
            button.clicked.connect(slot)
        return button

    def _label_button(self, text, icon, slot=None, checkable=False, tip=None):
        button = self._tool_button(tip or text, icon, slot, checkable)
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setProperty("labelled", True)
        return button

    def _build_toolbar(self):
        self._source_tools = FloatingTools(self._area)
        self._regions_button = self._label_button(
            "框选表格", "square-dashed", checkable=True,
            tip="在原图上调整、删除或补画表格框；再次点击结束 (R)")
        self._regions_button.toggled.connect(self._toggle_regions)
        self._schemes = QComboBox()
        self._schemes.setObjectName("TableScheme")
        self._schemes.setFixedWidth(128)
        self._schemes.setToolTip("按表格结构、文字行或外框选择网格方案；可撤销")
        self._schemes.activated.connect(self._scheme_chosen)
        self._source_tools.group(self._regions_button, self._schemes)
        self._tools = QButtonGroup(self)
        self._tools.setExclusive(False)
        self._tool_buttons = {}
        for tool, label, icon_name, key in TOOLS:
            text = {SELECT: "选择", ADD_HORIZONTAL: "增加横线", ADD_VERTICAL: "增加竖线", DELETE: "删除线"}[tool]
            button = self._label_button(text, icon_name, checkable=True, tip=f"{label} ({key})；再次点击退出")
            button.clicked.connect(lambda _checked=False, value=tool: self._choose_tool(value))
            self._tools.addButton(button)
            self._tool_buttons[tool] = button
        # 选择是默认状态；不再单独占用一个常驻按钮。
        self._tool_buttons[SELECT].hide()
        self._source_tools.group(*(self._tool_buttons[t] for t in (ADD_HORIZONTAL, ADD_VERTICAL, DELETE)))
        self._merge = self._label_button("合并单元格", "table-cells-merge", lambda: self._canvas.merge_selected())
        self._split = self._label_button("取消合并", "table-cells-split", lambda: self._canvas.split_selected())
        self._source_tools.group(self._merge, self._split)
        self._undo_button = self._label_button("撤销", "undo-2", self._undo, tip="撤销 (Ctrl+Z)")
        self._redo_button = self._label_button("重做", "redo-2", self._redo, tip="重做 (Ctrl+Y)")
        self._source_tools.group(self._undo_button, self._redo_button)
        if len(self.pages) > 1:
            self._page_tools = FloatingTools(self._area, bottom=True)
            self._previous_page = self._tool_button("上一页 (Ctrl+PgUp)", "chevron-left", lambda: self.show_page(self._index - 1))
            self._next_page = self._tool_button("下一页 (Ctrl+PgDown)", "chevron-right", lambda: self.show_page(self._index + 1))
            self._page_input = QLineEdit()
            self._page_input.setObjectName("TablePageInput")
            self._page_input.setAlignment(Qt.AlignCenter)
            self._page_input.setFixedWidth(10 + 8 * len(str(len(self.pages))))
            self._page_input.setFixedHeight(28)
            self._previous_page.setFixedSize(28, 28)
            self._next_page.setFixedSize(28, 28)
            self._page_input.setAccessibleName("页码")
            self._page_input.returnPressed.connect(self._jump_to_page)
            self._page_total = QLabel(f"/ {len(self.pages)}")
            self._page_total.setObjectName("TablePageTotal")
            self._page_tools.group(self._previous_page, self._page_input, self._page_total, self._next_page)
        self._source_tools.place()

    def _fit_focused_view(self):
        focused = QApplication.focusWidget()
        # 代理表格的键盘焦点属于外层视图，两个画布共用一个窗口级快捷键。
        if focused is self._result_view or self._result_view.isAncestorOf(focused):
            self._result_view.fit()
        else:
            self._zoom.fit()

    def _choose_tool(self, tool):
        target = SELECT if self._canvas.tool == tool else tool
        self._canvas.set_tool(target)
        self._sync_tool_buttons()

    def _sync_tool_buttons(self):
        for tool, button in self._tool_buttons.items():
            button.setChecked(isinstance(self._canvas, GridCanvas) and self._canvas.tool == tool)

    def _build_table_bar(self):
        self._tabs = TableTabs()
        self._table_picker = TablePicker(self._result_view, self._tabs, self._result_view._bar)
        self._picker_page = None
        self._tabs.currentChanged.connect(self._select_table)
        self._join = self._label_button("接上一页", "combine", checkable=True,
                                       tip="接在上一页最后一张表后导出，自动去掉相同的重复表头")
        self._join.toggled.connect(self._join_toggled)

    def _build_review_tools(self):
        self._review_tools = FloatingTools(self._result_view)
        self._pending = QLabel()
        self._pending.setObjectName("TablePending")
        self._pending.setToolTip("低置信度或跨列切分的文字；改字或确认后消失")
        self._position = QLabel()
        self._next = self._label_button("下一处", "arrow-right", self._next_pending, tip="下一处待复核 (F8)")
        self._confirm_button = self._label_button("确认并下一处", "circle-check", self._confirm, tip="提交当前修改或确认正确，再跳到下一处 (Ctrl+Enter)")
        self._review_group = self._review_tools.group(self._position, self._pending, self._next, self._confirm_button)
        self._selected_count = QLabel()
        self._amount_button = self._label_button("统一金额", "list-ordered", self._preview_amounts,
                                               tip="自动识别选区金额格式，原表预览；有歧义的值不修改")
        self._amount_group = self._review_tools.group(self._selected_count, self._amount_button)
        self._amount_summary = QLabel()
        self._amount_apply = self._label_button("应用", "check", self._apply_amounts)
        self._amount_cancel = self._label_button("取消", "x", self._cancel_amounts)
        self._amount_preview_group = self._review_tools.group(self._amount_summary, self._amount_apply, self._amount_cancel)
        self._amount_preview_group.hide()
        self._join_group = self._review_tools.group(self._join)
        self._join_group.setVisible(len(self.pages) > 1)
        self._join.setVisible(len(self.pages) > 1)
        self._review_tools.place()

    def _build_footer(self):
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        self._status = QLabel()
        self._status.setObjectName("TableStatus")
        self._status.setMinimumWidth(0)
        row.addWidget(self._status, 1)
        if len(self.pages) > 1:
            self._progress = QProgressBar()
            self._progress.setProperty("class", "slim")
            self._progress.setTextVisible(False)
            self._progress.setFixedWidth(100)
            self._progress.setRange(0, len(self.pages))
            row.addWidget(self._progress)
        self._export = self._label_button("导出", "download", self._export_file)
        self._copy_button = self._label_button("复制表格", "copy", self._copy, tip="复制当前表到 Excel / WPS，保留合并 (Ctrl+Shift+C)")
        for button in (self._export, self._copy_button):
            button.setProperty("class", "outline")
            row.addWidget(button)
        return row

    def _selected_cells(self):
        grid = self._review.document.grid
        return sorted({grid.anchor(index.row(), index.column()) for index in self._table.selectedIndexes()})

    def _selection_changed(self):
        if self._review is None:
            self._selected_count.clear()
            return
        count = len(self._selected_cells())
        self._selected_count.setText(f"已选 {count} 格" if count else "")
        self._amount_button.setEnabled(count > 0 and not self._amount_preview)
        self._review_tools.place()

    def _preview_amounts(self):
        self._canvas.set_tool(SELECT)
        self._sync_tool_buttons()
        self._commit_editor()
        self._amount_preview = {(row, column): preview_amount(self._review.document.cell(row, column).text)
                                for row, column in self._selected_cells()}
        if not self._amount_preview:
            return
        changed = sum(p.status == "changed" for p in self._amount_preview.values())
        ambiguous = sum(p.status == "ambiguous" for p in self._amount_preview.values())
        self._amount_summary.setText(f"{changed} 格可转换" + (f" · {ambiguous} 格待判断" if ambiguous else ""))
        self._amount_apply.setEnabled(changed > 0)
        with QSignalBlocker(self._table):
            for (row, column), preview in self._amount_preview.items():
                item = self._table.item(row, column)
                item.setData(AMOUNT_ROLE, preview)
                item.setToolTip(preview.reason or f"{preview.before} → {preview.after}")
                if preview.status == "changed":
                    self._table.setRowHeight(row, max(self._table.rowHeight(row), 44))
        self._lock_amount_preview(True)
        self._result_view.refresh_geometry()

    def _lock_amount_preview(self, locked):
        self._cancel_amount_shortcut.setEnabled(locked)
        self._review_group.setVisible(not locked)
        self._amount_group.setVisible(not locked)
        self._amount_preview_group.setVisible(locked)
        self._source_tools.setEnabled(not locked)
        self._tabs.setEnabled(not locked)
        self._join.setEnabled(not locked and self._index > 0 and self._state.current == 0)
        self._canvas.setEnabled(not locked)
        self._result_view.set_preview_locked(locked)
        if len(self.pages) > 1:
            self._page_tools.setEnabled(not locked)
        self._copy_button.setEnabled(not locked)
        self._export.setEnabled(not locked)
        self._review_tools.place()

    def _cancel_amounts(self):
        if not self._amount_preview:
            return
        with QSignalBlocker(self._table):
            for row, column in self._amount_preview:
                self._table.item(row, column).setData(AMOUNT_ROLE, None)
        self._amount_preview = {}
        self._lock_amount_preview(False)
        self._refresh()
        self._set_enabled(True)

    def _apply_amounts(self):
        changes = [(r, c, p.after) for (r, c), p in self._amount_preview.items() if p.status == "changed"]
        self._review.edit_many(changes)
        self._cancel_amounts()

    # ---- 分析结果 ----

    def set_stage(self, stage, page=None):
        state = self._states[self._index if page is None else page]
        state.stage = STAGES[stage]
        if self.mode == "text" and state is self._state and self.text_pane is None:
            self._wait_label.setText(state.stage)
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
        keep_view = state.regions is not None and _ready(state.table)
        if self.mode == "table" and index == self._index and not self._regions_mode and not keep_view:
            self._save_position()
        pending = {_key(table.rect): table for table in state.tables if table.review is None}
        results = []
        for result in tables:
            previous = pending.get(_key(result.rect))
            if previous is not None and previous.resized_from is not None:
                table = previous.resized_from
                origin = (result.rect.x(), result.rect.y())
                table.review.replace_source(result.tokens, result.suggestion.selected.grid, origin)
                table.rect = result.rect
                table.suggestion = result.suggestion
                table.scope_suggestions[_key(result.rect)] = result.suggestion
                table.scheme = 0
                results.append(table)
            else:
                results.append(self._table_state(result, bool(tokens)))
        # 改框后只重算了几张表：用户可能正在没动过的表里改字，不能重建界面把输入框销毁。
        if state.regions is None:
            state.tables = results
            state.current = 0
        else:
            # 只重算了改过的框，按顺序填回占位，没改的表连同改字一起保留。
            filled = iter(results)
            state.tables = [next(filled, table) if table.review is None else table for table in state.tables]
            state.regions = state.restore = None
        state.tokens = tokens
        if index == self._index and self.mode == "text":
            self._table_dirty = True
        elif index == self._index and not self._regions_mode:
            if keep_view:
                self._sync_tabs()
            else:
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
        review = TableReview(result.tokens, suggestion.selected.grid, origin=(result.rect.x(), result.rect.y()) if result.rect is not None else (0, 0))
        pending = review.pending_cells()
        table = _TableState(result.rect, review, suggestion, message, cell=pending[0] if pending else (0, 0))
        x, y = review.origin
        table.scope_suggestions[(x, y, suggestion.selected.grid.vertical_lines[-1], suggestion.selected.grid.horizontal_lines[-1])] = suggestion
        return table

    def show_error(self, message, page=None):
        index = self._index if page is None else page
        state = self._states[index]
        if state.regions is not None:
            # 改框后的重新分析失败：整页回到改框前（被挪动的表连同改字一起回来），
            # 没动过的表是同一个对象，失败前正在改的字也在。
            current = state.table
            if current is not None and current.resized_from is not None:
                current = current.resized_from
            keep_view = _ready(current) and any(table is current for table in state.restore)
            state.tables = state.restore
            # 当前表在恢复后的列表里位置可能变了，按对象找回。
            state.current = next((number for number, table in enumerate(state.tables) if table is current), 0)
            state.regions = state.restore = None
            if index == self._index and self.mode == "text":
                self._table_dirty = True
            elif index == self._index and not self._regions_mode:
                if keep_view:
                    # 用户可能正在当前表里改字：只更新标签，不重建界面销毁输入框。
                    self._sync_tabs()
                    self._set_enabled(True)
                else:
                    self._save_position()
                    self._show_table()
                self._status.setText(f"{self._page_prefix()}重新分析失败，已恢复改框前的表格：{message}")
            return
        state.error = message
        if index == self._index:
            self._status.setText(f"{self._page_prefix()}识别失败：{message}")
        self._update_progress()
        if self.mode == "text" and index == self._index and self.text_pane is None:
            self._wait_label.setText(f"识别失败：{message}")
        if len(self.pages) == 1 and self.mode == "table":
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
        self._sync_join_label()
        self._refresh_actions()
        self._amount_button.setEnabled(enabled and bool(self._table.selectedIndexes()) and not self._amount_preview)
        if self._amount_preview:
            self._lock_amount_preview(True)

    def _can_join(self, page):
        """第 page 页的第一张表能否接上一页：紧挨着的上一页已分析完、最后一张表可用。

        上一页识别失败、框全删了或末表还在重算时不能接，免得跨过它接到更早的表上。
        """
        if page == 0:
            return False
        previous = self._states[page - 1]
        return previous.analyzed and previous.regions is None and _ready(previous.tables[-1] if previous.tables else None)

    def _sync_join_label(self):
        current = self._current
        blocked = current is not None and current.joined and self._state.current == 0 and not self._can_join(self._index)
        self._join.setText("接上一页")
        self._join.setToolTip("上一页暂无可接的表，将单独导出" if blocked else "接到上一页最后一张表，导出为同一个工作表")

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
        report = self._status.setText if self.mode == "table" else show_toast
        try:
            number = int(text) if text.isdecimal() else None
        except ValueError:
            number = None
        if number is None:
            report(f"页码无效：请输入 1–{total} 之间的数字")
            return
        if not 1 <= number <= total:
            report(f"没有第 {number} 页：共 {total} 页")
            return
        self.show_page(number - 1)
        if self.mode == "table":
            self._canvas.setFocus()

    def show_page(self, index):
        if not 0 <= index < len(self.pages):
            return
        if index == self._index and (self._canvas is not None or self.text_pane is not None):
            return
        if self.mode == "table" and self._canvas is not None:
            self._settle_table()
            self._capture_view()
        elif self.text_pane is not None:
            self._capture_view()
            if isinstance(self.pages[self._index], FilePage):
                self.text_pane.set_source_pixmap(None)
            self.text_pane = None
        self._index = index
        self._table_dirty = True
        if self.mode == "text":
            self._show_text_page()
        else:
            self._display_table_pane()
        if len(self.pages) > 1:
            self._page_input.setText(str(index + 1))
            self._page_input.setToolTip(f"{self.pages[index].label}；输入页码后回车跳转")
            self._previous_page.setEnabled(index > 0)
            self._next_page.setEnabled(index < len(self.pages) - 1)

    def _save_position(self):
        self._cancel_amounts()
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
        """只改标签文字，表数没变时不增删标签，不打断正在输入的单元格。"""
        state = self._state
        self._syncing_tabs = True
        while self._tabs.count() > len(state.tables):
            self._tabs.removeTab(self._tabs.count() - 1)
        while self._tabs.count() < len(state.tables):
            self._tabs.addTab(tinted_icon("table-2", T.THEMES[self._mode]["table_foreground"]), "")
        for index, table in enumerate(state.tables):
            self._tabs.setTabText(index, region_label(index) + ("" if table.review is not None else " …"))
            self._tabs.setTabToolTip(index, "接在上一页最后一张表后面" if table.joined and index == 0 else "")
        self._tabs.setCurrentIndex(state.current)
        self._syncing_tabs = False
        self._table_picker.refresh(reveal=self._picker_page != self._index)
        self._picker_page = self._index
        self._review_tools.place()

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
            # 始终显示整页（实测决定）：缩放后可以自由看表格以外的内容，当前表以外压暗。
            current = self._current
            canvas = GridCanvas(image, current.rect if current is not None else None)
            canvas.set_tool(self._canvas.tool if isinstance(self._canvas, GridCanvas) else SELECT)
            canvas.toolFinished.connect(self._sync_tool_buttons)
            canvas.boundsChanged.connect(self._resize_table_bounds)
            canvas.gridChanged.connect(self._grid_changed)
            canvas.selectionChanged.connect(self._refresh_actions)
            canvas.cellSelected.connect(self._select_in_table)
        scale = self._canvas.scale if self._canvas is not None else None
        bars = self._area.horizontalScrollBar(), self._area.verticalScrollBar()
        position = [bar.value() for bar in bars]
        self._canvas = canvas
        self._sync_tool_buttons()
        # setWidget 会销毁上一张画布。
        self._zoom.stop_pan()
        self._area.setWidget(canvas)
        self._zoom.watch(canvas)
        if scale is None or self._zoom.fitted:
            self._zoom.fit()
            return
        # 手动缩放过：换表、翻页、进出框选都保持比例和位置，只把当前表挪进视野。
        canvas.set_scale(scale)
        for bar, value in zip(bars, position):
            bar.setValue(value)
        if isinstance(canvas, GridCanvas):
            bounds = canvas.bounds
            self._area.ensureVisible(round(bounds.center().x() * scale), round(bounds.center().y() * scale),
                                     round(bounds.width() * scale / 2), round(bounds.height() * scale / 2))

    def _fill_schemes(self):
        table = self._current
        self._schemes.clear()
        for candidate in (table.suggestion.selected, *table.suggestion.alternatives):
            self._schemes.addItem(tinted_icon("table-2", T.THEMES[self._mode]["table_foreground"]), STRATEGY_LABELS[candidate.strategy], candidate.grid)
        if table.suggestion.selected.strategy != "manual":
            grid = table.review.document.grid
            manual = GridSpec((0, grid.horizontal_lines[-1]), (0, grid.vertical_lines[-1]))
            self._schemes.addItem(tinted_icon("table-2", T.THEMES[self._mode]["table_foreground"]), STRATEGY_LABELS["manual"], manual)
        self._schemes.setCurrentIndex(table.scheme)

    def _activate(self):
        table = self._current
        self._fill_schemes()
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
        self._canvas.finish_drag()
        regions = self._canvas.regions
        self._regions_mode = False
        self._apply_regions(regions)

    def _resize_table_bounds(self, bounds):
        self._save_position()
        regions = [table.rect for table in self._state.tables]
        regions[self._state.current] = bounds
        self._apply_regions(regions, resized=(bounds, self._current))

    def _regions_edited(self):
        self._status.setText(f"{self._page_prefix()}已框 {len(self._canvas.regions)} 张表 · {REGION_HINT}")

    def _apply_regions(self, regions, resized=None):
        """没动过的框保留表格和改字；新框、改过的框重新分析结构（不重跑 OCR）。"""
        state = self._state
        current = state.table
        kept = {_key(table.rect): table for table in state.tables}
        tables, pending = [], []
        for rect in reading_order(regions):
            table = kept.pop(_key(rect), None)
            if table is None:
                table = _TableState(rect)
                if resized is not None and _key(rect) == _key(resized[0]):
                    table.resized_from = resized[1]
                pending.append(rect)
            tables.append(table)
        previous = state.tables
        state.tables = tables
        state.current = next((index for index, table in enumerate(tables) if table is current or table.resized_from is current), 0)
        if pending:
            state.regions = pending
            state.restore = previous
            state.queued = False
            state.stage = STAGES["structure"]
            self.analysisRequested.emit()
        self._show_table()

    def _join_toggled(self, checked):
        if self._current is not None:
            self._current.joined = checked
            self._sync_tabs()
            self._sync_join_label()

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
            pending = QColor(T.THEMES[self._mode]["table_pending"])
            for row, values in enumerate(document.cells):
                for column, cell in enumerate(values):
                    item = table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem()
                        table.setItem(row, column, item)
                    item.setText(cell.text)
                    item.setTextAlignment(Qt.AlignVCenter | (Qt.AlignRight if column > 0 else Qt.AlignLeft))
                    item.setData(Qt.ForegroundRole, QColor(T.THEMES[self._mode]["table_pending_foreground"]) if cell.status == PENDING else None)
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
                source_width = grid.vertical_lines[column + 1] - grid.vertical_lines[column]
                proportional = round(724 * source_width / grid.vertical_lines[-1])
                table.setColumnWidth(column, max(64, min(240, max(proportional, table.columnWidth(column) + T.SPACE_3))))
        table.resizeRowsToContents()
        for row in range(table.rowCount()):
            table.setRowHeight(row, max(32, table.rowHeight(row)))
        self._result_view.refresh_geometry()
        self._syncing = False
        self._status.setText(f"{self._page_prefix()}{self._current.message} · {grid.row_count} 行 × {grid.column_count} 列"
                             + (f" · {len(grid.merges)} 处合并" if grid.merges else ""))
        self._refresh_actions()
        self._selection_changed()

    def _refresh_actions(self):
        review = self._review
        self._merge.setEnabled(review is not None and self._canvas.can_merge)
        self._split.setEnabled(review is not None and self._canvas.can_split)
        self._undo_button.setEnabled(review is not None and review.can_undo)
        self._redo_button.setEnabled(review is not None and review.can_redo)
        count = review.document.pending_count if review is not None else 0
        self._pending.setText(f"待复核 {count} 处")
        self._next_shortcut.setEnabled(count > 0 and not self._amount_preview)
        row, column = self._table.currentRow(), self._table.currentColumn()
        self._position.setText(f"当前 {_column_name(column)}{row + 1}" if row >= 0 else "")
        for widget in (self._pending, self._next, self._confirm_button):
            widget.setVisible(count > 0)
        self._review_tools.place()

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
        origin = self._canvas.bounds.topLeft()
        self._area.ensureVisible(round((origin.x() + rect.center_x) * scale), round((origin.y() + rect.center_y) * scale),
                                 round(rect.width * scale / 2) + 20, round(rect.height * scale / 2) + 20)

    # ---- 操作 ----

    def _scheme_chosen(self, index):
        self._cancel_amounts()
        self._current.scheme = index
        self._canvas.set_grid(self._schemes.itemData(index))
        self._review.set_grid(self._canvas.grid)
        self._refresh()

    def _undo(self):
        if self._amount_preview:
            self._cancel_amounts()
            return
        if self._review is not None and self._review.can_undo:
            self._review.undo()
            self._restore_review_geometry()
            self._refresh()

    def _redo(self):
        if self._amount_preview:
            self._cancel_amounts()
            return
        if self._review is not None and self._review.can_redo:
            self._review.redo()
            self._restore_review_geometry()
            self._refresh()

    def _restore_review_geometry(self):
        grid = self._review.document.grid
        x, y = self._review.origin
        bounds = QRect(x, y, grid.vertical_lines[-1], grid.horizontal_lines[-1])
        self._current.rect = bounds
        suggestion = self._current.scope_suggestions.get(_key(bounds))
        if suggestion is not None:
            self._current.suggestion = suggestion
            candidates = (suggestion.selected, *suggestion.alternatives)
            self._current.scheme = next((i for i, candidate in enumerate(candidates) if candidate.grid == grid), 0)
            self._fill_schemes()
        self._canvas.bounds = bounds
        self._canvas.set_grid(grid)

    def _next_pending(self):
        if self._amount_preview or self._review is None:
            return
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
        self._result_view.focus_table()

    def _commit_editor(self):
        # 快捷键会先于编辑器收键，输出和确认前先保存仍在输入框里的改字。
        editor = self._table.focusWidget()
        if isinstance(editor, QPlainTextEdit) and self._table.isAncestorOf(editor):
            delegate = self._table.itemDelegate()
            delegate.commitData.emit(editor)
            delegate.closeEditor.emit(editor, QStyledItemDelegate.NoHint)
            self._result_view.focus_table()

    def _confirm(self):
        if self._amount_preview:
            return
        self._commit_editor()
        row, column = self._table.currentRow(), self._table.currentColumn()
        if self._review is not None and row >= 0:
            if self._review.confirm(row, column):
                self._refresh()
            if self._review.pending_cells():
                self._next_pending()
            else:
                self._result_view.focus_table()

    def _sheets(self):
        """已分析的表按页序排成工作表：[(名称, 文档, [(页, 表), ...])]。还没出结果的表不等。

        勾了“接上一页”的表接在上一个工作表末尾，与它首行相同的重复表头去掉。
        """
        groups = []
        for page, state in enumerate(self._states):
            for index, table in enumerate(state.tables):
                if table.review is None:
                    continue
                if table.joined and index == 0 and self._can_join(page):
                    # 上一页末表一定在最后一组末尾：页按顺序排，且它本身可用。
                    groups[-1][2].append((page, index))
                    continue
                if self._file_document:
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
        unjoined = [page + 1 for page, state in enumerate(self._states)
                    if state.tables and state.tables[0].joined and not self._can_join(page)]
        if unjoined:
            note += f"；第 {'、'.join(map(str, unjoined))} 页勾了接上一页但上一页没有可接的表，已单独导出"
        return note

    def _copy(self):
        """复制当前这张表；跨页接起来的续表一起复制。"""
        if self._review is None or self._amount_preview:
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
        if self._amount_preview:
            return
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
        # 选保存位置时后台可能又分析完几页；提示必须和上面定下的导出内容同一时刻算。
        note = self._pending_note(sheets)
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
        show_toast(f"已导出{count}" + note,
                   ("复制路径", lambda: QApplication.clipboard().setText(path)))

    # ---- 缩放（ui.zoom 的预览接口）----

    def zoom_scale(self):
        return self._canvas.scale

    def set_zoom_scale(self, scale):
        self._canvas.set_scale(scale)

    def fit_zoom_scale(self, size):
        return self._canvas.fit_scale(size)

    def to_content(self, point):
        return QPointF(self._canvas.mapFrom(self._area.viewport(), point)) / self._canvas.scale

    def from_content(self, point):
        return QPointF(self._canvas.mapTo(self._area.viewport(), (point * self._canvas.scale).toPoint()))

    def pans_at(self, point):
        return self._canvas.pans_at(QPointF(self._canvas.mapFrom(self._area.viewport(), point)))


def _column_name(index):
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell_tip(cell):
    if cell.status == PENDING and cell.split:
        confidence = f"；模型置信度 {cell.weighted_confidence:.0%}" if cell.low_confidence else ""
        return f"待复核：这格文字和相邻格被识别成了一整段，已沿表格线切开{confidence}，核对后改字或点“确认并下一处”"
    if cell.status == PENDING:
        return f"待复核：模型置信度 {cell.weighted_confidence:.0%}，改字或点“确认并下一处”"
    if cell.status == CORRECTED:
        return f"已人工修改；原识别：{cell.original_text or '（空）'}"
    return ""
