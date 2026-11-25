"""文字与表格工作台共用的居中模式栏。"""

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QSizePolicy, QToolButton, QWidget

from styles import tokens as T
from .dialogs import topmost_button
from .icons import tinted_icon
from .motion import SelectionPill


class _ModePill(SelectionPill):
    def paintEvent(self, event):
        theme = T.THEMES[QApplication.instance().property("theme_mode") or "light"]
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor(theme[self._border_key]), 1))
            fill = "secondary_hover" if QApplication.instance().property("theme_mode") == "dark" else self._fill_key
            painter.setBrush(QColor(theme[fill]))
            rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)


class RecognitionModeBar(QWidget):
    modeRequested = Signal(str)

    def __init__(self, window, *, initial_mode, has_image):
        super().__init__(window)
        self.setObjectName("RecognitionModeBar")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(T.SPACE_4 * 3)
        self._has_image = has_image
        self._mode = "text"
        self._buttons = {}
        self._capsule = QFrame(self)
        self._capsule.setObjectName("RecognitionModeCapsule")
        self._capsule.setFixedSize(T.SPACE_4 * 9 + T.SPACE_2, T.SPACE_4 * 2 - 2)
        layout = QHBoxLayout(self._capsule)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._pill = _ModePill(self._capsule, "table_card", "table_border")
        for mode, label, icon in (("text", "文字", "type"), ("table", "表格", "table-2")):
            button = QToolButton(self._capsule)
            button.setObjectName("RecognitionModeSegment")
            button.setProperty("class", "ghost")
            button.setProperty("pill", True)
            button.setProperty("keyboardFocus", False)
            button.setText(label)
            button.setAccessibleName(label + "识别")
            button.setToolTip(label + "识别")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setAttribute(Qt.WA_MacShowFocusRect, False)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(16, 16))
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            button.setProperty("mode", mode)
            button.setProperty("mode_icon", icon)
            button.clicked.connect(lambda checked=False, requested=mode: self._request(requested))
            layout.addWidget(button)
            self._buttons[mode] = button
        if not has_image:
            table = self._buttons["table"]
            table.setEnabled(False)
            table.setToolTip("此历史记录没有原图，无法切换为表格识别")
            table.setAccessibleDescription(table.toolTip())
        QApplication.instance().installEventFilter(self)
        key = "window/ocr_topmost" if initial_mode == "text" else "window/table_topmost"
        self._pin = topmost_button(window, key)
        self._pin.setParent(self)
        self._pin.setProperty("class", "ghost")
        self._pin.setObjectName("RecognitionModePin")
        self._pin.setFixedSize(T.SPACE_4 * 2, T.SPACE_4 * 2)
        self._pin.setFocusPolicy(Qt.StrongFocus)
        self.set_mode(initial_mode)

    def sizeHint(self):
        return QSize(self._capsule.width() + T.SPACE_4 * 4, self.height())

    def set_mode(self, mode):
        if mode not in self._buttons:
            raise ValueError(f"Unknown recognition mode: {mode}")
        if mode == "table" and not self._has_image:
            raise ValueError("Table recognition requires an image")
        self._mode = mode
        for name, button in self._buttons.items():
            button.setChecked(name == mode)
        self._pill.track(self._buttons[mode])
        self._refresh_icons()

    def _request(self, mode):
        # 切换可能需要窗口先保存草稿；只有调用方确认后才移动选中块。
        self.set_mode(self._mode)
        if mode != self._mode:
            self.modeRequested.emit(mode)

    def _refresh_icons(self):
        theme = T.THEMES[QApplication.instance().property("theme_mode") or "light"]
        for mode, button in self._buttons.items():
            color = theme["table_foreground" if mode == self._mode else "table_muted"]
            icon = tinted_icon(button.property("mode_icon"), color, 16)
            icon = tinted_icon(button.property("mode_icon"), theme["table_muted"], 16,
                               icon, QIcon.Disabled, QIcon.Off)
            button.setIcon(icon)
        icon = tinted_icon("pin", theme["table_muted"], 16, QIcon(), QIcon.Normal, QIcon.Off)
        self._pin.setIcon(tinted_icon("pin", theme["table_foreground"], 16,
                                     icon, QIcon.Normal, QIcon.On))
        self._pill.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 独立于左右辅助按钮布局，图钉显隐也不会挪动模式栏的中心。
        self._pin.move(self.width() - self._pin.width() - T.SPACE_2,
                       (self.height() - self._pin.height()) // 2)
        self._capsule.move((self.width() - self._capsule.width()) // 2,
                           (self.height() - self._capsule.height()) // 2)

    def eventFilter(self, watched, event):
        if watched is QApplication.instance():
            if event.type() == QEvent.DynamicPropertyChange and event.propertyName() == b"theme_mode":
                self._refresh_icons()
        elif watched in self._buttons.values():
            if event.type() in (QEvent.FocusIn, QEvent.FocusOut):
                keyboard = event.type() == QEvent.FocusIn and event.reason() in (
                    Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason, Qt.OtherFocusReason)
                watched.setProperty("keyboardFocus", keyboard)
                watched.style().unpolish(watched)
                watched.style().polish(watched)
                watched.update()
            elif event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Left, Qt.Key_Right):
                target = self._buttons["text" if event.key() == Qt.Key_Left else "table"]
                if target.isEnabled():
                    target.setFocus(Qt.OtherFocusReason)
                    self._request(target.property("mode"))
                return True
        return super().eventFilter(watched, event)
