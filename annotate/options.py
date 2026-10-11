"""当前工具的二级配置；未验证的 PixPin 默认范围采用本地可用值。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox,
    QCheckBox, QPushButton, QColorDialog, QFontComboBox, QSlider,
)

from styles import tokens as T


class ToolOptions(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(6, 4, 6, 4)
        self._controls = {}
        self._choices = {}
        self._colors = {}
        self._tool_id = ""
        canvas.styleChanged.connect(self._sync)
        self.setVisible(False)

    def set_tool(self, tool_id):
        while self._layout.count():
            item = self._layout.takeAt(0)
            item.widget().deleteLater()
        self._controls.clear()
        self._choices.clear()
        self._colors.clear()
        self._tool_id = tool_id
        self.setVisible(bool(tool_id))
        if not tool_id:
            return
        if tool_id not in ("mosaic", "eraser", "magnifier"):
            self._color("color", "颜色")
        if tool_id in ("rect", "ellipse", "arrow", "line", "polyline", "pencil", "highlight"):
            self._number("width", "粗细", 1, 30)
        if tool_id in ("rect", "ellipse"):
            self._choice("fill_mode", "填充", [("描边", "outline"), ("填充", "fill"), ("填充及描边", "both")])
        if tool_id == "rect":
            self._number("round_radius", "圆角", 0, 64)
        elif tool_id == "arrow":
            self._choice("arrow_type", "箭头", [("直线箭头", "straight"), ("直线双向", "straight_both"),
                         ("线形箭头", "line"), ("线形双向", "line_both"), ("实心箭头", "solid"),
                         ("实心双向", "solid_both"), ("尺寸箭头", "dimension"), ("曲线箭头", "curve")])
            self._number("arrow_size", "箭头大小", 6, 60)
            heads = [("无", "none"), ("开放", "open"), ("实心", "filled")]
            self._choice("arrow_start", "起点", heads)
            self._choice("arrow_end", "终点", heads)
            self._add("调整", QLabel("选中曲线后拖圆点；双击箭头添加/关闭注释"))
        elif tool_id == "polyline":
            self._choice("polyline_cap", "端点", [("圆头", "round"), ("方头", "square")])
            self._choice("polyline_join", "连接", [("圆角", "round"), ("尖角", "miter")])
        elif tool_id == "line":
            self._choice("line_style", "线型", [("实线", "solid"), ("虚线", "dash"), ("点线", "dot"),
                                                   ("双线", "double"), ("波浪线", "wavy")])
        elif tool_id == "text":
            font = QFontComboBox()
            font.setMaximumWidth(180)
            font.setCurrentFont(QFont(self._canvas.style.font_family, self._canvas.style.font_size))
            font.currentFontChanged.connect(lambda value: self._canvas.update_style("font_family", value.family()))
            self._add("字体", font)
            self._number("font_size", "字号", 6, 96)
            self._choice("text_alignment", "对齐", [("左", "left"), ("中", "center"), ("右", "right")])
            self._check("text_background", "背景")
            self._color("background_color", "背景色")
            self._check("text_outline", "描边")
            self._color("outline_color", "描边色")
        elif tool_id == "serial":
            self._number("serial_size", "大小", 12, 96)
            self._choice("serial_start", "起点", [("从 0 开始", 0), ("从 1 开始", 1)])
            self._check("serial_auto_renumber", "删除后重排")
            self._check("serial_global", "跨截图连续编号")
            # TODO：逆向材料未确认其他视觉类型，当前仅实现圆形数字。
            serial_type = QLabel("圆形数字")
            self._add("类型", serial_type)
            adjust = QWidget()
            row = QHBoxLayout(adjust)
            row.setContentsMargins(0, 0, 0, 0)
            for text, delta in (("−", -1), ("+", 1)):
                button = QPushButton(text)
                button.setToolTip("调整选中序号")
                button.clicked.connect(lambda checked=False, delta=delta: self._canvas.adjust_serial(delta))
                row.addWidget(button)
            self._add("微调", adjust)
        elif tool_id == "mosaic":
            self._choice("mosaic_mode", "模式", [("像素化", "pixel"), ("模糊", "blur")])
            self._number("mosaic_strength", "强度", 2, 40)
        elif tool_id == "highlight":
            self._number("opacity", "不透明度 %", 5, 100)
        elif tool_id == "eraser":
            self._number("eraser_size", "大小", 4, 120)
        elif tool_id == "magnifier":
            self._add("像素预览", QLabel("11 × 11 · 8 倍"))
        if tool_id != "magnifier":
            self._check("wheel_enabled", "滚轮调节")
        self.adjustSize()

    def _add(self, title, widget):
        index = self._layout.count() // 2
        row, column = divmod(index, 3)
        self._layout.addWidget(QLabel(title), row, column * 2)
        self._layout.addWidget(widget, row, column * 2 + 1)

    def _number(self, key, title, low, high):
        control = QWidget()
        row = QHBoxLayout(control)
        row.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(low, high)
        slider.setMinimumWidth(60)
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(getattr(self._canvas.style, key))
        slider.setValue(spin.value())
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        spin.valueChanged.connect(lambda value: self._canvas.update_style(key, value))
        row.addWidget(slider)
        row.addWidget(spin)
        self._controls[key] = (spin, slider)
        self._add(title, control)

    def _choice(self, key, title, values):
        combo = QComboBox()
        for label, value in values:
            combo.addItem(label, value)
        combo.setCurrentIndex(combo.findData(getattr(self._canvas.style, key)))
        combo.currentIndexChanged.connect(lambda _: self._canvas.update_style(key, combo.currentData()))
        self._choices[key] = combo
        self._add(title, combo)

    def _check(self, key, title):
        box = QCheckBox()
        box.setChecked(getattr(self._canvas.style, key))
        box.toggled.connect(lambda value: self._canvas.update_style(key, value))
        self._add(title, box)

    def _color(self, key, title):
        control = QWidget()
        row = QHBoxLayout(control)
        row.setContentsMargins(0, 0, 0, 0)
        presets = QComboBox()
        for name, color in zip(("红", "黑", "白", "蓝", "绿", "黄"), T.ANNOTATION_COLORS):
            presets.addItem(name, color)
        selected = presets.findData(getattr(self._canvas.style, key).name())
        presets.setCurrentIndex(selected)
        presets.currentIndexChanged.connect(lambda _: self._canvas.update_style(key, QColor(presets.currentData())))
        custom = QPushButton("自定义")
        custom.clicked.connect(lambda: self._choose_color(key))
        row.addWidget(presets)
        row.addWidget(custom)
        self._colors[key] = presets
        self._add(title, control)

    def _choose_color(self, key):
        color = QColorDialog.getColor(getattr(self._canvas.style, key), self, "选择颜色")
        if color.isValid():
            self._canvas.update_style(key, color)

    def _sync(self, tool_id):
        if tool_id != self._tool_id:
            return
        for key, combo in self._choices.items():
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(getattr(self._canvas.style, key)))
            combo.blockSignals(False)
        for key, combo in self._colors.items():
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(getattr(self._canvas.style, key).name()))
            combo.blockSignals(False)
        for key, (spin, slider) in self._controls.items():
            value = getattr(self._canvas.style, key)
            spin.blockSignals(True)
            slider.blockSignals(True)
            spin.setValue(value)
            slider.setValue(value)
            spin.blockSignals(False)
            slider.blockSignals(False)
