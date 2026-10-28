"""列表/表格的格内多行编辑器：表格识别和文字识别共用同一种改字手感。"""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QPlainTextEdit, QStyledItemDelegate


class MultilineDelegate(QStyledItemDelegate):
    """同 Excel：Enter 提交，Alt+Enter 在格内换行。"""

    def createEditor(self, parent, option, index):
        editor = QPlainTextEdit(parent)
        editor.setTabChangesFocus(True)
        editor.installEventFilter(self)
        return editor

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(Qt.EditRole) or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect
        rect.setHeight(max(rect.height(), editor.fontMetrics().lineSpacing() * 3))
        editor.setGeometry(rect)

    def eventFilter(self, editor, event):
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.AltModifier:
                editor.insertPlainText("\n")
            else:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QStyledItemDelegate.EditNextItem)
            return True
        return super().eventFilter(editor, event)
