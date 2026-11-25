"""表格校对的悬浮操作组和金额预览；不持有校对文档。"""

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QStyle, QStyleOptionViewItem, QToolButton, QApplication, QScrollArea, QTabBar

from styles import tokens as T
from ui import MultilineDelegate, tinted_icon

AMOUNT_ROLE = Qt.UserRole + 17


class FloatingTools(QFrame):
    """工具组按内容收紧；窄面板改为图标按钮，始终保持一排。"""

    def __init__(self, area, bottom=False):
        super().__init__(area)
        self.setObjectName("ZoomBar" if bottom else "TableFloatingTools")
        self._area = area
        self._bottom = bottom
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(*( (2, 2, 2, 2) if bottom else (4, 4, 4, 4) ))
        self.row.setSpacing(6)
        area.viewport().installEventFilter(self)

    def group(self, *widgets):
        group = QFrame(self)
        group.setObjectName("TableToolGroup")
        row = QHBoxLayout(group)
        row.setContentsMargins(*( (0, 0, 0, 0) if self._bottom else (2, 2, 2, 2) ))
        row.setSpacing(2)
        for widget in widgets:
            row.addWidget(widget)
        self.row.addWidget(group)
        return group

    def set_area(self, area):
        """页码条随文字/表格模式移动，保留输入框与翻页信号连接。"""
        self._area.viewport().removeEventFilter(self)
        self._area = area
        self.setParent(area)
        area.viewport().installEventFilter(self)
        self.show()
        self.place()

    def place(self):
        buttons = [b for b in self.findChildren(QToolButton) if b.property("labelled")]
        def measure():
            for i in range(self.row.count()):
                group = self.row.itemAt(i).widget()
                group.layout().invalidate()
                group.layout().activate()
            self.row.invalidate()
            self.row.activate()
            # frame的QSS边框也占空间，不能只用子layout的sizeHint。
            return self.sizeHint()
        for button in buttons:
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        available = self._area.viewport().width() - (16 if self._bottom else 24)
        for button in reversed(buttons):
            if measure().width() <= available:
                break
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.resize(measure())
        self.row.activate()
        viewport = self._area.viewport().geometry()
        y = viewport.bottom() - self.height() - 7 if self._bottom else viewport.top() + 12
        self.move(viewport.left() + (8 if self._bottom else 12), y)
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.place()
        return False


class TableTabs(QTabBar):
    """表格标签槽宽不随选中、加粗或分析状态变化。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("TableTabs")
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setUsesScrollButtons(False)
        self.setElideMode(Qt.ElideNone)
        self.setIconSize(QSize(16, 16))
        self.setAccessibleName("当前页的表格")

    def tabSizeHint(self, index):
        return QSize(80 + max(0, len(str(self.count())) - 2) * 8, 28)

    def minimumTabSizeHint(self, index):
        return self.tabSizeHint(index)


class TablePicker(QFrame):
    """结果画布左下角的工作表标签；独立滚动，不挤占复核工具。"""

    def __init__(self, area, tabs, zoom_bar):
        super().__init__(area)
        self.setObjectName("TablePicker")
        self._area = area
        self._tabs = tabs
        self._zoom_bar = zoom_bar
        self._layout_signature = None
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(2, 2, 2, 2)
        self._row.setSpacing(2)
        self._back = self._arrow("chevron-left", "向左滚动工作表标签", -1)
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("TableTabViewport")
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(28)
        self._scroll.setWidget(tabs)
        self._row.addWidget(self._scroll, 1)
        self._forward = self._arrow("chevron-right", "向右滚动工作表标签", 1)
        self._scroll.horizontalScrollBar().valueChanged.connect(self._sync_arrows)
        self._scroll.horizontalScrollBar().rangeChanged.connect(self._sync_arrows)
        tabs.currentChanged.connect(lambda _index: QTimer.singleShot(0, self.reveal_current))
        area.viewport().installEventFilter(self)
        zoom_bar.installEventFilter(self)
        self.hide()

    def _arrow(self, icon, label, direction):
        button = QToolButton(self)
        button.setObjectName("TableTabArrow")
        mode = QApplication.instance().property("theme_mode") or "light"
        button.setIcon(tinted_icon(icon, T.THEMES[mode]["table_foreground"]))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(24, 28)
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.clicked.connect(lambda: self._scroll_by(direction))
        self._row.addWidget(button)
        return button

    def _scroll_by(self, direction):
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(bar.value() + direction * max(80, round(self._scroll.viewport().width() * .8)))

    def _sync_arrows(self, *_args):
        bar = self._scroll.horizontalScrollBar()
        self._back.setEnabled(bar.value() > bar.minimum())
        self._forward.setEnabled(bar.value() < bar.maximum())

    def refresh(self, reveal=False):
        if self._tabs.count() < 2:
            self.hide()
            return
        self.show()
        viewport = self._area.viewport().geometry()
        available = viewport.width() - self._zoom_bar.width() - 24
        above_zoom = available < 140
        if above_zoom:
            available = viewport.width() - 16
        tab_width = self._tabs.tabSizeHint(0).width()
        content_width = tab_width * self._tabs.count()
        self._tabs.setFixedSize(content_width, 28)
        overflow = content_width + 6 > available
        self._back.setVisible(overflow)
        self._forward.setVisible(overflow)
        width = min(available, content_width + 6 + (52 if overflow else 0))
        self.resize(max(1, width), 34)
        self._row.activate()
        y = viewport.bottom() - self.height() - 7
        if above_zoom:
            y -= self._zoom_bar.height() + 8
        self.move(viewport.left() + 8, y)
        self.raise_()
        signature = (self._tabs.count(), available, tab_width)
        if reveal or signature != self._layout_signature:
            QTimer.singleShot(0, self.reveal_current)
        self._layout_signature = signature
        self._sync_arrows()

    def reveal_current(self):
        index = self._tabs.currentIndex()
        if index < 0:
            return
        rect = self._tabs.tabRect(index)
        bar = self._scroll.horizontalScrollBar()
        width = self._scroll.viewport().width()
        if rect.left() < bar.value():
            bar.setValue(rect.left())
        elif rect.right() >= bar.value() + width:
            bar.setValue(rect.right() - width + 1)
        self._sync_arrows()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.refresh()
        return False


class AmountDelegate(MultilineDelegate):
    def paint(self, painter, option, index):
        preview = index.data(AMOUNT_ROLE)
        if preview is None or preview.status not in ("changed", "ambiguous"):
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            current = bool(opt.state & QStyle.State_HasFocus)
            opt.state &= ~QStyle.State_HasFocus
            text = opt.text
            opt.text = ""
            opt.widget.style().drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
            colors = T.THEMES[QApplication.instance().property("theme_mode") or "light"]
            painter.save()
            painter.setClipRect(option.rect)
            selected = bool(opt.state & QStyle.State_Selected)
            background = index.data(Qt.BackgroundRole)
            if not selected and background is not None:
                painter.fillRect(option.rect, background)
            foreground = index.data(Qt.ForegroundRole)
            painter.setPen(QColor(colors["table_selected_foreground"] if selected else colors["table_foreground"]) if foreground is None or selected else foreground)
            painter.setFont(opt.font)
            painter.drawText(option.rect.adjusted(6, 2, -6, -2), int(opt.displayAlignment) | int(Qt.TextWordWrap), text)
            if current:
                painter.setPen(QPen(QColor(colors["table_focus"]), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()
            return
        mode = QApplication.instance().property("theme_mode") or "light"
        colors = T.THEMES[mode]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.state &= ~QStyle.State_HasFocus
        opt.widget.style().drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setClipRect(option.rect)
        rect = option.rect.adjusted(6, 2, -6, -2)
        if preview.status == "ambiguous":
            painter.fillRect(option.rect, QColor(colors["table_pending"]))
            painter.setPen(QColor(colors["table_pending_foreground"]))
            painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, preview.before + " ?")
        else:
            font = QFont(option.font)
            font.setPixelSize(T.FONT_SM)
            painter.setFont(font)
            half = rect.height() // 2
            before = rect.adjusted(0, 0, 0, -half)
            after = rect.adjusted(0, half, 0, 0)
            painter.setPen(QColor(colors["table_muted"]))
            painter.drawText(before, Qt.AlignRight | Qt.AlignVCenter, preview.before)
            painter.setPen(QColor(colors["table_foreground"]))
            painter.drawText(after, Qt.AlignRight | Qt.AlignVCenter, "→ " + preview.after)
        painter.restore()
