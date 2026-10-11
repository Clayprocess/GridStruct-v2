"""显式启动的可见验收入口，按钮复用正式回调。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget


def floating_window_type():
    # 必须在创建 HWND 前确定类型，避免窗口重建使截图排除和吸附失效。
    return Qt.Window if QApplication.instance().property("desktop_check") else Qt.Tool


def show_desktop_check(callbacks):
    window = QWidget()
    window.setWindowTitle("GridStruct-V2 正式包 · 可见验收")
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("同一 EXE · 正式业务流程"))
    for label, action in (("截图", "screenshot"), ("剪贴板贴图", "pin"),
                          ("长截图", "longshot"), ("设置", "settings"),
                          ("退出程序", "exit")):
        button = QPushButton(label)
        button.setProperty("class", "destructive" if action == "exit" else "outline")
        button.clicked.connect(callbacks[action])
        layout.addWidget(button)
    window.adjustSize()
    available = window.screen().availableGeometry()
    window.move(available.right() - window.width() - 24, available.top() + 80)
    window.show()
    return window
