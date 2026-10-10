# annotate/toolbar.py
# FloatPanel 浮动工具条：截图窗口的标注工具切换 + 操作按钮。
#
# 布局（见 ../pixpin/pixpin-ui-spec.md §2.2，从左到右）：
#   [12 个标注工具（单选互斥）] | [撤销 重做 保存 复制 贴图 OCR] | [确认 取消]
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

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QToolButton, QWidget

from styles import tokens as T
from ui.icons import tinted_icon

# 工具 id -> (中文名, Lucide 图标名)，顺序即工具条从左到右
TOOLS: list[tuple[str, str, str]] = [
    ("rect", "矩形", "square"),
    ("ellipse", "椭圆", "circle"),
    ("arrow", "箭头", "arrow-up-right"),
    ("pencil", "画笔", "pencil"),
    ("line", "直线", "minus"),
    ("polyline", "折线", "spline"),
    ("text", "文字", "type"),
    ("serial", "序号", "list-ordered"),
    ("mosaic", "马赛克", "grid-2x2"),
    ("highlight", "高亮", "highlighter"),
    ("eraser", "橡皮擦", "eraser"),
    ("magnifier", "放大镜", "zoom-in"),
]

# 操作 id -> (中文名, Lucide 图标名)
ACTIONS: list[tuple[str, str, str]] = [
    ("undo", "撤销", "undo-2"),
    ("redo", "重做", "redo-2"),
    ("save", "保存", "save"),
    ("copy", "复制", "copy"),
    ("pin", "贴图", "pin"),
    ("ocr", "文字识别", "scan-text"),
]

CONFIRM_ID = "confirm"
CANCEL_ID = "cancel"
_END_BUTTONS: dict[str, tuple[str, str]] = {
    CONFIRM_ID: ("确认", "check"),
    CANCEL_ID: ("取消", "x"),
}

_ICON_SIZE = 16
_GAP = 8  # 工具条与选区之间的间距


class FloatPanel(QWidget):
    """无边框浮动工具条（截图窗口用）。"""

    toolSelected = Signal(str)     # 标注工具被选中 -> 工具 id
    actionTriggered = Signal(str)  # 操作按钮被点击 -> 操作 id（含 confirm / cancel）

    def __init__(self, mode: str = "light", parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint,
        )
        self.setObjectName("FloatPanel")
        # 圆角卡片需要透明背景，否则四角会被填成方形
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._mode = "light"
        self._buttons: dict[str, QToolButton] = {}
        # 按钮 id -> 图标颜色在 tokens 中的键（确认/取消用前景色变体）
        self._icon_color_key: dict[str, str] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        # 标注工具：单选互斥
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        for tool_id, label, icon_name in TOOLS:
            btn = self._make_button(tool_id, label, icon_name, checkable=True)
            self._tool_group.addButton(btn)
            layout.addWidget(btn)
            btn.clicked.connect(
                lambda _checked=False, tid=tool_id: self.toolSelected.emit(tid)
            )

        layout.addWidget(self._make_separator())

        # 操作按钮
        for action_id, label, icon_name in ACTIONS:
            btn = self._make_button(action_id, label, icon_name)
            layout.addWidget(btn)
            btn.clicked.connect(
                lambda _checked=False, aid=action_id: self.actionTriggered.emit(aid)
            )

        layout.addWidget(self._make_separator())

        # 确认 / 取消：放右侧，用 primary / destructive 区分
        confirm = self._make_button(CONFIRM_ID, *_END_BUTTONS[CONFIRM_ID])
        confirm.setProperty("class", "primary")
        self._icon_color_key[CONFIRM_ID] = "primary_foreground"
        confirm.clicked.connect(lambda: self.actionTriggered.emit(CONFIRM_ID))
        layout.addWidget(confirm)

        cancel = self._make_button(CANCEL_ID, *_END_BUTTONS[CANCEL_ID])
        cancel.setProperty("class", "destructive")
        self._icon_color_key[CANCEL_ID] = "destructive_foreground"
        cancel.clicked.connect(lambda: self.actionTriggered.emit(CANCEL_ID))
        layout.addWidget(cancel)

        self.refresh_icons(mode)

    def paintEvent(self, _event) -> None:
        # 圆角卡片背景：WA_TranslucentBackground 下 QSS 不绘制自身背景，
        # 这里用 QPainter 手绘（颜色取自 tokens，与 QSS 同源）。
        t = T.THEMES[self._mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(t["popover"]))
        painter.setPen(QPen(QColor(t["border"]), 1))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, T.RADIUS_LG, T.RADIUS_LG)

    # ---- 内部构造 ----

    def _make_button(
        self, btn_id: str, label: str, icon_name: str, checkable: bool = False
    ) -> QToolButton:
        btn = QToolButton(self)
        btn.setToolTip(label)
        btn.setCheckable(checkable)
        btn.setAutoRaise(True)
        btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._buttons[btn_id] = btn
        self._icon_color_key.setdefault(btn_id, "foreground")
        return btn

    def _make_separator(self) -> QFrame:
        sep = QFrame(self)
        sep.setObjectName("FloatPanelSeparator")
        sep.setFrameShape(QFrame.VLine)
        return sep

    def _icon_name(self, btn_id: str) -> str:
        for bid, _, icon_name in TOOLS:
            if bid == btn_id:
                return icon_name
        for bid, _, icon_name in ACTIONS:
            if bid == btn_id:
                return icon_name
        return _END_BUTTONS[btn_id][1]

    # ---- 对外接口 ----

    def refresh_icons(self, mode: str) -> None:
        """按主题重染全部图标。mode: 'light' | 'dark'。"""
        if mode not in T.THEMES:
            raise ValueError(f"unknown theme mode: {mode}")
        self._mode = mode
        t = T.THEMES[mode]
        for btn_id, btn in self._buttons.items():
            btn.setIcon(
                tinted_icon(
                    self._icon_name(btn_id),
                    t[self._icon_color_key[btn_id]],
                    _ICON_SIZE,
                )
            )
        self.update()  # 卡片背景色也在 paintEvent 里取自主题，需重绘

    def set_tool(self, tool_id: str) -> None:
        """程序化选中标注工具（走 click，保证与用户点击一致的信号）。"""
        btn = self._buttons.get(tool_id)
        if btn is not None and btn.isCheckable():
            btn.click()

    def selected_tool(self) -> str | None:
        """当前选中的工具 id，未选返回 None。"""
        checked = self._tool_group.checkedButton()
        if checked is None:
            return None
        for tool_id, btn in self._buttons.items():
            if btn is checked:
                return tool_id
        return None

    def set_position_near(self, rect: QRect) -> None:
        """把工具条放到选区附近：下方优先，放不下放上方；始终不超出屏幕。"""
        size = self.sizeHint()
        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()

        x = rect.center().x() - size.width() // 2
        x = max(geo.left(), min(x, geo.right() - size.width() + 1))

        below = rect.bottom() + _GAP
        if below + size.height() <= geo.bottom() + 1:
            y = below
        else:
            y = max(geo.top(), rect.top() - size.height() - _GAP)

        self.setGeometry(QRect(QPoint(x, y), size))


if __name__ == "__main__":
    # 自查 demo：offscreen 可跑，抓图 + 程序化触发信号验证。
    import sys

    from PySide6.QtWidgets import QApplication

    from styles.theme import apply_theme

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
