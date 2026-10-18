"""全局短提示：截图会话结束后已没有窗口可承载反馈，复制、保存的结果由它告知。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget
from styles import tokens as T


def show_toast(text):
    toast = QWidget(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowTransparentForInput
                    | Qt.WindowDoesNotAcceptFocus)
    # 顶层窗口透明，圆角深底交给子控件 QLabel#Toast 的 QSS 画：顶层自己的 QSS 底色在透明窗口上不绘制。
    toast.setAttribute(Qt.WA_TranslucentBackground)
    toast.setAttribute(Qt.WA_ShowWithoutActivating)
    toast.setAttribute(Qt.WA_DeleteOnClose)
    layout = QHBoxLayout(toast)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(text)
    label.setObjectName("Toast")
    layout.addWidget(label)
    toast.adjustSize()
    # 放在鼠标所在屏幕底部居中：不挡住刚操作的位置，也不会落到别的屏幕上。
    area = QGuiApplication.screenAt(QCursor.pos()).availableGeometry()
    toast.move(area.center().x() - toast.width() // 2, area.bottom() - toast.height() - 48)
    toast.show()
    QTimer.singleShot(T.TOAST_MS, toast.close)
    return toast
