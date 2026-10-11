"""图片导出的文件边界。"""

from pathlib import Path
from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication, QFileDialog
from settings import get_settings
from ui import show_message


def save_image(pixmap, parent=None):
    settings = get_settings()
    directory = settings.value("screenshot_dir", "") or QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
    fmt = settings.value("image_format", "png")
    filters = "JPG (*.jpg);;PNG (*.png)" if fmt == "jpg" else "PNG (*.png);;JPG (*.jpg)"
    path, chosen = QFileDialog.getSaveFileName(parent, "保存截图", str(Path(directory) / f"GridStruct-V2.{fmt}"), filters)
    if not path:
        return None
    if not Path(path).suffix:
        path += ".jpg" if chosen.startswith("JPG") else ".png"
    if not pixmap.save(path):
        show_message(parent, "保存失败", f"无法写入图片：{path}")
        return None
    QApplication.clipboard().setText(path)
    return path
