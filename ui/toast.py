"""全局短提示：截图会话结束后已没有窗口可承载反馈，复制、保存的结果由它告知。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget
from styles import tokens as T


def show_toast(text, action=None):
    """action 为 (按钮文字, 回调)：带按钮的提示要能点击，停留也更久。"""
    flags = Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
    if action is None:
        flags |= Qt.ToolTip | Qt.WindowTransparentForInput
    else:
        # ToolTip 类型窗口在 Windows 上点击即被系统收起，可点的提示改用置顶 Tool 窗口。
        flags |= Qt.Tool | Qt.WindowStaysOnTopHint
    toast = QWidget(None, flags)
    # 顶层窗口透明，圆角深底交给子控件 QFrame#Toast 的 QSS 画：顶层自己的 QSS 底色在透明窗口上不绘制。
    toast.setAttribute(Qt.WA_TranslucentBackground)
    toast.setAttribute(Qt.WA_ShowWithoutActivating)
    toast.setAttribute(Qt.WA_DeleteOnClose)
    outer = QHBoxLayout(toast)
    outer.setContentsMargins(0, 0, 0, 0)
    body = QFrame()
    body.setObjectName("Toast")
    outer.addWidget(body)
    layout = QHBoxLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(T.SPACE_2)
    layout.addWidget(QLabel(text))
    if action is not None:
        label, callback = action
        button = QPushButton(label)
        button.setObjectName("ToastAction")
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(callback)
        button.clicked.connect(toast.close)
        layout.addWidget(button)
    toast.adjustSize()
    # 放在鼠标所在屏幕底部居中：不挡住刚操作的位置，也不会落到别的屏幕上。
    area = QGuiApplication.screenAt(QCursor.pos()).availableGeometry()
    toast.move(area.center().x() - toast.width() // 2, area.bottom() - toast.height() - 48)
    toast.show()
    QTimer.singleShot(T.TOAST_MS if action is None else T.TOAST_ACTION_MS, toast.close)
    return toast
