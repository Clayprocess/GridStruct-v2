# 离屏渲染自查：把演示窗口抓成 PNG，Codex/人工看图验收。
# 用法：QT_QPA_PLATFORM=offscreen python tools/render_check.py
# 输出：/tmp/theme_light.png /tmp/theme_dark.png

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from styles import apply_theme
from main import build_demo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def render(mode: str, path: str):
    app = QApplication.instance() or QApplication([])
    apply_theme(app, mode)
    w = build_demo(mode)
    w.show()
    app.processEvents()
    pm = w.grab()
    pm.save(path)
    print(f"{mode}: {path} ({pm.width()}x{pm.height()})")
    w.close()


if __name__ == "__main__":
    render("light", "/tmp/theme_light.png")
    render("dark", "/tmp/theme_dark.png")
