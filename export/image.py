"""图片导出的文件边界。"""

from pathlib import Path
from PySide6.QtCore import QDateTime, QStandardPaths
from PySide6.QtWidgets import QApplication, QFileDialog
from settings import get_settings
from ui import show_message, show_toast


def save_image(pixmap, parent=None):
    settings = get_settings()
    directory = settings.value("screenshot_dir", "") or QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
    fmt = settings.value("image_format", "png")
    filters = "JPG (*.jpg);;PNG (*.png)" if fmt == "jpg" else "PNG (*.png);;JPG (*.jpg)"
    # 带时间戳的默认文件名，连续保存不会互相覆盖。
    name = f"GridStruct-V2_{QDateTime.currentDateTime().toString('yyyy-MM-dd_HH-mm-ss')}.{fmt}"
    path, chosen = QFileDialog.getSaveFileName(parent, "保存截图", str(Path(directory) / name), filters)
    if not path:
        return None
    if not Path(path).suffix:
        path += ".jpg" if chosen.startswith("JPG") else ".png"
    if not pixmap.save(path):
        show_message(parent, "保存失败", f"无法写入图片：{path}")
        return None
    QApplication.clipboard().setText(path)
    show_toast("已保存，文件路径已复制")
    return path
