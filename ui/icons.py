# Lucide 图标加载器（MIT）。
# Lucide SVG 使用 stroke="currentColor"，tinted_icon() 会把 currentColor
# 替换为目标色再渲染，保证图标颜色跟随主题。

import os
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QPainter

from styles import tokens as T

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


# Windows 常见的显示缩放档位。每档按实际像素单独画一张：只给 1 倍图，
# 在 125%/150% 的屏幕上会被位图放大，线条发虚。
_SCALES = (1, 1.25, 1.5, 1.75, 2, 2.5, 3)


def painted_icon(size: QSize, draw, icon: QIcon | None = None,
                 mode=QIcon.Normal, state=QIcon.Off) -> QIcon:
    """逐个缩放档位绘制同一图形。draw(painter, rect) 按逻辑像素作画，传入 icon 时追加到它上面。"""
    if icon is None:
        icon = QIcon()
    for scale in _SCALES:
        pm = QPixmap(round(size.width() * scale), round(size.height() * scale))
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.transparent)
        with QPainter(pm) as p:
            p.setRenderHint(QPainter.Antialiasing)
            draw(p, QRectF(0, 0, size.width(), size.height()))
        icon.addPixmap(pm, mode, state)
    return icon


def paint_svg(painter: QPainter, name: str, color: str, rect: QRectF) -> None:
    """把图标按矢量直接画进 rect，不经过中间位图。"""
    key = ("svg", name, color)
    if key not in _cache:
        svg = _load_svg(name).replace("currentColor", color)
        _cache[key] = QSvgRenderer(bytearray(svg.encode("utf-8")))
    _cache[key].render(painter, rect)


def tinted_icon(name: str, color: str, size: int = 16, icon: QIcon | None = None,
                mode=QIcon.Normal, state=QIcon.Off) -> QIcon:
    """按 styles/tokens.py 提供的颜色渲染图标；传入 icon 时把这一态追加上去（不缓存）。"""
    if icon is not None:
        return painted_icon(QSize(size, size), lambda p, r: paint_svg(p, name, color, r), icon, mode, state)
    key = ("tinted", name, color, size)
    if key not in _cache:
        _cache[key] = painted_icon(QSize(size, size), lambda p, r: paint_svg(p, name, color, r))
    return _cache[key]


APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "app"))
APP_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _app_variant(size: int) -> str:
    # 8 点阵（3×3 去掉右下角）在 32px 及以下每档单独按像素对齐，再大用 256 的矢量版缩放。
    for limit in (16, 20, 24, 32):
        if size <= limit:
            return f"gridstruct-{limit}"
    return "gridstruct"


def app_icon_pixmap(size: int, mode: str = "light") -> QPixmap:
    """点阵只用一种颜色：浅色主题黑、深色主题白（取主题前景色），无底色无描边。"""
    with open(os.path.join(APP_DIR, f"{_app_variant(size)}.svg"), encoding="utf-8") as f:
        svg = f.read().replace("currentColor", T.THEMES[mode]["foreground"])
    pm = QPixmap(QSize(size, size))
    pm.fill(Qt.transparent)
    with QPainter(pm) as p:
        p.setRenderHint(QPainter.Antialiasing)
        QSvgRenderer(bytearray(svg.encode("utf-8"))).render(p, QRectF(0, 0, size, size))
    return pm


def app_icon(mode: str = "light") -> QIcon:
    """应用标识：浅色主题黑点，深色主题白点；EXE 文件图标固定用黑点（见 tools/build_app_icon.py）。"""
    key = ("app", mode)
    if key not in _cache:
        result = QIcon()
        for size in APP_ICON_SIZES:
            result.addPixmap(app_icon_pixmap(size, mode))
        _cache[key] = result
    return _cache[key]
