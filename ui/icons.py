# Lucide 图标加载器（MIT）。
# Lucide SVG 使用 stroke="currentColor"，tinted_icon() 会把 currentColor
# 替换为目标色再渲染，保证图标颜色跟随主题。

import os
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import QSize, Qt
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QPainter

ICON_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "icons")
ICON_DIR = os.path.abspath(ICON_DIR)

_cache: dict = {}


def _load_svg(name: str) -> str:
    path = os.path.join(ICON_DIR, f"{name}.svg")
    if not os.path.exists(path):
        raise FileNotFoundError(f"icon not found: {name} (assets/icons/{name}.svg)")
    with open(path, encoding="utf-8") as f:
        return f.read()


def icon(name: str) -> QIcon:
    """原样加载 SVG（Lucide 默认 currentColor，多为深色线条）。"""
    key = ("raw", name)
    if key not in _cache:
        path = os.path.join(ICON_DIR, f"{name}.svg")
        if not os.path.exists(path):
            raise FileNotFoundError(f"icon not found: {name}")
        _cache[key] = QIcon(path)
    return _cache[key]


def tinted_icon(name: str, color: str, size: int = 16) -> QIcon:
    """按 styles/tokens.py 提供的颜色渲染图标。"""
    key = ("tinted", name, color, size)
    if key not in _cache:
        svg = _load_svg(name).replace("currentColor", color)
        renderer = QSvgRenderer(bytearray(svg.encode("utf-8")))
        pm = QPixmap(QSize(size, size))
        pm.fill(Qt.transparent)
        with QPainter(pm) as p:
            renderer.render(p)
        _cache[key] = QIcon(pm)
    return _cache[key]
