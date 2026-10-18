"""当前工具的二级配置：单行紧凑条，高频项平铺，开关类收进"更多"菜单。"""

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QLabel, QComboBox, QSpinBox, QToolButton,
    QButtonGroup, QColorDialog, QFontComboBox, QFrame, QMenu,
)

from styles import tokens as T
from ui import tinted_icon

_COLOR_NAMES = ("红", "黑", "白", "蓝", "绿", "黄")
# 粗细预设覆盖默认值 3，其余值用旁边的数字框或滚轮微调。
_WIDTH_PRESETS = ((3, "细"), (6, "中"), (10, "粗"))
_SWATCH = 16


def _theme():
    return T.THEMES[QApplication.instance().property("theme_mode") or "light"]


def _swatch_icon(color, current=False):
    # 色块是数据（颜色值）而非图标。圆形是预设色；方形是"当前颜色"，点开自定义取色，
    # 同 PixPin 调色板里那块最大的当前色。白色色块需要描边才能在浅色面板上看见。
    pm = QPixmap(QSize(_SWATCH, _SWATCH) * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    with QPainter(pm) as p:
        p.setRenderHint(QPainter.Antialiasing)
        # 深色主题的 border 与面板几乎同色，黑色色块会消失，描边改用更亮一级的灰。
        p.setPen(QPen(QColor(_theme()["secondary_hover"]), 1))
        p.setBrush(QColor(color))
        if current:
            p.drawRoundedRect(QRectF(0.5, 0.5, _SWATCH - 1, _SWATCH - 1), T.RADIUS_SM, T.RADIUS_SM)
        else:
            p.drawEllipse(QRectF(2, 2, _SWATCH - 4, _SWATCH - 4))
    return QIcon(pm)


def _dot_icon(diameter):
    pm = QPixmap(QSize(_SWATCH, _SWATCH) * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    with QPainter(pm) as p:
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_theme()["foreground"]))
        offset = (_SWATCH - diameter) / 2
        p.drawEllipse(offset, offset, diameter, diameter)
    return QIcon(pm)


class ToolOptions(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolOptions")
        # 顶部分隔线由 QSS 画，需要样式背景。
        self.setAttribute(Qt.WA_StyledBackground)
        self._canvas = canvas
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 6, 8, 4)
        self._layout.setSpacing(4)
        self._bindings = []
        self._tool_id = ""
        canvas.optionsChanged.connect(self.sync_tool)
        self.setVisible(False)

    def sync_tool(self, tool_id):
        if tool_id != self._tool_id:
            self.set_tool(tool_id)
        else:
            self._sync(tool_id)

    def set_tool(self, tool_id):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._bindings.clear()
        self._tool_id = tool_id
        self.setVisible(bool(tool_id))
        if not tool_id:
            return
        more = []
        if tool_id in ("rect", "ellipse") and self._canvas.tool_id == tool_id:
            self._geo_shape(tool_id)
            self._separator()
        if tool_id not in ("mosaic", "eraser", "magnifier"):
            self._colors("color")
        if tool_id in ("rect", "ellipse", "arrow", "line", "polyline", "pencil", "highlight"):
            self._separator()
            self._width()
        if tool_id in ("rect", "ellipse"):
            self._separator()
            self._segment("fill_mode", None, [("描边", "outline"), ("填充", "fill"), ("两者", "both")])
        if tool_id == "rect":
            self._spin("round_radius", "圆角", 0, 64)
        elif tool_id == "arrow":
            self._separator()
            combo = self._combo("arrow_type", None, [
                ("直线箭头", "straight"), ("直线双向", "straight_both"), ("线形箭头", "line"),
                ("线形双向", "line_both"), ("实心箭头", "solid"), ("实心双向", "solid_both"),
                ("尺寸箭头", "dimension"), ("曲线箭头", "curve")])
            combo.setToolTip("曲线箭头：选中后拖动圆点调整弧度；双击箭头添加或关闭注释")
            self._spin("arrow_size", "头部", 6, 60)
            # 切换箭头类型会自动配好两端，单独改端点属于低频操作。
            heads = [("无", "none"), ("开放", "open"), ("实心", "filled")]
            more += [("arrow_start", "起点样式", heads), ("arrow_end", "终点样式", heads)]
        elif tool_id == "polyline":
            self._separator()
            self._segment("polyline_cap", "端点", [("圆头", "round"), ("方头", "square")])
            self._segment("polyline_join", "拐角", [("圆角", "round"), ("尖角", "miter")])
        elif tool_id == "line":
            self._separator()
            self._combo("line_style", None, [("实线", "solid"), ("虚线", "dash"), ("点线", "dot"),
                                             ("双线", "double"), ("波浪线", "wavy")])
        elif tool_id == "text":
            self._separator()
            self._font()
            self._spin("font_size", None, 6, 96).setToolTip("字号")
            self._segment("text_alignment", None, [("左", "left"), ("中", "center"), ("右", "right")])
            self._toggle("text_background", "背景", "background_color")
            self._toggle("text_outline", "描边", "outline_color")
        elif tool_id == "serial":
            self._separator()
            self._combo("serial_type", None, [("圆形数字", "circle"), ("描边圆形", "circle_outline"),
                                              ("方形数字", "square"), ("圆形字母", "letter")])
            self._spin("serial_size", "大小", 12, 96)
            self._segment("serial_start", "起点", [("0", 0), ("1", 1)])
            self._serial_adjust()
            more += [("serial_auto_renumber", "删除后重新编号"), ("serial_global", "跨截图连续编号")]
        elif tool_id == "mosaic":
            self._segment("mosaic_mode", None, [("像素化", "pixel"), ("模糊", "blur")])
            self._spin("mosaic_strength", "强度", 2, 40)
        elif tool_id == "highlight":
            self._spin("opacity", "不透明度", 5, 100).setSuffix("%")
        elif tool_id == "eraser":
            self._spin("eraser_size", "大小", 4, 120)
            self._label("填充")
            self._color_button("background_color")
        elif tool_id == "magnifier":
            self._label("像素预览 11 × 11 · 8 倍，滚轮调节倍率")
        if tool_id != "magnifier":
            more.append(("wheel_enabled", "滚轮调节大小"))
        self._layout.addStretch()
        self._more(more)
        self._sync(tool_id)
        self.adjustSize()

    # ---- 控件构造 ----

    def _bind(self, key, widget, refresh):
        self._bindings.append((key, widget, refresh))

    def _label(self, text):
        label = QLabel(text)
        self._layout.addWidget(label)
        return label

    def _separator(self):
        sep = QFrame()
        sep.setObjectName("OptionSeparator")
        self._layout.addWidget(sep)

    def _row(self):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._layout.addWidget(row)
        return row

    def _colors(self, key):
        row = self._row()
        current = self._color_button(key, row)
        current.setToolTip("当前颜色，点击自定义")
        group = QButtonGroup(row)
        buttons = {}
        for name, color in zip(_COLOR_NAMES, T.ANNOTATION_COLORS):
            button = self._swatch_button(row, name)
            button.setIcon(_swatch_icon(color))
            button.clicked.connect(lambda _=False, c=color: self._pick_preset(key, c))
            group.addButton(button)
            buttons[color] = button

        def refresh(value):
            # 自定义色不在预设里时，预设都不选中，只看左侧当前色。
            group.setExclusive(False)
            for color, button in buttons.items():
                button.setChecked(color == value.name())
            group.setExclusive(True)

        self._bind(key, row, refresh)

    def _swatch_button(self, row, tip):
        button = QToolButton()
        button.setObjectName("Swatch")
        button.setCheckable(True)
        button.setToolTip(tip)
        button.setIconSize(QSize(_SWATCH, _SWATCH))
        row.layout().addWidget(button)
        return button

    def _color_button(self, key, row=None):
        button = QToolButton()
        button.setObjectName("Swatch")
        button.setToolTip("选择颜色")
        button.setIconSize(QSize(_SWATCH, _SWATCH))
        button.clicked.connect(lambda: self._choose_color(key))
        (row.layout() if row is not None else self._layout).addWidget(button)
        self._bind(key, button, lambda value: button.setIcon(_swatch_icon(value, current=True)))
        return button

    def _width(self):
        row = self._row()
        group = QButtonGroup(row)
        presets = {}
        for (value, name), diameter in zip(_WIDTH_PRESETS, (4, 7, 11)):
            button = QToolButton()
            button.setObjectName("Swatch")
            button.setCheckable(True)
            button.setToolTip(f"{name}（{value}）")
            button.setIconSize(QSize(_SWATCH, _SWATCH))
            button.setIcon(_dot_icon(diameter))
            button.clicked.connect(lambda _=False, v=value: self._canvas.update_style("width", v))
            group.addButton(button)
            presets[value] = button
            row.layout().addWidget(button)
        spin = self._spin("width", None, 1, 30)
        spin.setToolTip("粗细")

        def refresh(value):
            # 非预设值时三个圆点都不选中，数字框显示真实值。
            group.setExclusive(False)
            for preset, button in presets.items():
                button.setChecked(preset == value)
            group.setExclusive(True)

        self._bind("width", row, refresh)

    def _spin(self, key, title, low, high):
        if title:
            self._label(title)
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setButtonSymbols(QSpinBox.NoButtons)
        spin.setAlignment(Qt.AlignCenter)
        spin.valueChanged.connect(lambda value: self._canvas.update_style(key, value))
        self._layout.addWidget(spin)
        self._bind(key, spin, spin.setValue)
        return spin

    def _segment(self, key, title, values):
        if title:
            self._label(title)
        frame = QFrame()
        frame.setObjectName("SegmentGroup")
        row = QHBoxLayout(frame)
        row.setContentsMargins(1, 1, 1, 1)
        row.setSpacing(2)
        group = QButtonGroup(frame)
        buttons = {}
        for label, value in values:
            button = QToolButton()
            button.setObjectName("Segment")
            button.setText(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, v=value: self._canvas.update_style(key, v))
            group.addButton(button)
            row.addWidget(button)
            buttons[value] = button
        self._layout.addWidget(frame)
        self._bind(key, frame, lambda value: buttons[value].setChecked(True))

    def _geo_shape(self, tool_id):
        # 同 PixPin：矩形和椭圆是同一个几何工具，形状在这里切换；只在画新图形时出现，选中已有图形时不改它的形状。
        frame = QFrame()
        frame.setObjectName("SegmentGroup")
        row = QHBoxLayout(frame)
        row.setContentsMargins(1, 1, 1, 1)
        row.setSpacing(2)
        group = QButtonGroup(frame)
        for label, shape in (("矩形", "rect"), ("椭圆", "ellipse")):
            button = QToolButton()
            button.setObjectName("Segment")
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(shape == tool_id)
            button.clicked.connect(lambda _=False, s=shape: self._canvas.set_tool_by_id(s))
            group.addButton(button)
            row.addWidget(button)
        self._layout.addWidget(frame)

    def _combo(self, key, title, values):
        if title:
            self._label(title)
        combo = QComboBox()
        for label, value in values:
            combo.addItem(label, value)
        combo.currentIndexChanged.connect(lambda _: self._canvas.update_style(key, combo.currentData()))
        self._layout.addWidget(combo)
        self._bind(key, combo, lambda value: combo.setCurrentIndex(combo.findData(value)))
        return combo

    def _font(self):
        font = QFontComboBox()
        font.setMaximumWidth(120)
        font.setToolTip("字体")
        font.currentFontChanged.connect(lambda value: self._canvas.update_style("font_family", value.family()))
        self._layout.addWidget(font)
        self._bind("font_family", font,
                   lambda value: font.setCurrentFont(QFont(value, self._canvas.style.font_size)))

    def _toggle(self, key, title, color_key):
        button = QToolButton()
        button.setObjectName("Segment")
        button.setText(title)
        button.setCheckable(True)
        button.toggled.connect(lambda value: self._canvas.update_style(key, value))
        self._layout.addWidget(button)
        self._bind(key, button, button.setChecked)
        self._color_button(color_key)

    def _serial_adjust(self):
        self._label("微调")
        for text, delta in (("−", -1), ("+", 1)):
            button = QToolButton()
            button.setObjectName("Segment")
            button.setText(text)
            button.setToolTip("选中序号后调整编号")
            button.clicked.connect(lambda _=False, d=delta: self._canvas.adjust_serial(d))
            self._layout.addWidget(button)

    def _more(self, entries):
        if not entries:
            return
        button = QToolButton()
        button.setToolTip("更多选项")
        button.setIcon(tinted_icon("settings", _theme()["muted_foreground"], 16))
        button.setPopupMode(QToolButton.InstantPopup)
        button.setObjectName("MoreOptions")
        menu = QMenu(button)
        for key, title, *choices in entries:
            if choices:
                self._menu_choice(menu.addMenu(title), key, choices[0])
                continue
            action = menu.addAction(title)
            action.setCheckable(True)
            action.toggled.connect(lambda value, k=key: self._canvas.update_style(k, value))
            self._bind(key, action, action.setChecked)
        button.setMenu(menu)
        self._layout.addWidget(button)

    def _menu_choice(self, menu, key, values):
        actions = {}
        for label, value in values:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _=False, v=value: self._canvas.update_style(key, v))
            actions[value] = action

        def refresh(value):
            for option, action in actions.items():
                action.setChecked(option == value)

        self._bind(key, menu, refresh)

    def _pick_preset(self, key, name):
        # 透明度是 Ctrl+滚轮单独调的，换预设色时保留它。
        color = QColor(name)
        color.setAlpha(getattr(self._canvas.style, key).alpha())
        self._canvas.update_style(key, color)

    def _choose_color(self, key):
        color = QColorDialog.getColor(getattr(self._canvas.style, key), self, "选择颜色",
                                      QColorDialog.ShowAlphaChannel)
        if color.isValid():
            self._canvas.update_style(key, color)

    def _sync(self, tool_id):
        if tool_id != self._tool_id:
            return
        style = self._canvas.style
        for key, widget, refresh in self._bindings:
            widget.setEnabled(self._canvas.style_key_enabled(key))
            widget.blockSignals(True)
            refresh(getattr(style, key))
            widget.blockSignals(False)
