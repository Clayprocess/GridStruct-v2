# annotate/toolbar.py
# FloatPanel 浮动工具条：截图窗口的标注工具切换 + 操作按钮。
#
# 布局（同 PixPin 3.5.5.1 默认工具条，从左到右）：
#   [几何▾ 画笔▾ 线条▾ 文字 序号 马赛克 橡皮擦] | [撤销] | [长截图 文字识别 表格识别 贴图 保存 关闭 复制]
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

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QRegion
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from styles import tokens as T
from settings import get_settings
from ui import floating_window_type, round_corners, tinted_icon
from ui.motion import SelectionPill, Spinner, animation, tween

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
    ("table", "表格识别", "table"),
    ("pin", "贴图", "pin"),
    ("save", "保存", "save"),
]

# 同 PixPin 没有单独的“确认”：最右的复制就是完成，与 Enter 同效；关闭紧挨在它左边。
CONFIRM_ID = "confirm"
CANCEL_ID = "cancel"
_END_BUTTONS: list[tuple[str, str, str]] = [
    (CANCEL_ID, "关闭", "x"),
    (CONFIRM_ID, "复制截图", "copy"),
]
_SHORTCUTS = {"undo": "Ctrl+Z", "save": "Ctrl+S", "ocr": "Shift+O", "longshot": "Ctrl+Alt+L",
              CONFIRM_ID: "Ctrl+C / Enter", CANCEL_ID: "Esc"}
_SHORTCUTS.update({tool: f"Shift+{i + 1}" for i, tool in enumerate(DIGIT_TOOLS) if tool in _TOOL_INFO})
_SHORTCUTS["rect"] = _SHORTCUTS["ellipse"] = "Shift+1"


def _tip(item_id, label):
    return f"{label} ({_SHORTCUTS[item_id]})" if item_id in _SHORTCUTS else label


def _menu_text(item_id, label):
    # 制表符后的文字是 QMenu 右侧的快捷键列，只作提示，按键仍由截图窗口统一分发。
    return f"{label}\t{_SHORTCUTS[item_id]}" if item_id in _SHORTCUTS else label

_ICON_SIZE = T.TOOLBAR_ICON_SIZE
_CARET_SIZE = 12
_GAP = 8  # 工具条与选区之间的间距
# 主工具条与二级面板是两张独立卡片，中间留缝（同 PixPin：二级面板在主条下方、左缘对齐、宽度随工具变化）。
_CARD_GAP = 6


class _DragHandle(QWidget):
    """主条最左侧的竖胶囊拖柄：平时是一道淡色细线，悬停或拖动时加深变宽，提示这里能抓。"""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self._press = None
        self._hover = 0.0
        self._anim = tween(self, T.MOTION_FAST)
        self._anim.valueChanged.connect(self._set_hover)
        self.setFixedWidth(14)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("拖动移动工具条，双击回到选区旁")

    def _set_hover(self, value):
        self._hover = value
        self.update()

    def _animate(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event):
        self._animate(1.0)

    def leaveEvent(self, event):
        if self._press is None:
            self._animate(0.0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = (event.globalPosition().toPoint(), self._panel.pos())
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._press is not None:
            start, origin = self._press
            self._panel.move(origin + event.globalPosition().toPoint() - start)

    def mouseReleaseEvent(self, event):
        if self._press is None:
            return
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        if not self.underMouse():
            self._animate(0.0)
        self._panel.keep_position()

    def mouseDoubleClickEvent(self, event):
        self._panel.reset_position()

    def paintEvent(self, _event):
        # 静止时在描边色和弱化色之间取四成，既看得见又不抢按钮；悬停过渡到弱化色。
        t = T.THEMES[self._panel._mode]
        rest, active = QColor(t["border"]), QColor(t["muted_foreground"])
        amount = 0.4 + 0.6 * self._hover
        color = QColor(*(round(a + (b - a) * amount) for a, b in
                         ((rest.red(), active.red()), (rest.green(), active.green()), (rest.blue(), active.blue()))))
        width = 3 + self._hover
        height = 16
        with QPainter(self) as p:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height),
                              width / 2, width / 2)


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
        # 二级面板卡片的展开/变宽过渡：_card 是动画中的卡片矩形，_card_end 是上次展开到的位置。
        self._card = None
        self._card_end = None
        self._card_anim = tween(self)
        self._card_anim.valueChanged.connect(self._set_card)
        self._card_anim.finished.connect(lambda: self._set_card(None))
        self._button_container = QWidget(self)
        # 主条比二级面板窄时容器左侧留白，全局 QWidget 背景会把这段留白涂成方块。
        self._button_container.setObjectName("FloatPanelRow")
        self._button_layout = QHBoxLayout(self._button_container)
        self._button_layout.setContentsMargins(0, 0, 0, 0)
        self._button_layout.setSpacing(0)
        buttons = QWidget(self._button_container)
        buttons.setObjectName("FloatPanelBar")
        self._button_row = buttons
        self._button_layout.addWidget(buttons, 0, Qt.AlignLeft)
        self._button_layout.addStretch()
        self._root_layout.addWidget(self._button_container)
        layout = QHBoxLayout(buttons)
        layout.setContentsMargins(2, 6, 6, 6)
        layout.setSpacing(2)
        # 拖动过的主条留在原处（_manual 是主条左上角的屏幕坐标），选区再变也不跟着跳；双击拖柄复位。
        self._manual: QPoint | None = None
        self._near: QRect | None = None
        layout.addWidget(_DragHandle(self))

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
        # 同 PixPin 默认工具条只放撤销；重做没有按钮，在提示里写上快捷键。
        undo.setToolTip("撤销 (Ctrl+Z)，重做 (Ctrl+Y)")
        undo.clicked.connect(lambda: self.actionTriggered.emit("undo"))
        layout.addWidget(undo)
        layout.addWidget(self._make_separator())

        for action_id, label, icon_name in ACTIONS + _END_BUTTONS:
            btn = self._make_button(action_id, label, icon_name)
            layout.addWidget(btn)
            btn.clicked.connect(
                lambda _checked=False, aid=action_id: self.actionTriggered.emit(aid)
            )

        self._build_ocr_card()
        self._root_layout.addWidget(self._ocr_feedback)
        self._ocr_feedback.hide()

        self.refresh_icons(mode)

    def _build_ocr_card(self):
        """识别卡片：等待、失败时只有一行状态；识别出文字后展开成可直接改的全文（Clay 2026-11-05 定）。

        对照截图核对、改字都在这里完成，不必再开识别窗口；排版和识别窗口共用同一个设置。
        """
        from ocr.editor import OCRTextEdit
        from ocr.layout import LAYOUTS
        card = QWidget(self)
        card.setObjectName("FloatPanelCard")
        self._ocr_feedback = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(T.SPACE_2)

        header = QHBoxLayout()
        header.setSpacing(T.SPACE_2)
        self._ocr_spinner = Spinner(card)
        header.addWidget(self._ocr_spinner)
        self._ocr_status = QLabel()
        self._ocr_status.setObjectName("OCRStatus")
        self._ocr_status.setWordWrap(True)
        header.addWidget(self._ocr_status, 1)
        self._ocr_header = header
        self._ocr_pending = QLabel()
        self._ocr_pending.setObjectName("OCRPending")
        self._ocr_pending.setToolTip("模型置信度较低的文字标了波浪线，仅供参考；文字正确时无需修改")
        # 标签默认被拉到和排版下拉框一样高，低置信度的底色块会变成一大块。
        header.addWidget(self._ocr_pending, 0, Qt.AlignVCenter)
        # 等待时可取消、失败后可重试，都留在这张卡片里，不弹窗打断截图。
        self._ocr_buttons = {}
        for action_id, text, tip in (("ocr_next", "下一处 F8", "跳到下一处低置信度文字"),
                                     ("ocr_cancel", "取消", "停止等待这次识别"),
                                     ("ocr", "重试", "重新识别当前截图")):
            button = QToolButton()
            button.setText(text)
            button.setProperty("class", "ghost")
            button.setToolTip(tip)
            header.addWidget(button)
            self._ocr_buttons[action_id] = button
        self._ocr_buttons["ocr_next"].clicked.connect(lambda: self.ocr_editor.next_pending())
        for action_id in ("ocr_cancel", "ocr"):
            self._ocr_buttons[action_id].clicked.connect(lambda _=False, aid=action_id: self.actionTriggered.emit(aid))
        header.addStretch()
        self._ocr_layout_caption = QLabel("排版")
        self._ocr_layout_caption.setObjectName("OCRStatus")
        header.addWidget(self._ocr_layout_caption)
        self._ocr_layout = QComboBox()
        self._ocr_layout.setToolTip("段落：一段合成一行；原样换行：保留截图里的换行；一行：全部接成一行")
        for mode, label, tip in LAYOUTS:
            self._ocr_layout.addItem(label, mode)
        self._ocr_layout.activated.connect(self._choose_ocr_layout)
        header.addWidget(self._ocr_layout)
        layout.addLayout(header)

        self.ocr_editor = OCRTextEdit(self._mode, card)
        self.ocr_editor.marksChanged.connect(self._update_ocr_card)
        layout.addWidget(self.ocr_editor)

        footer = QHBoxLayout()
        footer.setSpacing(T.SPACE_2)
        self._ocr_hint = QLabel()
        self._ocr_hint.setObjectName("OCRStatus")
        footer.addWidget(self._ocr_hint, 1)
        open_window = QPushButton("在窗口中打开")
        open_window.setToolTip("在文字识别窗口里对照原图核对，截图随即结束")
        open_window.clicked.connect(lambda: self.actionTriggered.emit("ocr_results"))
        copy = QPushButton("复制文字")
        copy.setProperty("class", "primary")
        copy.setToolTip("复制上面的全文并结束截图")
        copy.clicked.connect(lambda: self.actionTriggered.emit("ocr_copy"))
        footer.addWidget(open_window)
        footer.addWidget(copy)
        layout.addLayout(footer)
        self._ocr_full = (self._ocr_layout_caption, self._ocr_layout, self.ocr_editor, self._ocr_hint, open_window, copy)
        self._ocr_linked = False
        self._ocr_message = ""
        self._ocr_fitted = None
        for widget in self._ocr_full:
            widget.hide()

    def eventFilter(self, watched, event):
        if watched is self._options and event.type() == QEvent.Show and self._card_end is None:
            # 面板刚显示时布局要等下一轮事件循环才排好，先整块藏起来，交给展开动画露出。
            self._options.setMask(QRegion(0, 0, 1, 1))
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
            geometry = self._row_rect(row)
            if row is self._options:
                # 面板刚显示、还没开始展开时布局未排好，不画卡片，否则会闪出一个小圆点。
                if self._card_end is None:
                    continue
                geometry = self._card if self._card is not None else geometry
            if geometry.height() < 4:
                continue
            rect = QRectF(geometry).adjusted(-1, -1, 1, 1)
            painter.setPen(Qt.NoPen)
            layers = len(T.FLOAT_PANEL_SHADOW_ALPHAS)
            for index, alpha in enumerate(T.FLOAT_PANEL_SHADOW_ALPHAS):
                spread = layers - index
                painter.setBrush(QColor(0, 0, 0, alpha))
                painter.drawRoundedRect(rect.translated(0, 2).adjusted(-spread, -spread, spread, spread),
                                        T.RADIUS_LG + spread, T.RADIUS_LG + spread)
            # 描边用“外圈填描边色、内圈填底色”两次填充，不用 1px 画笔：画笔线居中压在边界上，
            # 125%/150% 缩放时落在半个物理像素上，圆角处发虚、深浅不一。
            painter.setBrush(QColor(t["border"]))
            painter.drawRoundedRect(rect, T.RADIUS_LG, T.RADIUS_LG)
            painter.setBrush(QColor(t["popover"]))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), T.RADIUS_LG - 1, T.RADIUS_LG - 1)

    def _row_rect(self, row):
        return QRect(row.mapTo(self, QPoint()), row.size())

    def _set_card(self, rect):
        self._card = rect
        if rect is None:
            self._options.clearMask()
        else:
            # 内容按卡片当前大小裁切，看起来是从主条里抽出来的。
            self._options.setMask(QRegion(rect.translated(-self._options.pos())))
        self.update()

    def _reveal_options(self):
        """二级面板首次出现时从主条边缘展开，换工具时卡片宽高平滑过渡到新尺寸。"""
        if self._options is None or self._options.isHidden():
            self._card_anim.stop()
            if self._card is not None:
                self._set_card(None)
            self._card_end = None
            return
        end = self._row_rect(self._options)
        if end == self._card_end:
            if self._card_anim.state() != QVariantAnimation.Running:
                self._options.clearMask()
            return
        start = self._card if self._card is not None else self._card_end
        if start is None:
            edge = end.bottom() + 1 if self._options_above else end.top()
            start = QRect(end.left(), edge, end.width(), 0)
        self._card_end = end
        self._card_anim.stop()
        self._card_anim.setStartValue(start)
        self._card_anim.setEndValue(end)
        self._card_anim.start()

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
        menu = round_corners(QMenu(caret))
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

    def set_ocr_status(self, message: str, has_results: bool, state: str):
        """识别文字层本身透明，持续反馈让用户知道结果已可操作。"""
        from ocr.controller import WAITING_STATES
        waiting = state in WAITING_STATES
        self._ocr_message = message
        self._ocr_spinner.setVisible(waiting)
        self._ocr_buttons["ocr_cancel"].setVisible(waiting)
        self._ocr_buttons["ocr"].setVisible(state in ("failed", "timeout", "cancelled"))
        self._update_ocr_card()
        self._ocr_feedback.show()

    @property
    def ocr_document(self):
        return self.ocr_editor.document_model

    def set_ocr_document(self, document, linked):
        """卡片里显示这份识别全文；None 收起成一行状态。linked：截图上还有文字层可以互相定位。"""
        if document is not None and not document:
            document = None
        if document is not self.ocr_document:
            if self.ocr_document is not None:
                self.ocr_document.changed.disconnect(self._fit_ocr_text)
            self.ocr_editor.set_document(document)
            if document is not None:
                document.changed.connect(self._fit_ocr_text)
                self._fit_ocr_text()
        self._ocr_linked = linked and document is not None
        for widget in self._ocr_full:
            widget.setVisible(document is not None)
        self._update_ocr_card()

    def _choose_ocr_layout(self, index):
        if self.ocr_editor.choose_layout(self._ocr_layout.itemData(index)):
            self.ocr_editor.setFocus()
        self._update_ocr_card()

    def _fit_ocr_text(self):
        """编辑框按内容定高：少则不留大片空白，多于 8 行就在框内滚动。
        只在换结果、换排版时定一次，打字时不跟着变，免得工具条上下跳。"""
        fitted = (self.ocr_document, self.ocr_document.layout)
        if fitted == self._ocr_fitted:
            return
        self._ocr_fitted = fitted
        editor = self.ocr_editor
        # 第一次识别出结果时卡片还没显示过，样式表里的字号尚未生效，先套上样式再量。
        editor.ensurePolished()
        line = editor.fontMetrics().lineSpacing() * 1.4
        margins = editor.contentsMargins()
        chrome = margins.top() + margins.bottom() + 2 * editor.frameWidth()
        card = self._ocr_feedback.layout().contentsMargins()
        width = (self._button_row.sizeHint().width() - card.left() - card.right()
                 - margins.left() - margins.right() - 2 * editor.frameWidth()
                 - editor.verticalScrollBar().sizeHint().width())
        height = min(max(editor.content_height(width), line * 3), line * 8)
        editor.setFixedHeight(round(height + chrome))
        if self._near is not None and self.isVisible():
            self.set_position_near(self._near)

    def _update_ocr_card(self):
        document = self.ocr_document
        # 只有一行状态时文字占满整行、长提示可折行；有全文时让低置信度紧跟在“已识别 N 处”后面。
        self._ocr_header.setStretchFactor(self._ocr_status, 1 if document is None else 0)
        if document is None:
            self._ocr_status.setText(self._ocr_message)
            self._ocr_pending.hide()
            self._ocr_buttons["ocr_next"].hide()
            return
        self._ocr_status.setText(f"已识别 {len(document)} 处文字" if self._ocr_linked else self._ocr_message)
        count = len(self.ocr_editor.pending_spans())
        self._ocr_pending.setText(f"低置信度 {count} 处")
        self._ocr_pending.setVisible(count > 0)
        self._ocr_buttons["ocr_next"].setVisible(count > 0)
        self._ocr_layout.setCurrentIndex(self._ocr_layout.findData(document.layout))
        hint = "已修改，复制以这里为准" if document.draft is not None else "直接改字"
        hint += " · Enter 换行"
        if self._ocr_linked:
            hint += " · 点截图上的字可定位到这里"
        self._ocr_hint.setText(hint)

    def refresh_icons(self, mode: str) -> None:
        """按主题重染全部图标。mode: 'light' | 'dark'。"""
        if mode not in T.THEMES:
            raise ValueError(f"unknown theme mode: {mode}")
        self._mode = mode
        self.ocr_editor.set_mode(mode)
        t = T.THEMES[mode]
        # Qt 自动生成的禁用图标在深色主题里几乎和正常态一样亮，禁用色自己给：弱化色再往底色淡一半。
        muted, base = QColor(t["muted_foreground"]), QColor(t["popover"])
        disabled = QColor((muted.red() + base.red()) // 2, (muted.green() + base.green()) // 2,
                          (muted.blue() + base.blue()) // 2).name()
        for btn_id, btn in self._buttons.items():
            name = self._icon_names[btn_id]
            icon = tinted_icon(name, t["foreground"], _ICON_SIZE, QIcon())
            btn.setIcon(tinted_icon(name, disabled, _ICON_SIZE, icon, QIcon.Disabled))
        for caret in self._carets:
            caret.setIcon(tinted_icon("chevron-down", t["muted_foreground"], _CARET_SIZE))
        for action, icon_name in self._menu_icons:
            action.setIcon(tinted_icon(icon_name, t["popover_foreground"], _ICON_SIZE))
        self.update()  # 卡片背景色也在 paintEvent 里取自主题，需重绘

    def set_undo_enabled(self, enabled: bool) -> None:
        self._buttons["undo"].setEnabled(enabled)

    def set_copy_text_selected(self, selected: bool):
        # 选字时 Ctrl+C 只复制文字不结束，只有按钮和 Enter 会复制文字并结束。
        label = "复制选中文字" if selected else "复制截图"
        button = self._buttons[CONFIRM_ID]
        button.setAccessibleName(label)
        button.setToolTip(f"{label}并结束（{'Enter' if selected else 'Ctrl+C / Enter'}）")

    def set_tool(self, tool_id: str) -> None:
        """程序化选中标注工具，与用户点击发出同样的信号。tool_id 也可以是组 id，如 "geo"。"""
        self._pick(tool_id)

    def mark_tool(self, tool_id: str) -> None:
        """画布换了工具后同步按钮选中态和组按钮图标，不发信号。"""
        group = _GROUP_OF.get(tool_id)
        if group is None:
            # 切回 OCR 选字等无标注工具状态时，清除工具高亮。
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
            self._reveal_options()
            self.adjustSize()

    def set_options_widget(self, widget):
        """工具配置跟随浮动面板，避免挤占截图画布。"""
        self._options = widget
        widget.installEventFilter(self)
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

    def manual_position(self) -> QPoint | None:
        """用户拖到的主条位置；没拖过为 None。框选阶段的工具条换成标注窗口的工具条时要接着用。"""
        return self._manual

    def set_manual_position(self, pos: QPoint | None) -> None:
        self._manual = pos

    def keep_position(self) -> None:
        """拖动结束：记下主条位置，再按新位置重新决定二级面板放在哪一侧。"""
        row = self._button_row.mapToGlobal(QPoint())
        self._manual = QPoint(row.x() - self._root_layout.contentsMargins().left(), row.y())
        self.set_position_near(self._near)

    def reset_position(self) -> None:
        self._manual = None
        self.set_position_near(self._near)

    def set_position_near(self, rect: QRect) -> None:
        """主条只按自身尺寸锚定选区，二级面板靠屏幕边缘时独立换侧或横移。"""
        self._slide.stop()
        self._near = QRect(rect)
        anchor = self._manual if self._manual is not None else rect.center()
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        margins = self._root_layout.contentsMargins()
        self._button_layout.setContentsMargins(0, 0, 0, 0)
        # 改子布局边距不会让外层布局的尺寸缓存失效，不刷新就会沿用上一个工具的留白宽度。
        self._button_container.updateGeometry()
        size = self.sizeHint()
        bar = self._button_row.sizeHint()
        bar_width = bar.width() + margins.left() + margins.right()
        wanted_x = self._manual.x() if self._manual is not None else rect.center().x() - bar_width // 2
        anchor_x = max(geo.left(), min(wanted_x, geo.right() - bar_width + 1))
        x = max(geo.left(), min(anchor_x, geo.right() - size.width() + 1))
        # 宽面板把整个窗口向左推时，用主条行的留白抵消位移。
        self._button_layout.setContentsMargins(anchor_x - x, 0, 0, 0)
        self._button_container.updateGeometry()
        if self._manual is not None:
            bar_y = max(geo.top() + margins.top(),
                        min(self._manual.y(), geo.bottom() + 1 - margins.bottom() - bar.height()))
            # 拖到选区中线以上就当作在选区上方，二级面板照样朝外展开。
            bar_above = bar_y + bar.height() // 2 < rect.center().y()
        else:
            bar_y = rect.bottom() + _GAP + margins.top()
            above_y = rect.top() - _GAP - margins.bottom() - bar.height()
            bar_above = bar_y + bar.height() + margins.bottom() > geo.bottom() + 1
            # 识别全文卡片很高：下方连卡片放不下、上方放得下时整条翻到选区上方，卡片不压住截图（Clay 2026-11-05 定）。
            # 二级面板不这样做：换工具时主条跳到另一边太晃。
            card = self._ocr_feedback.sizeHint().height() + _CARD_GAP if self.ocr_document is not None else 0
            if card and not bar_above and bar_y + bar.height() + card + margins.bottom() > geo.bottom() + 1:
                bar_above = above_y - card - margins.top() >= geo.top()
            if bar_above:
                bar_y = max(geo.top() + margins.top(), above_y)
        # 二级面板放在主条背向选区的一侧，不遮住截图；那一侧放不下才换到另一侧。
        fits_below = bar_y - margins.top() + size.height() <= geo.bottom() + 1
        fits_above = bar_y - (size.height() - bar.height() - margins.bottom()) >= geo.top()
        self._set_options_above(fits_above if bar_above else not fits_below)
        self.resize(size)
        self._root_layout.activate()
        self.move(x, bar_y - self._button_row.mapTo(self, QPoint()).y())
        self._reveal_options()
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
