# 屏幕捕获：多显示器虚拟桌面截图（物理像素）。
# PerMonitorV2 思路：按每块显示器抓取，在逻辑坐标系下拼成一张大图。

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtCore import QRect, Qt


def virtual_geometry() -> QRect:
    """全部显示器的联合逻辑区域。"""
    vg = QRect()
    for s in QApplication.screens():
        vg = vg.united(s.geometry())
    return vg


def capture_virtual_desktop() -> QPixmap:
    """抓取完整虚拟桌面。返回的 QPixmap 自带 devicePixelRatio。"""
    screens = QApplication.screens()
    vg = virtual_geometry()
    max_dpr = max((s.devicePixelRatio() for s in screens), default=1.0)

    size = vg.size()
    pm = QPixmap(int(size.width() * max_dpr), int(size.height() * max_dpr))
    pm.setDevicePixelRatio(max_dpr)
    pm.fill(Qt.black)

    with QPainter(pm) as p:
        for s in screens:
            shot = s.grabWindow(0)  # 整屏，自带该屏的 DPR
            pos = s.geometry().topLeft() - vg.topLeft()
            p.drawPixmap(pos, shot)
    return pm
