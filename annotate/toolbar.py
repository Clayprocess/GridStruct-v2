# annotate/toolbar.py
# FloatPanel 浮动工具条：截图窗口的标注工具切换 + 操作按钮。
#
# 布局（同 PixPin 3.5.5.1 默认工具条，从左到右）：
#   [几何▾ 画笔▾ 线条▾ 文字 序号 马赛克 橡皮擦] | [撤销] | [长截图 识别 贴图 保存 关闭 复制] [更多]
#
# 约束（AGENTS.md）：
# - 样式全部走 styles/theme.py，本文件不写任何 setStyleSheet；
# - 颜色全部走 styles/tokens.py；
# - 图标都在 assets/icons/，经 ui/icons.py 加载；Lucide 没有的几个按同样线宽自绘，见该目录 README。

import os as _os
import sys as _sys

# 直接运行时（python annotate/toolbar.py）把项目根目录加入路径；
# 作为模块导入时此操作无害（路径已存在则重复）。
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QVBoxLayout, QMenu, QToolButton, QWidget, QLabel

from styles import tokens as T
from settings import get_settings
from ui import floating_window_type, tinted_icon
from ui.motion import SelectionPill, animation

# 主区按 PixPin 3.5.5.1 默认工具条排列：同类工具折成一个按钮，右侧小箭头切换，按钮图标跟随所选。
# 马赛克的像素化/模糊是样式不是工具，留在二级面板里切换。
TOOL_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("geo", [("rect", "矩形", "square"), ("ellipse", "椭圆", "circle")]),
    ("pen", [("pencil", "画笔", "pencil"), ("highlight", "荧光笔", "highlighter")]),
    ("lines", [("arrow", "箭头", "arrow-up-right"), ("line", "直线", "slash"), ("polyline", "折线", "polyline")]),
    ("text", [("text", "文字", "type")]),
    ("serial", [("serial", "序号", "circle-number")]),
    ("mosaic", [("mosaic", "马赛克", "checkerboard")]),
    ("eraser", [("eraser", "橡皮擦", "eraser")]),
]
_GROUP_OF = {tool[0]: group for group, tools in TOOL_GROUPS for tool in tools}
_TOOL_INFO = {tool[0]: tool[1:] for _group, tools in TOOL_GROUPS for tool in tools}
# 合并按钮不改快捷键：Shift+1～9 仍是 PixPin 文档的几何、折线、箭头、序号、铅笔、荧光笔、马赛克、文字、橡皮擦。
DIGIT_TOOLS = ("geo", "polyline", "arrow", "serial", "pencil", "highlight", "mosaic", "text", "eraser")
# “更多”菜单：低频工具和操作，同 PixPin 放在最右。
MORE_TOOLS: list[tuple[str, str, str]] = [
    ("magnifier", "放大镜", "zoom-in"),
]
MORE_ACTIONS: list[tuple[str, str, str]] = [
    ("pipette", "取色", "pipette"),
    ("redo", "重做", "redo-2"),
]


def shortcut_tool(event):
    """Shift+数字对应的工具 id，没有则返回 None。"""
    if event.modifiers() != Qt.ShiftModifier:
        return None
    # Shift 让数字键产生的字符随键盘布局变化；Windows 虚拟键码 0x31～0x39 与布局无关，优先用它。
    vk, key = event.nativeVirtualKey(), event.key()
    if 0x31 <= vk <= 0x39:
        return DIGIT_TOOLS[vk - 0x31]
    index = "123456789!@#$%^&*(".find(chr(key)) if key < 0x110000 else -1
    return DIGIT_TOOLS[index % 9] if index >= 0 else None

# 操作 id -> (中文名, Lucide 图标名)。撤销单独一组，其余是输出。
UNDO_ACTION = ("undo", "撤销", "undo-2")
ACTIONS: list[tuple[str, str, str]] = [
    ("longshot", "长截图", "longshot"),
    ("ocr", "文字识别", "scan-text"),
    ("pin", "贴图", "pin"),
    ("save", "保存", "save"),
]

# 同 PixPin 没有单独的“确认”：最右的复制就是完成，与 Enter 同效；关闭紧挨在它左边。
CONFIRM_ID = "confirm"
CANCEL_ID = "cancel"
_END_BUTTONS: list[tuple[str, str, str]] = [
    (CANCEL_ID, "关闭", "x"),
    (CONFIRM_ID, "复制", "copy"),
]
_SHORTCUTS = {"undo": "Ctrl+Z", "redo": "Ctrl+Y", "save": "Ctrl+S", "ocr": "Shift+O", "longshot": "Ctrl+Alt+L",
              CONFIRM_ID: "Ctrl+C / Enter", CANCEL_ID: "Esc"}
_SHORTCUTS.update({tool: f"Shift+{i + 1}" for i, tool in enumerate(DIGIT_TOOLS) if tool in _TOOL_INFO})
_SHORTCUTS["rect"] = _SHORTCUTS["ellipse"] = "Shift+1"


def _tip(item_id, label):
    return f"{label} ({_SHORTCUTS[item_id]})" if item_id in _SHORTCUTS else label


def _menu_text(item_id, label):
    # 制表符后的文字是 QMenu 右侧的快捷键列，只作提示，按键仍由截图窗口统一分发。
    return f"{label}\t{_SHORTCUTS[item_id]}" if item_id in _SHORTCUTS else label

_ICON_SIZE = 16
_CARET_SIZE = 12
_GAP = 8  # 工具条与选区之间的间距
# 主工具条与二级面板是两张独立卡片，中间留缝（同 PixPin：二级面板在主条下方、左缘对齐、宽度随工具变化）。
_CARD_GAP = 6


class FloatPanel(QWidget):
    """无边框浮动工具条（截图窗口用）。"""

    toolSelected = Signal(str)     # 标注工具被选中 -> 工具 id
    actionTriggered = Signal(str)  # 操作按钮被点击 -> 操作 id（含 confirm / cancel）

    def __init__(self, mode: str = "light", parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | floating_window_type() | Qt.WindowStaysOnTopHint,
        )
        self.setObjectName("FloatPanel")
        self.setWindowTitle("GridStruct-V2 标注工具条")
        # 圆角卡片需要透明背景，否则四角会被填成方形
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._mode = "light"
        self._fade = animation(self, b"windowOpacity")
        self._slide = animation(self, b"pos")
        self._buttons: dict[str, QToolButton] = {}
        # 按钮 id -> 当前图标名；组按钮的图标会随所选工具变化。
        self._icon_names: dict[str, str] = {}
        self._carets: list[QToolButton] = []
        # 菜单项 -> 图标名，换主题时一起重染。
        self._menu_icons: list[tuple[object, str]] = []

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(8, 6, 8, 10)
        self._root_layout.setSpacing(_CARD_GAP)
        self._options = None
        self._options_above = False
        self._button_container = QWidget(self)
        # 主条比二级面板窄时容器左侧留白，全局 QWidget 背景会把这段留白涂成方块。
        self._button_container.setObjectName("FloatPanelRow")
        self._button_layout = QHBoxLayout(self._button_container)
        self._button_layout.setContentsMargins(0, 0, 0, 0)
        self._button_layout.setSpacing(0)
        buttons = QWidget(self._button_container)
        self._button_row = buttons
        self._button_layout.addWidget(buttons, 0, Qt.AlignLeft)
        self._button_layout.addStretch()
        self._root_layout.addWidget(self._button_container)
        layout = QHBoxLayout(buttons)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        # 标注工具：单选互斥。每组一个按钮，记住组内上次用的工具。选中底块在按钮间滑动。
        self._pill = SelectionPill(buttons, "secondary", "border")
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        settings = get_settings()
        self._variant: dict[str, str] = {}
        for group, tools in TOOL_GROUPS:
            saved = settings.value(f"annotation/{group}_tool", tools[0][0])
            self._variant[group] = saved if _GROUP_OF.get(saved) == group else tools[0][0]
            btn = self._make_button(group, "", "", checkable=True)
            btn.setProperty("pill", True)
            self._tool_group.addButton(btn)
            layout.addWidget(btn)
            btn.clicked.connect(lambda _checked=False, g=group: self._pick(self._variant[g]))
            if len(tools) > 1:
                layout.addWidget(self._make_caret(tools))
            self._show_variant(group)

        layout.addWidget(self._make_separator())
        undo = self._make_button(*UNDO_ACTION)
        undo.setEnabled(False)
        undo.clicked.connect(lambda: self.actionTriggered.emit("undo"))
        layout.addWidget(undo)
        layout.addWidget(self._make_separator())

        for action_id, label, icon_name in ACTIONS + _END_BUTTONS:
            btn = self._make_button(action_id, label, icon_name)
            layout.addWidget(btn)
            btn.clicked.connect(
                lambda _checked=False, aid=action_id: self.actionTriggered.emit(aid)
            )

        self._more = self._make_button("more", "更多", "ellipsis")
        self._more.setObjectName("MoreTools")
        self._more.setPopupMode(QToolButton.InstantPopup)
        self._more_menu = QMenu(self._more)
        for tool_id, label, icon_name in MORE_TOOLS:
            action = self._more_menu.addAction(label, lambda tid=tool_id: self._pick(tid))
            self._menu_icons.append((action, icon_name))
        self._more_menu.addSeparator()
        for action_id, label, icon_name in MORE_ACTIONS:
            action = self._more_menu.addAction(_menu_text(action_id, label),
                                               lambda aid=action_id: self.actionTriggered.emit(aid))
            self._menu_icons.append((action, icon_name))
        self._more.setMenu(self._more_menu)
        # 同 PixPin 悬停即展开；showMenu 会进入嵌套事件循环，不能在事件过滤器里同步调用。
        self._more.installEventFilter(self)
        layout.addWidget(self._more)

        self._ocr_feedback = QWidget(self)
        feedback_layout = QHBoxLayout(self._ocr_feedback)
        feedback_layout.setContentsMargins(8, 4, 8, 4)
        self._ocr_status = QLabel()
        self._ocr_status.setWordWrap(True)
        feedback_layout.addWidget(self._ocr_status, 1)
        self._ocr_results = QToolButton()
        self._ocr_results.setText("查看全文")
        self._ocr_results.setProperty("class", "ghost")
        self._ocr_results.setToolTip("打开可编辑的识别全文")
        self._ocr_results.clicked.connect(lambda: self.actionTriggered.emit("ocr_results"))
        feedback_layout.addWidget(self._ocr_results)
        self._root_layout.addWidget(self._ocr_feedback)
        self._ocr_feedback.hide()

        self.refresh_icons(mode)

    def eventFilter(self, watched, event):
        if watched is self._more and event.type() == QEvent.Enter and not self._more_menu.isVisible():
            QTimer.singleShot(0, self._more.showMenu)
        return super().eventFilter(watched, event)

    def _pick(self, tool_id):
        self.toolSelected.emit(self._variant[tool_id] if tool_id in self._variant else tool_id)

    def _show_variant(self, group):
        tool_id = self._variant[group]
        label, icon_name = _TOOL_INFO[tool_id]
        button = self._buttons[group]
        button.setToolTip(_tip(tool_id, label))
        button.setAccessibleName(label)
        self._icon_names[group] = icon_name

    def paintEvent(self, _event) -> None:
        # 圆角卡片背景：WA_TranslucentBackground 下 QSS 不绘制自身背景，
        # 这里用 QPainter 手绘（颜色取自 tokens，与 QSS 同源）。主条、二级面板、识别状态各一张卡片。
        t = T.THEMES[self._mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for row in (self._button_row, self._options, self._ocr_feedback):
            if row is None or row.isHidden():
                continue
            rect = QRectF(QRect(row.mapTo(self, QPoint()), row.size())).adjusted(-1.5, -1.5, 1.5, 1.5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(*T.FLOAT_PANEL_SHADOW))
            painter.drawRoundedRect(rect.translated(0, 3).adjusted(-1, -1, 1, 1), T.RADIUS_LG + 1, T.RADIUS_LG + 1)
            painter.setBrush(QColor(t["popover"]))
            painter.setPen(QPen(QColor(t["border"]), 1))
            painter.drawRoundedRect(rect, T.RADIUS_LG, T.RADIUS_LG)

    def showEvent(self, event):
        super().showEvent(event)
        target = self.pos()
        self._fade.stop()
        self._slide.stop()
        self.setWindowOpacity(0)
        self.move(target + QPoint(0, 6))
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(target)
        self._fade.start()
        self._slide.start()

    # ---- 内部构造 ----

    def _make_button(
        self, btn_id: str, label: str, icon_name: str, checkable: bool = False
    ) -> QToolButton:
        btn = QToolButton(self)
        btn.setToolTip(_tip(btn_id, label))
        btn.setAccessibleName(label)
        btn.setCheckable(checkable)
        btn.setAutoRaise(True)
        btn.setProperty("class", "ghost")
        btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._buttons[btn_id] = btn
        self._icon_names[btn_id] = icon_name
        return btn

    def _make_caret(self, tools) -> QToolButton:
        """组按钮右侧的小箭头，点开切换组内工具（同 PixPin）。"""
        caret = QToolButton(self)
        caret.setObjectName("ToolCaret")
        caret.setToolTip("切换" + "、".join(tool[1] for tool in tools))
        caret.setAutoRaise(True)
        caret.setIconSize(QSize(_CARET_SIZE, _CARET_SIZE))
        caret.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(caret)
        for tool_id, label, icon_name in tools:
            action = menu.addAction(_menu_text(tool_id, label), lambda tid=tool_id: self._pick(tid))
            self._menu_icons.append((action, icon_name))
        caret.setMenu(menu)
        self._carets.append(caret)
        return caret

    def _make_separator(self) -> QFrame:
        sep = QFrame(self)
        sep.setObjectName("FloatPanelSeparator")
        sep.setFrameShape(QFrame.VLine)
        return sep

    # ---- 对外接口 ----

    def set_ocr_status(self, message: str, has_results: bool):
        """识别文字层本身透明，持续反馈让用户知道结果已可操作。"""
        self._ocr_status.setText(message)
        self._ocr_results.setVisible(has_results)
        self._ocr_feedback.show()

    def refresh_icons(self, mode: str) -> None:
        """按主题重染全部图标。mode: 'light' | 'dark'。"""
        if mode not in T.THEMES:
            raise ValueError(f"unknown theme mode: {mode}")
        self._mode = mode
        t = T.THEMES[mode]
        for btn_id, btn in self._buttons.items():
            btn.setIcon(tinted_icon(self._icon_names[btn_id], t["foreground"], _ICON_SIZE))
        for caret in self._carets:
            caret.setIcon(tinted_icon("chevron-down", t["muted_foreground"], _CARET_SIZE))
        for action, icon_name in self._menu_icons:
            action.setIcon(tinted_icon(icon_name, t["popover_foreground"], _ICON_SIZE))
        self.update()  # 卡片背景色也在 paintEvent 里取自主题，需重绘

    def set_undo_enabled(self, enabled: bool) -> None:
        self._buttons["undo"].setEnabled(enabled)

    def set_tool(self, tool_id: str) -> None:
        """程序化选中标注工具，与用户点击发出同样的信号。tool_id 也可以是组 id，如 "geo"。"""
        self._pick(tool_id)

    def mark_tool(self, tool_id: str) -> None:
        """画布换了工具后同步按钮选中态和组按钮图标，不发信号。"""
        group = _GROUP_OF.get(tool_id)
        if group is None:
            # 更多菜单里的工具在主条上没有按钮，只取消其他工具的高亮。
            self._uncheck_tools()
            return
        if tool_id != self._variant[group]:
            self._variant[group] = tool_id
            get_settings().setValue(f"annotation/{group}_tool", tool_id)
            self._show_variant(group)
            self.refresh_icons(self._mode)
        self._buttons[group].setChecked(True)
        self._pill.track(self._buttons[group])

    def selected_tool(self) -> str | None:
        """当前选中的工具 id，未选返回 None。"""
        checked = self._tool_group.checkedButton()
        if checked is None:
            return None
        return next(self._variant[group] for group in self._variant if self._buttons[group] is checked)

    def _uncheck_tools(self):
        self._pill.track(None)
        self._tool_group.setExclusive(False)
        for button in self._tool_group.buttons():
            button.setChecked(False)
        self._tool_group.setExclusive(True)

    def clear_selection(self):
        """画布进入选择模式时清除工具高亮，不再触发绘制工具。"""
        self._uncheck_tools()
        if self._options is not None:
            self._options.hide()
            self.adjustSize()

    def set_options_widget(self, widget):
        """工具配置跟随浮动面板，避免挤占截图画布。"""
        self._options = widget
        self._root_layout.insertWidget(1, widget, 0, Qt.AlignLeft)
        widget.setVisible(bool(self.selected_tool()))

    def _set_options_above(self, above):
        """工具条在选区上方时把二级面板和识别状态移到按钮行上面：贴着选区的始终是按钮行，展开收起不挪动它。"""
        if above == self._options_above:
            return
        self._options_above = above
        rows = [self._button_container, self._options, self._ocr_feedback]
        for row in rows:
            if row is not None:
                self._root_layout.removeWidget(row)
        for row in reversed(rows) if above else rows:
            if row is not None:
                self._root_layout.addWidget(row, 0, Qt.AlignLeft if row is self._options else Qt.Alignment())

    def set_position_near(self, rect: QRect) -> None:
        """主条只按自身尺寸锚定选区，二级面板靠屏幕边缘时独立换侧或横移。"""
        self._slide.stop()
        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        margins = self._root_layout.contentsMargins()
        self._button_layout.setContentsMargins(0, 0, 0, 0)
        # 改子布局边距不会让外层布局的尺寸缓存失效，不刷新就会沿用上一个工具的留白宽度。
        self._button_container.updateGeometry()
        size = self.sizeHint()
        bar = self._button_row.sizeHint()
        bar_width = bar.width() + margins.left() + margins.right()
        anchor_x = max(geo.left(), min(rect.center().x() - bar_width // 2, geo.right() - bar_width + 1))
        x = max(geo.left(), min(anchor_x, geo.right() - size.width() + 1))
        # 宽面板把整个窗口向左推时，用主条行的留白抵消位移。
        self._button_layout.setContentsMargins(anchor_x - x, 0, 0, 0)
        self._button_container.updateGeometry()
        bar_y = rect.bottom() + _GAP + margins.top()
        bar_above = bar_y + bar.height() + margins.bottom() > geo.bottom() + 1
        if bar_above:
            bar_y = max(geo.top() + margins.top(), rect.top() - _GAP - margins.bottom() - bar.height())
        # 二级面板放在主条背向选区的一侧，不遮住截图；那一侧放不下才换到另一侧。
        fits_below = bar_y - margins.top() + size.height() <= geo.bottom() + 1
        fits_above = bar_y - (size.height() - bar.height() - margins.bottom()) >= geo.top()
        self._set_options_above(fits_above if bar_above else not fits_below)
        self.resize(size)
        self._root_layout.activate()
        self.move(x, bar_y - self._button_row.mapTo(self, QPoint()).y())
        self.update()


if __name__ == "__main__":
    # 自查 demo：offscreen 可跑，抓图 + 程序化触发信号验证。
    import sys

    from PySide6.QtWidgets import QApplication

    from styles import apply_theme

    app = QApplication(sys.argv)
    mode = "dark" if "--dark" in sys.argv else "light"
    apply_theme(app, mode)

    panel = FloatPanel(mode=mode)
    panel.toolSelected.connect(lambda tid: print("toolSelected:", tid))
    panel.actionTriggered.connect(lambda aid: print("actionTriggered:", aid))

    panel.set_position_near(QRect(400, 300, 800, 600))
    panel.show()
    app.processEvents()
    print("geometry:", panel.geometry().getRect())

    # 程序化触发：选中矩形工具 + 点保存 + 切换主题重染
    panel.set_tool("rect")
    print("selected_tool:", panel.selected_tool())
    panel._buttons["save"].click()
    panel._buttons["confirm"].click()
    panel.refresh_icons("dark" if mode == "light" else "light")
    panel.refresh_icons(mode)
    app.processEvents()

    out = f"/tmp/toolbar_{mode}.png"
    panel.grab().save(out)
    print("saved:", out)
