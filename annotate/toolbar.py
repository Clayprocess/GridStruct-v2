# annotate/toolbar.py
# FloatPanel 浮动工具条：截图窗口的标注工具切换 + 操作按钮。
#
# 布局（同 PixPin 的主区 + 次区，从左到右）：
#   [9 个常用工具 | 次区浮动按钮 | 次区展开] | [撤销 重做 保存 复制 贴图 OCR 长截图] | [确认 取消]
#
# 约束（AGENTS.md）：
# - 样式全部走 styles/theme.py，本文件不写任何 setStyleSheet；
# - 颜色全部走 styles/tokens.py；
# - 图标只用 assets/icons/ 的 Lucide SVG，经 ui/icons.py 加载。

import os as _os
import sys as _sys

# 直接运行时（python annotate/toolbar.py）把项目根目录加入路径；
# 作为模块导入时此操作无害（路径已存在则重复）。
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QVBoxLayout, QMenu, QToolButton, QWidget, QLabel

from styles import tokens as T
from settings import get_settings
from ui import floating_window_type, tinted_icon

# 主区：同 PixPin 的 Shift+1～9 顺序。矩形和椭圆合成一个“几何”按钮，形状在二级面板切换。
PRIMARY_TOOLS: list[tuple[str, str, str]] = [
    ("geo", "几何", "square"),
    ("polyline", "折线", "spline"),
    ("arrow", "箭头", "arrow-up-right"),
    ("serial", "序号", "list-ordered"),
    ("pencil", "画笔", "pencil"),
    ("highlight", "高亮", "highlighter"),
    ("mosaic", "马赛克", "grid-2x2"),
    ("text", "文字", "type"),
    ("eraser", "橡皮擦", "eraser"),
]
DIGIT_TOOLS = tuple(tool[0] for tool in PRIMARY_TOOLS)
# 几何按钮背后的真实工具，按钮图标跟随当前形状。
GEO_SHAPES = {"rect": ("矩形", "square"), "ellipse": ("椭圆", "circle")}
# 次区：低频工具和取色，悬停展开；同 PixPin，最近用过的次区工具会浮到主区。
SECONDARY_TOOLS: list[tuple[str, str, str]] = [
    ("line", "直线", "minus"),
    ("magnifier", "放大镜", "zoom-in"),
]
SECONDARY_ACTIONS: list[tuple[str, str, str]] = [
    ("pipette", "取色", "pipette"),
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

# 操作 id -> (中文名, Lucide 图标名)
ACTIONS: list[tuple[str, str, str]] = [
    ("undo", "撤销", "undo-2"),
    ("redo", "重做", "redo-2"),
    ("save", "保存", "save"),
    ("copy", "复制", "copy"),
    ("pin", "贴图", "pin"),
    ("ocr", "文字识别", "scan-text"),
    ("longshot", "长截图", "unfold-vertical"),
]

CONFIRM_ID = "confirm"
CANCEL_ID = "cancel"
_END_BUTTONS: dict[str, tuple[str, str]] = {
    CONFIRM_ID: ("确认", "check"),
    CANCEL_ID: ("取消", "x"),
}

_ICON_SIZE = 16
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
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._slide = QPropertyAnimation(self, b"pos", self)
        for animation in (self._fade, self._slide):
            animation.setDuration(150)
            animation.setEasingCurve(QEasingCurve.OutCubic)
        self._buttons: dict[str, QToolButton] = {}
        # 按钮 id -> 当前图标名；几何和浮动按钮的图标会随所选工具变化。
        self._icon_names: dict[str, str] = {}
        # 按钮 id -> 图标颜色在 tokens 中的键（确认/取消用前景色变体）
        self._icon_color_key: dict[str, str] = {}

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(8, 6, 8, 10)
        self._root_layout.setSpacing(_CARD_GAP)
        self._options = None
        self._options_above = False
        buttons = QWidget(self)
        self._button_row = buttons
        # 各行按自身宽度靠左：二级面板变宽变窄时，主条始终在原位。
        self._root_layout.addWidget(buttons, 0, Qt.AlignLeft)
        layout = QHBoxLayout(buttons)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        # 标注工具：单选互斥
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._geo_shape = get_settings().value("annotation/geo_shape", "rect")
        for tool_id, label, icon_name in PRIMARY_TOOLS:
            btn = self._make_button(tool_id, label, icon_name, checkable=True)
            self._tool_group.addButton(btn)
            layout.addWidget(btn)
            btn.clicked.connect(lambda _checked=False, tid=tool_id: self._pick(tid))
        self._set_geo_shape(self._geo_shape)

        # 次区浮动按钮：始终占位，默认显示上次用过的次区工具，首次使用时工具条不会变宽。
        self._floating = self._make_button("floating", "", "minus", checkable=True)
        self._tool_group.addButton(self._floating)
        self._floating.clicked.connect(lambda: self._pick(self._floating_tool))
        layout.addWidget(self._floating)
        self._set_floating_tool(get_settings().value("annotation/floating_tool", SECONDARY_TOOLS[0][0]))

        self._more = self._make_button("more", "更多工具", "chevron-down")
        self._more.setObjectName("MoreTools")
        self._more.setPopupMode(QToolButton.InstantPopup)
        self._more_menu = QMenu(self._more)
        self._menu_actions = {}
        for tool_id, label, icon_name in SECONDARY_TOOLS:
            action = self._more_menu.addAction(label, lambda tid=tool_id: self._pick(tid))
            self._menu_actions[tool_id] = (action, icon_name)
        self._more_menu.addSeparator()
        for action_id, label, icon_name in SECONDARY_ACTIONS:
            action = self._more_menu.addAction(label, lambda aid=action_id: self.actionTriggered.emit(aid))
            self._menu_actions[action_id] = (action, icon_name)
        self._more.setMenu(self._more_menu)
        # 同 PixPin 悬停即展开；showMenu 会进入嵌套事件循环，不能在事件过滤器里同步调用。
        self._more.installEventFilter(self)
        layout.addWidget(self._more)

        layout.addWidget(self._make_separator())

        # 操作按钮
        for action_id, label, icon_name in ACTIONS:
            btn = self._make_button(action_id, label, icon_name)
            layout.addWidget(btn)
            btn.clicked.connect(
                lambda _checked=False, aid=action_id: self.actionTriggered.emit(aid)
            )

        layout.addWidget(self._make_separator())

        # 取消不属于破坏数据的操作，保持描边按钮的层级。
        confirm = self._make_button(CONFIRM_ID, *_END_BUTTONS[CONFIRM_ID])
        confirm.setProperty("class", "primary")
        self._icon_color_key[CONFIRM_ID] = "primary_foreground"
        confirm.clicked.connect(lambda: self.actionTriggered.emit(CONFIRM_ID))
        layout.addWidget(confirm)

        cancel = self._make_button(CANCEL_ID, *_END_BUTTONS[CANCEL_ID])
        cancel.setProperty("class", "outline")
        self._icon_color_key[CANCEL_ID] = "foreground"
        cancel.clicked.connect(lambda: self.actionTriggered.emit(CANCEL_ID))
        layout.addWidget(cancel)

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
        self.toolSelected.emit(self._geo_shape if tool_id == "geo" else tool_id)

    def _set_floating_tool(self, tool_id):
        self._floating_tool = tool_id
        label, icon_name = next(tool[1:] for tool in SECONDARY_TOOLS if tool[0] == tool_id)
        self._floating.setToolTip(label)
        self._floating.setAccessibleName(label)
        self._icon_names["floating"] = icon_name

    def _set_geo_shape(self, shape):
        self._geo_shape = shape
        label, icon_name = GEO_SHAPES[shape]
        self._icon_names["geo"] = icon_name
        self._buttons["geo"].setToolTip(f"几何：{label} (Shift+1)")

    def paintEvent(self, _event) -> None:
        # 圆角卡片背景：WA_TranslucentBackground 下 QSS 不绘制自身背景，
        # 这里用 QPainter 手绘（颜色取自 tokens，与 QSS 同源）。主条、二级面板、识别状态各一张卡片。
        t = T.THEMES[self._mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for row in (self._button_row, self._options, self._ocr_feedback):
            if row is None or row.isHidden():
                continue
            rect = QRectF(row.geometry()).adjusted(-1.5, -1.5, 1.5, 1.5)
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
        shortcut = (f"Shift+{DIGIT_TOOLS.index(btn_id) + 1}" if btn_id in DIGIT_TOOLS else
                    {"undo": "Ctrl+Z", "redo": "Ctrl+Y", "save": "Ctrl+S",
                     "copy": "Ctrl+C", "ocr": "Shift+O",
                     "longshot": "Ctrl+Alt+L", "confirm": "Enter", "cancel": "Esc"}.get(btn_id, ""))
        btn.setToolTip(f"{label} ({shortcut})" if shortcut else label)
        btn.setAccessibleName(label)
        btn.setCheckable(checkable)
        btn.setAutoRaise(True)
        btn.setProperty("class", "ghost")
        btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._buttons[btn_id] = btn
        self._icon_names[btn_id] = icon_name
        self._icon_color_key.setdefault(btn_id, "foreground")
        return btn

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
            btn.setIcon(tinted_icon(self._icon_names[btn_id], t[self._icon_color_key[btn_id]], _ICON_SIZE))
        for action, icon_name in self._menu_actions.values():
            action.setIcon(tinted_icon(icon_name, t["popover_foreground"], _ICON_SIZE))
        self.update()  # 卡片背景色也在 paintEvent 里取自主题，需重绘

    def set_tool(self, tool_id: str) -> None:
        """程序化选中标注工具，与用户点击发出同样的信号。tool_id 可以是 "geo"。"""
        if tool_id in GEO_SHAPES:
            self._set_geo_shape(tool_id)
        self._pick(tool_id)

    def mark_tool(self, tool_id: str) -> None:
        """画布换了工具后同步按钮选中态、几何图标和次区浮动按钮，不发信号。"""
        if tool_id in GEO_SHAPES:
            if tool_id != self._geo_shape:
                self._set_geo_shape(tool_id)
                get_settings().setValue("annotation/geo_shape", tool_id)
                self.refresh_icons(self._mode)
            button = self._buttons["geo"]
        elif any(tool_id == tool[0] for tool in SECONDARY_TOOLS):
            if tool_id != self._floating_tool:
                self._set_floating_tool(tool_id)
                get_settings().setValue("annotation/floating_tool", tool_id)
                self.refresh_icons(self._mode)
            button = self._floating
        else:
            button = self._buttons[tool_id]
        button.setChecked(True)

    def selected_tool(self) -> str | None:
        """当前选中的工具 id，未选返回 None。"""
        checked = self._tool_group.checkedButton()
        if checked is None:
            return None
        if checked is self._buttons["geo"]:
            return self._geo_shape
        if checked is self._floating:
            return self._floating_tool
        return next(tool_id for tool_id, btn in self._buttons.items() if btn is checked)

    def clear_selection(self):
        """画布进入选择模式时清除工具高亮，不再触发绘制工具。"""
        self._tool_group.setExclusive(False)
        for button in self._tool_group.buttons():
            button.setChecked(False)
        self._tool_group.setExclusive(True)
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
        rows = [self._button_row, self._options, self._ocr_feedback]
        for row in rows:
            if row is not None:
                self._root_layout.removeWidget(row)
        for row in reversed(rows) if above else rows:
            if row is not None:
                self._root_layout.addWidget(row, 0, Qt.Alignment() if row is self._ocr_feedback else Qt.AlignLeft)

    def set_position_near(self, rect: QRect) -> None:
        """把工具条放到选区附近：下方优先，放不下放上方；始终不超出屏幕。

        上下按“按钮行 + 二级面板”都展开的高度来选，切换工具时不会在上下之间翻；
        在下方时顶边贴选区，在上方时底边贴选区，按钮行都不动。
        """
        self._slide.stop()
        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        margins = self._root_layout.contentsMargins()
        tallest = self._button_row.sizeHint().height() + margins.top() + margins.bottom()
        if self._options is not None:
            tallest += self._options.sizeHint().height() + _CARD_GAP
        below = rect.bottom() + _GAP
        self._set_options_above(below + tallest > geo.bottom() + 1)
        size = self.sizeHint()

        # 按主条宽度居中：二级面板比主条宽时向右伸出，主条不随工具切换左右跳。
        bar_width = self._button_row.sizeHint().width() + margins.left() + margins.right()
        x = rect.center().x() - bar_width // 2
        x = max(geo.left(), min(x, geo.right() - size.width() + 1))
        if self._options_above:
            y = max(geo.top(), rect.top() - size.height() - _GAP)
        else:
            y = below

        self.setGeometry(QRect(QPoint(x, y), size))


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
