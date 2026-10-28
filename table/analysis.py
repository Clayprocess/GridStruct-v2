"""一次表格分析：OCR 文字框 + SLANet_plus 结构 + 规则网格，在后台线程跑。"""

from statistics import median

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
    """文档页里表格通常只占一部分：先按文字范围外扩 1.5 倍字高（留住表格外边线），
    再收到这块里有墨迹的范围。外框贴着表格边线，不会多出空白行列；结构模型也只看表格，
    它本来就是按表格截图训练的。"""
    height, width = pixels.shape[:2]
    if not tokens:
        return QRect(0, 0, width, height)
    text = (min(token.box.left for token in tokens), min(token.box.top for token in tokens),
            max(token.box.right for token in tokens), max(token.box.bottom for token in tokens))
    pad = round(median(token.box.height for token in tokens) * 1.5)
    left, top = max(0, text[0] - pad), max(0, text[1] - pad)
    right, bottom = min(width, text[2] + pad), min(height, text[3] + pad)
    # 扫描件底色常偏灰，按区域内最亮处往下一截算墨迹。
    gray = pixels[top:bottom, left:right].min(axis=2)
    ink = gray < int(gray.max()) - 80
    rows, columns = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
    if rows.size and columns.size:
        # 检测框比字形略宽，收到墨迹时不能把文字框切掉。
        margin = 2
        left, right = left + columns[0] - margin, left + columns[-1] + 1 + margin
        top, bottom = top + rows[0] - margin, top + rows[-1] + 1 + margin
        left, top = max(0, min(left, text[0])), max(0, min(top, text[1]))
        right, bottom = min(width, max(right, text[2])), min(height, max(bottom, text[3]))
    return QRect(int(left), int(top), int(right - left), int(bottom - top))


def _shift(token, dx, dy):
    box = token.box
    return Token(token.text, token.score, Rect(box.left - dx, box.top - dy, box.right - dx, box.bottom - dy))


def analyze(image, results=None, on_stage=None, crop=False):
    """返回 (文字框, 网格建议, 结构模型提示, 裁出的分析区域或 None)。results 为已有 OCR 结果时不再重复识别。

    crop 用于识别文档：只分析文字所在区域，文字框和网格都换算到这块区域的坐标。
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
