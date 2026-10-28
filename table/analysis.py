"""一次表格分析：OCR 文字框 + SLANet_plus 结构 + 规则网格，在后台线程跑。"""

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .grid import suggest
from .model import Rect, Token
from .slanet import SlanetPlus, TableStructureError


def image_to_bgr(image):
    image = image.convertToFormat(QImage.Format_BGR888)
    width, height = image.width(), image.height()
    # Qt 每行按 4 字节对齐，先按 bytesPerLine 取行再裁掉补齐字节。
    rows = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(height, image.bytesPerLine())
    return rows[:, :width * 3].reshape(height, width, 3).copy()


def tokens_from_results(results, image):
    """OCR 结果转成原图物理像素的文字框；四点框缺失的旧结果按 DPR 还原逻辑框。"""
    dpr = image.devicePixelRatio()
    tokens = []
    for result in results:
        if not result.text.strip():
            continue
        if result.polygon:
            xs = [point[0] for point in result.polygon]
            ys = [point[1] for point in result.polygon]
        else:
            box = result.box
            xs = [box.left() * dpr, (box.left() + box.width()) * dpr]
            ys = [box.top() * dpr, (box.top() + box.height()) * dpr]
        left = max(0, round(min(xs)))
        top = max(0, round(min(ys)))
        right = min(image.width(), max(left + 1, round(max(xs))))
        bottom = min(image.height(), max(top + 1, round(max(ys))))
        tokens.append(Token(result.text, float(result.score), Rect(left, top, right, bottom)))
    return tuple(tokens)


def content_rect(tokens, pixels):
    """只裁掉与整页最亮背景完全一致的均匀页边，保留浅色表格线和 OCR 文字框。"""
    height, width = pixels.shape[:2]
    # 浅灰线也可能是表格边界；颜色或噪声有差异就保留，宁可多留页边。
    background = pixels.max(axis=(0, 1))
    ink = np.any(pixels != background, axis=2)
    rows, columns = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
    if not rows.size:
        return QRect(0, 0, width, height)
    left, right = columns[0], columns[-1] + 1
    top, bottom = rows[0], rows[-1] + 1
    if tokens:
        # 检测框比字形略宽，收到墨迹时不能把文字框切掉。
        left = min(left, min(token.box.left for token in tokens))
        top = min(top, min(token.box.top for token in tokens))
        right = max(right, max(token.box.right for token in tokens))
        bottom = max(bottom, max(token.box.bottom for token in tokens))
    margin = 2
    left, top = max(0, left - margin), max(0, top - margin)
    right, bottom = min(width, right + margin), min(height, bottom + margin)
    return QRect(int(left), int(top), int(right - left), int(bottom - top))


def _shift(token, dx, dy):
    box = token.box
    return Token(token.text, token.score, Rect(box.left - dx, box.top - dy, box.right - dx, box.bottom - dy))


def analyze(image, results=None, on_stage=None, crop=False):
    """返回 (文字框, 网格建议, 结构模型提示, 裁出的分析区域或 None)。results 为已有 OCR 结果时不再重复识别。

    crop 用于识别文档：裁掉内容外的空白页边，文字框和网格都换算到这块区域的坐标。
    截图、贴图本来就是用户框好的表格，不裁。
    """
    if results is None:
        from ocr import OCREngine
        results = OCREngine().recognize_image(image, on_stage=on_stage)
    tokens = tokens_from_results(results, image)
    pixels = image_to_bgr(image)
    rect = content_rect(tokens, pixels) if crop else None
    if rect is not None and rect != image.rect():
        pixels = pixels[rect.top():rect.bottom() + 1, rect.left():rect.right() + 1].copy()
        tokens = tuple(_shift(token, rect.left(), rect.top()) for token in tokens)
    else:
        rect = None
    if on_stage is not None:
        on_stage("structure")
    note = ""
    try:
        structure = SlanetPlus().suggest_grid(pixels)
    except TableStructureError as exc:
        # 结构模型对无线表、碎表经常给不出自洽结构；规则网格仍可用，不算失败。
        structure, note = None, str(exc)
    return tokens, suggest(pixels, tokens, structure), note, rect
