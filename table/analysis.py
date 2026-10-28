"""一次表格分析：OCR 文字框 + SLANet_plus 结构 + 规则网格，在后台线程跑。"""

import numpy as np
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


def analyze(image, results=None, on_stage=None):
    """返回 (文字框, 网格建议, 结构模型提示)。results 为已有 OCR 结果时不再重复识别。"""
    if results is None:
        from ocr import OCREngine
        results = OCREngine().recognize_image(image, on_stage=on_stage)
    tokens = tokens_from_results(results, image)
    if on_stage is not None:
        on_stage("structure")
    pixels = image_to_bgr(image)
    note = ""
    try:
        structure = SlanetPlus().suggest_grid(pixels)
    except TableStructureError as exc:
        # 结构模型对无线表、碎表经常给不出自洽结构；规则网格仍可用，不算失败。
        structure, note = None, str(exc)
    return tokens, suggest(pixels, tokens, structure), note
