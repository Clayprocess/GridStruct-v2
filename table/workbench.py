"""表格校对的悬浮操作组和金额预览；不持有校对文档。"""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QStyle, QStyleOptionViewItem, QToolButton, QApplication

from styles import tokens as T
from ui import MultilineDelegate

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

    def place(self):
        buttons = [b for b in self.findChildren(QToolButton) if b.property("labelled")]
        def measure():
            widths, heights = [], []
            for i in range(self.row.count()):
                group = self.row.itemAt(i).widget()
                if group.isHidden():
                    continue
                row = group.layout()
                row.invalidate()
                row.activate()
                widths.append(row.sizeHint().width())
                heights.append(row.sizeHint().height())
            margin = self.row.contentsMargins()
            return sum(widths) + max(0, len(widths) - 1) * self.row.spacing() + margin.left() + margin.right(), max(heights, default=0) + margin.top() + margin.bottom()
        for button in buttons:
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        available = self._area.viewport().width() - 24
        for button in reversed(buttons):
            if measure()[0] <= available:
                break
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        width, height = measure()
        self.row.invalidate()
        self.resize(width, height)
        self.row.activate()
        viewport = self._area.viewport().geometry()
        y = viewport.bottom() - self.height() - 7 if self._bottom else viewport.top() + 12
        self.move(viewport.left() + (8 if self._bottom else 12), y)
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.place()
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
