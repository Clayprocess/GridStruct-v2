# pin 模块公开接口

from PySide6.QtWidgets import QApplication, QMessageBox

from .window import PinWindow

__all__ = ["PinWindow", "show_pin_from_clipboard"]

# 存活引用：无边框窗口无父对象，Python 层必须持有否则被回收
_open_pins: list[PinWindow] = []


def show_pin_from_clipboard():
    """托盘"贴图"回调：剪贴板有图则贴图，无图提示。"""
    cb = QApplication.clipboard()
    md = cb.mimeData()  # 空剪贴板时可能为 None
    pm = cb.pixmap() if (md is not None and md.hasImage()) else None
    if pm is None or pm.isNull():
        QMessageBox.information(None, "贴图", "剪贴板没有图片")
        return
    w = PinWindow(pm)
    _open_pins.append(w)
    w.destroyed.connect(lambda: _open_pins.remove(w))
    w.show()
