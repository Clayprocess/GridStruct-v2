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


APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "app"))
APP_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def app_icon_pixmap(size: int, color: str) -> QPixmap:
    # 完整 5×5 点阵在 32px 以下糊成一片，小尺寸换用点更少、按像素对齐的版本。
    name = ("gridstruct-16" if size <= 20 else "gridstruct-24" if size <= 28
            else "gridstruct-32" if size <= 32 else "gridstruct")
    with open(os.path.join(APP_DIR, f"{name}.svg"), encoding="utf-8") as f:
        svg = f.read().replace("currentColor", color)
    pm = QPixmap(QSize(size, size))
    pm.fill(Qt.transparent)
    with QPainter(pm) as p:
        QSvgRenderer(bytearray(svg.encode("utf-8"))).render(p)
    return pm


def app_icon(color: str) -> QIcon:
    """应用标识（只有点阵、无底色）：按实际像素尺寸取对应版本，颜色由调用方从 tokens 取。"""
    key = ("app", color)
    if key not in _cache:
        result = QIcon()
        for size in APP_ICON_SIZES:
            result.addPixmap(app_icon_pixmap(size, color))
        _cache[key] = result
    return _cache[key]
