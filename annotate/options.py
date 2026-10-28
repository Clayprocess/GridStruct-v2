"""当前工具的二级配置：单行紧凑条，高频项平铺，开关类收进"更多"菜单。

样式选项一律画成图形（形状、端点、对齐、效果预览），文字只留在提示和下拉列表里。
"""

from PySide6.QtCore import Qt, QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSpinBox, QToolButton,
    QButtonGroup, QColorDialog, QFontComboBox, QFrame, QMenu, QSizePolicy, QWidgetAction,
)

from styles import tokens as T
from ui import round_corners, tinted_icon
from ui.motion import SelectionPill, fade_in
from .items import ArrowMarkItem, LineMarkItem, MarkStyle, SerialMarkItem, arrow_heads

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
        # 深色主题的 border 与面板几乎同色，黑色色块会消失，描边用专门的 swatch_ring。
        p.setPen(QPen(QColor(_theme()["swatch_ring"]), 1))
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


_ICON = QSize(_SWATCH, _SWATCH)
# 下拉预览要横向展开才看得出箭头头部和线型差别。
_PREVIEW = QSize(36, 16)


def _paint(size, draw):
    pm = QPixmap(size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    with QPainter(pm) as p:
        p.setRenderHint(QPainter.Antialiasing)
        draw(p, QRectF(0, 0, size.width(), size.height()))
    return pm


def _two_state(draw, size=_ICON):
    """未选中用弱化色、选中用前景色，和分段控件里文字的两态一致。"""
    t = _theme()
    icon = QIcon()
    for state, key in ((QIcon.Off, "muted_foreground"), (QIcon.On, "foreground")):
        color = QColor(t[key])
        icon.addPixmap(_paint(size, lambda p, r: draw(p, color, r)), QIcon.Normal, state)
    return icon


def _lucide(name):
    return _two_state(lambda p, c, r: tinted_icon(name, c.name(), _SWATCH).paint(p, r.toRect()))


def _fill_icon(mode):
    def draw(p, c, r):
        soft = QColor(c)
        soft.setAlphaF(0.45)
        p.setPen(QPen(c, 1.5) if mode != "fill" else Qt.NoPen)
        p.setBrush(soft if mode != "outline" else Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(2.5, 3.5, -2.5, -3.5), 2, 2)
    return _two_state(draw)


def _cap_icon(cap):
    def draw(p, c, r):
        p.setPen(QPen(c, 6, Qt.SolidLine, cap))
        p.drawLine(QPointF(5, r.center().y()), QPointF(r.right() - 5, r.center().y()))
    return _two_state(draw)


def _join_icon(join):
    def draw(p, c, r):
        p.setPen(QPen(c, 4, Qt.SolidLine, Qt.FlatCap, join))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline([QPointF(2.5, 13), QPointF(8, 5), QPointF(13.5, 13)])
    return _two_state(draw)


def _blur_icon():
    def draw(p, c, r):
        gradient = QRadialGradient(r.center(), 7)
        gradient.setColorAt(0, c)
        edge = QColor(c)
        edge.setAlpha(0)
        gradient.setColorAt(1, edge)
        p.setPen(Qt.NoPen)
        p.setBrush(gradient)
        p.drawEllipse(r.center(), 7, 7)
    return _two_state(draw)


def _letter_path(r):
    font = QFont()
    font.setPixelSize(13)
    font.setBold(True)
    path = QPainterPath()
    path.addText(0, 0, font, "A")
    box = path.boundingRect()
    return path.translated(r.center() - box.center())


def _text_background_icon():
    def draw(p, c, r):
        # 字母从色块里镂空，明暗主题下都不用另配字色。
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.drawPath(_letter_path(r))
    return _two_state(draw)


def _text_outline_icon():
    def draw(p, c, r):
        p.setPen(QPen(c, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(_letter_path(r))
    return _two_state(draw)


def _number_icon(number):
    def draw(p, c, r):
        p.setPen(QPen(c, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r.center(), 6.5, 6.5)
        font = QFont()
        font.setPixelSize(9)
        font.setBold(True)
        p.setFont(font)
        p.drawText(r, Qt.AlignCenter, str(number))
    return _two_state(draw)


def _arrow_preview(p, color, r, arrow_type):
    style = MarkStyle()
    style.color, style.width, style.arrow_size, style.arrow_type = color, 2, 7, arrow_type
    style.arrow_start, style.arrow_end = arrow_heads(arrow_type)
    item = ArrowMarkItem(style)
    item.set_line(QPointF(r.left() + 3, r.center().y()), QPointF(r.right() - 3, r.center().y()))
    item.paint(p, None, None)


def _line_preview(p, color, r, line_style):
    style = MarkStyle()
    style.color, style.width, style.line_style = color, 2, line_style
    item = LineMarkItem(style)
    item.set_line(QPointF(r.left() + 3, r.center().y()), QPointF(r.right() - 3, r.center().y()))
    item.paint(p, None, None)


def _serial_preview(p, _color, r, serial_type):
    # 序号预览用真实的标注红：白字画在前景色上，深色主题里会看不见。
    style = MarkStyle()
    style.serial_type, style.serial_size = serial_type, 15
    p.translate(r.center())
    SerialMarkItem(style, 1).paint(p, None, None)


class ToolOptions(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolOptions")
        self._canvas = canvas
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 6, 8, 6)
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
            self._segment("fill_mode", [("描边", "outline", _fill_icon("outline")),
                                        ("填充", "fill", _fill_icon("fill")),
                                        ("描边加填充", "both", _fill_icon("both"))])
        if tool_id == "rect":
            self._spin("round_radius", "圆角", 0, 64)
        elif tool_id == "arrow":
            self._separator()
            self._picker("arrow_type", "箭头样式；曲线箭头选中后拖动圆点调整弧度，双击箭头添加或关闭注释", [
                ("直线箭头", "straight"), ("直线双向", "straight_both"), ("线形箭头", "line"),
                ("线形双向", "line_both"), ("实心箭头", "solid"), ("实心双向", "solid_both"),
                ("尺寸箭头", "dimension"), ("曲线箭头", "curve")], _arrow_preview)
            self._spin("arrow_size", "头部", 6, 60)
            # 切换箭头类型会自动配好两端，单独改端点属于低频操作。
            heads = [("无", "none"), ("开放", "open"), ("实心", "filled")]
            more += [("arrow_start", "起点样式", heads), ("arrow_end", "终点样式", heads)]
        elif tool_id == "polyline":
            self._separator()
            self._segment("polyline_cap", [("圆头端点", "round", _cap_icon(Qt.RoundCap)),
                                           ("方头端点", "square", _cap_icon(Qt.SquareCap))])
            self._segment("polyline_join", [("圆角拐角", "round", _join_icon(Qt.RoundJoin)),
                                            ("尖角拐角", "miter", _join_icon(Qt.MiterJoin))])
        elif tool_id == "line":
            self._separator()
            self._picker("line_style", "线型", [("实线", "solid"), ("虚线", "dash"), ("点线", "dot"),
                                              ("双线", "double"), ("波浪线", "wavy")], _line_preview)
        elif tool_id == "text":
            self._separator()
            self._font()
            self._spin("font_size", None, 6, 96).setToolTip("字号")
            self._segment("text_alignment", [("左对齐", "left", _lucide("text-align-start")),
                                             ("居中", "center", _lucide("text-align-center")),
                                             ("右对齐", "right", _lucide("text-align-end"))])
            self._toggle("text_background", "文字背景", _text_background_icon(), "background_color")
            self._toggle("text_outline", "文字描边", _text_outline_icon(), "outline_color")
        elif tool_id == "serial":
            self._separator()
            self._picker("serial_type", "序号样式", [("圆形数字", "circle"), ("描边圆形", "circle_outline"),
                                                ("方形数字", "square"), ("圆形字母", "letter")], _serial_preview)
            self._spin("serial_size", "大小", 12, 96)
            self._segment("serial_start", [("从 0 开始编号", 0, _number_icon(0)),
                                           ("从 1 开始编号", 1, _number_icon(1))])
            self._serial_adjust()
            more += [("serial_auto_renumber", "删除后重新编号"), ("serial_global", "跨截图连续编号")]
        elif tool_id == "mosaic":
            self._segment("mosaic_mode", [("像素化", "pixel", _lucide("checkerboard")),
                                          ("模糊", "blur", _blur_icon())])
            self._spin("mosaic_strength", "强度", 2, 40)
        elif tool_id == "highlight":
            self._spin("opacity", "不透明度", 5, 100).setSuffix("%")
        elif tool_id == "eraser":
            self._spin("eraser_size", "大小", 4, 120)
            self._icon_label("paint-bucket", "擦除后填充的颜色")
            self._color_button("background_color")
        elif tool_id == "magnifier":
            self._label("像素预览 15 × 7，移动鼠标查看")
        if tool_id != "magnifier":
            more.append(("wheel_enabled", "滚轮调节大小"))
        self._layout.addStretch()
        self._more(more)
        self._sync(tool_id)
        self.adjustSize()
        fade_in(self)

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

    def _icon_label(self, name, tip):
        label = QLabel()
        label.setPixmap(tinted_icon(name, _theme()["muted_foreground"], _SWATCH).pixmap(_ICON))
        label.setToolTip(tip)
        self._layout.addWidget(label)

    def _segment_frame(self):
        frame = QFrame()
        frame.setObjectName("SegmentGroup")
        row = QHBoxLayout(frame)
        row.setContentsMargins(1, 1, 1, 1)
        row.setSpacing(2)
        self._layout.addWidget(frame)
        return frame, QButtonGroup(frame), SelectionPill(frame, "background", "border")

    def _segment_button(self, frame, group, pill, tip, icon):
        button = QToolButton()
        button.setObjectName("Segment")
        button.setProperty("pill", True)
        button.setToolTip(tip)
        button.setIcon(icon)
        button.setIconSize(_ICON)
        button.setCheckable(True)
        button.toggled.connect(lambda on: on and pill.track(button))
        group.addButton(button)
        frame.layout().addWidget(button)
        return button

    def _segment(self, key, values):
        """values: (提示, 取值, 图标)。选中底块在各段之间滑动。"""
        frame, group, pill = self._segment_frame()
        buttons = {}
        for tip, value, icon in values:
            button = self._segment_button(frame, group, pill, tip, icon)
            button.clicked.connect(lambda _=False, v=value: self._canvas.update_style(key, v))
            buttons[value] = button
        self._bind(key, frame, lambda value: buttons[value].setChecked(True))

    def _geo_shape(self, tool_id):
        # 同 PixPin：矩形和椭圆是同一个几何工具，形状在这里切换；只在画新图形时出现，选中已有图形时不改它的形状。
        frame, group, pill = self._segment_frame()
        for tip, shape, icon_name in (("矩形", "rect", "square"), ("椭圆", "ellipse", "circle")):
            button = self._segment_button(frame, group, pill, tip, _lucide(icon_name))
            button.setChecked(shape == tool_id)
            button.clicked.connect(lambda _=False, s=shape: self._canvas.set_tool_by_id(s))

    def _picker(self, key, tip, values, preview):
        """样式下拉：按钮和列表都画真实图元的效果预览，比“线形双向”这类名称直观。"""
        button = QToolButton()
        button.setObjectName("OptionPicker")
        button.setToolTip(tip)
        button.setIconSize(_PREVIEW)
        button.setPopupMode(QToolButton.InstantPopup)
        menu = round_corners(QMenu(button))
        panel = QWidget()
        panel.setObjectName("PickerPanel")
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        group = QButtonGroup(panel)
        items = {}
        list_color = QColor(_theme()["popover_foreground"])
        for label, value in values:
            item = QToolButton()
            item.setObjectName("PickerItem")
            item.setCheckable(True)
            item.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            item.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            item.setText(label)
            item.setIconSize(_PREVIEW)
            item.setIcon(QIcon(_paint(_PREVIEW, lambda p, r, v=value: preview(p, list_color, r, v))))
            item.clicked.connect(lambda _=False, v=value: (menu.close(), self._canvas.update_style(key, v)))
            group.addButton(item)
            column.addWidget(item)
            items[value] = item
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        button.setMenu(menu)
        self._layout.addWidget(button)
        color = QColor(_theme()["foreground"])

        def refresh(value):
            items[value].setChecked(True)
            button.setIcon(QIcon(_paint(_PREVIEW, lambda p, r: preview(p, color, r, value))))

        self._bind(key, button, refresh)

    def _font(self):
        font = QFontComboBox()
        font.setMaximumWidth(120)
        font.setToolTip("字体")
        font.currentFontChanged.connect(lambda value: self._canvas.update_style("font_family", value.family()))
        self._layout.addWidget(font)
        self._bind("font_family", font,
                   lambda value: font.setCurrentFont(QFont(value, self._canvas.style.font_size)))

    def _toggle(self, key, tip, icon, color_key):
        button = QToolButton()
        button.setObjectName("Segment")
        button.setToolTip(tip)
        button.setIcon(icon)
        button.setIconSize(_ICON)
        button.setCheckable(True)
        button.toggled.connect(lambda value: self._canvas.update_style(key, value))
        self._layout.addWidget(button)
        # 颜色块只在开关打开时出现：关着时一块白色色块看起来像个空复选框。
        swatch = self._color_button(color_key)

        def refresh(value):
            button.setChecked(value)
            swatch.setVisible(value)

        self._bind(key, button, refresh)

    def _serial_adjust(self):
        for icon_name, delta in (("minus", -1), ("plus", 1)):
            button = QToolButton()
            button.setObjectName("Segment")
            button.setIcon(_lucide(icon_name))
            button.setIconSize(_ICON)
            button.setToolTip(f"选中的序号{'减' if delta < 0 else '加'} 1")
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
        menu = round_corners(QMenu(button))
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
