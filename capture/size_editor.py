"""点击选区尺寸提示弹出的宽高输入框。"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QSpinBox, QToolButton, QWidget
from styles import tokens as T
from ui import tinted_icon


class SizeEditor(QWidget):
    """同 PixPin：直接输入选区宽高，可锁定当前宽高比。单位与尺寸提示一致，是物理像素。"""

    def __init__(self, size, apply):
        # Popup：点到外面自动关闭，键盘也只发给它，不会被截图遮罩的快捷键截走。
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
        # 顶层窗口透明，圆角面板交给子控件 QFrame#SizeEditor 画：顶层自己的 QSS 底色在透明窗口上不绘制。
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._apply = apply
        self._ratio = size.width() / size.height()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("SizeEditor")
        outer.addWidget(panel)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        self._w = self._spin(size.width(), "宽")
        self._h = self._spin(size.height(), "高")
        layout.addWidget(self._w)
        layout.addWidget(QLabel("×"))
        layout.addWidget(self._h)
        self._lock = QToolButton()
        self._lock.setProperty("class", "ghost")
        self._lock.setCheckable(True)
        self._lock.setToolTip("锁定宽高比")
        mode = QApplication.instance().property("theme_mode") or "light"
        self._lock.setIcon(tinted_icon("lock", T.THEMES[mode]["foreground"], 16))
        layout.addWidget(self._lock)
        self._w.valueChanged.connect(lambda value: self._follow(self._h, value / self._ratio))
        self._h.valueChanged.connect(lambda value: self._follow(self._w, value * self._ratio))
        self._w.selectAll()
        self._w.setFocus()

    def _spin(self, value, name):
        spin = QSpinBox()
        spin.setRange(1, 99999)
        spin.setValue(value)
        spin.setFixedWidth(72)
        spin.setAccessibleName(name)
        return spin

    def _follow(self, spin, value):
        if self._lock.isChecked():
            spin.blockSignals(True)
            spin.setValue(max(1, round(value)))
            spin.blockSignals(False)

    def keyPressEvent(self, event):
        # QSpinBox 处理完回车会把事件交给父控件，这里统一确认。
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._apply(QSize(self._w.value(), self._h.value()))
            self.close()
        else:
            super().keyPressEvent(event)
