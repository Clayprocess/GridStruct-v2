# 主题演示窗口：验证 shadcn 翻译效果。
# 用法：python main.py [--dark]

import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QToolButton, QLineEdit, QComboBox, QCheckBox,
    QSlider, QTabWidget, QListWidget, QGroupBox, QLabel, QMenu,
)
from PySide6.QtCore import Qt
from styles.theme import apply_theme
from styles import tokens as T
from ui.icons import tinted_icon


def themed_icon(name, mode):
    t = T.THEMES[mode]
    return tinted_icon(name, t["foreground"], 16)


def build_demo(mode: str) -> QMainWindow:
    w = QMainWindow()
    w.setWindowTitle(f"PixShot 主题演示（{mode}）")
    w.resize(560, 640)
    central = QWidget()
    w.setCentralWidget(central)
    layout = QVBoxLayout(central)
    layout.setSpacing(12)
    layout.setContentsMargins(20, 20, 20, 20)

    # 按钮区
    layout.addWidget(QLabel("按钮"))
    row = QHBoxLayout()
    for cls, text in [("primary", "Primary"), ("secondary", "Secondary"),
                      ("outline", "Outline"), ("ghost", "Ghost"),
                      ("destructive", "删除")]:
        b = QPushButton(text)
        b.setProperty("class", cls)
        row.addWidget(b)
    layout.addLayout(row)

    # 工具条按钮
    layout.addWidget(QLabel("工具条"))
    trow = QHBoxLayout()
    for name, tip in [("square", "矩形"), ("circle", "椭圆"),
                      ("arrow-up-right", "箭头"), ("pencil", "画笔"),
                      ("type", "文字"), ("eraser", "擦除")]:
        tb = QToolButton()
        tb.setIcon(themed_icon(name, mode))
        tb.setToolTip(tip)
        tb.setCheckable(True)
        trow.addWidget(tb)
    trow.addStretch()
    layout.addLayout(trow)

    # 输入区
    layout.addWidget(QLabel("输入"))
    irow = QHBoxLayout()
    irow.addWidget(QLineEdit("截图保存路径"))
    cb = QComboBox()
    cb.addItems(["PNG", "JPG", "BMP", "PDF"])
    irow.addWidget(cb)
    layout.addLayout(irow)

    # 选项
    orow = QHBoxLayout()
    orow.addWidget(QCheckBox("开机启动"))
    c2 = QCheckBox("深色跟随系统")
    c2.setChecked(True)
    orow.addWidget(c2)
    s = QSlider(Qt.Horizontal)
    s.setValue(70)
    orow.addWidget(s)
    layout.addLayout(orow)

    # Tab + 列表
    tabs = QTabWidget()
    tabs.addTab(QLabel("  通用设置页内容"), "通用")
    tabs.addTab(QLabel("  截图设置页内容"), "截图")
    tabs.addTab(QLabel("  关于页内容"), "关于")
    layout.addWidget(tabs)

    gb = QGroupBox("分组示例")
    gl = QVBoxLayout(gb)
    lw = QListWidget()
    lw.addItems(["截图 2026-10-10.png", "贴图分组 A", "OCR 历史"])
    gl.addWidget(lw)
    layout.addWidget(gb)

    layout.addStretch()
    return w


def main():
    app = QApplication(sys.argv)
    mode = "dark" if "--dark" in sys.argv else "light"
    apply_theme(app, mode)
    w = build_demo(mode)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
