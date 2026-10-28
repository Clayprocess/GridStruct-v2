"""表格识别的页面来源：截图/贴图是一张现成图，识别文档是图片文件或 PDF 的每一页。

文件页不常驻图像：分析和翻页时再从文件渲染，几十页的扫描件也不会占满内存。
"""

from dataclasses import dataclass
from pathlib import Path
import re

from PySide6.QtGui import QColor, QImage, QImageReader, QPainter
from PySide6.QtPdf import QPdfDocument

from styles import tokens as T

# 扫描件 OCR 的常用分辨率；再高检测会先缩小，只是更慢。
PDF_DPI = 200
IMAGE_PATTERNS = "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp"
OPEN_FILTER = f"文档 (*.pdf {IMAGE_PATTERNS});;PDF (*.pdf);;图片 ({IMAGE_PATTERNS})"
_PDF_ERRORS = {
    QPdfDocument.Error.IncorrectPassword: "PDF 有打开密码，暂不支持",
    QPdfDocument.Error.UnsupportedSecurityScheme: "PDF 加密方式不支持",
    QPdfDocument.Error.InvalidFileFormat: "不是有效的 PDF",
    QPdfDocument.Error.FileNotFound: "文件不存在",
}


@dataclass
class ImagePage:
    """截图或贴图：results 为同图已有的 OCR 结果，可省一次识别。"""
    image: QImage
    results: object = None
    label: str = ""

    def render(self):
        return self.image


@dataclass
class FilePage:
    source: object  # QPdfDocument 或图片路径
    index: int
    label: str
    results = None

    def render(self):
        if isinstance(self.source, QPdfDocument):
            size = self.source.pagePointSize(self.index) * PDF_DPI / 72
            rendered = self.source.render(self.index, size.toSize())
            # PDFium 只画页面内容，空白处是透明的；按白纸合成，识别和显示都不受影响。
            image = QImage(rendered.size(), QImage.Format_RGB32)
            image.fill(QColor(T.PAPER_BACKGROUND))
            painter = QPainter(image)
            painter.drawImage(0, 0, rendered)
            painter.end()
            return image
        reader = QImageReader(self.source)
        # 手机拍的扫描件常带 EXIF 方向，不转正会整页横着识别。
        reader.setAutoTransform(True)
        if self.index:
            reader.jumpToImage(self.index)
        image = reader.read()
        if image.isNull():
            raise ValueError(reader.errorString())
        image.setDevicePixelRatio(1)
        return image


def _natural_key(path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", Path(path).name)]


def collect_pages(paths, owner):
    """返回 (页面, 跳过的文件说明)。PDF 句柄挂在 owner 上，随窗口释放。"""
    pages, problems = [], []
    # 按文件名自然排序：扫描仪导出的 2.jpg 排在 10.jpg 前面。
    paths = sorted(paths, key=_natural_key)
    for path in paths:
        name = Path(path).name
        if path.lower().endswith(".pdf"):
            pdf = QPdfDocument(owner)
            error = pdf.load(path)
            if error != QPdfDocument.Error.None_:
                problems.append(f"{name}：{_PDF_ERRORS.get(error, '无法打开')}")
                continue
            pages += [FilePage(pdf, index, f"{name} 第 {index + 1} 页") for index in range(pdf.pageCount())]
            continue
        reader = QImageReader(path)
        if not reader.canRead():
            problems.append(f"{name}：{reader.errorString()}")
            continue
        # 多页 TIFF 是扫描仪常见输出，每一帧当一页。
        count = max(1, reader.imageCount())
        pages += [FilePage(path, index, name if count == 1 else f"{name} 第 {index + 1} 页") for index in range(count)]
    return pages, problems
