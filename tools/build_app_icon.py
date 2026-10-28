"""由 assets/app/ 的 SVG 生成 EXE 用的多尺寸 ICO：python -X utf8 tools/build_app_icon.py。改了 SVG 后重跑并提交 ICO。"""
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication

from styles import tokens as T
from ui import APP_ICON_SIZES, app_icon_pixmap

ICO = Path(__file__).resolve().parents[1] / "assets" / "app" / "gridstruct.ico"


def png_bytes(size):
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    app_icon_pixmap(size, T.APP_ICON_COLOR).save(buffer, "PNG")
    return bytes(buffer.data())


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    images = [(size, png_bytes(size)) for size in APP_ICON_SIZES]
    # ICO 目录项直接内嵌 PNG（Vista 起支持），每个尺寸用各自的精简版图形，不靠缩放。
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payload = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    ICO.write_bytes(header + entries + payload)
    print(f"{ICO}: {', '.join(str(size) for size, _ in images)}")
    del app


if __name__ == "__main__":
    main()
