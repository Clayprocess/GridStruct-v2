"""一次表格分析：OCR 文字框 + SLANet_plus 结构 + 规则网格，在后台线程跑。"""

from dataclasses import dataclass

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


# 整页找表格：表格线连通块够大、框内除外框还有分隔线才算一张表。
MIN_TABLE_WIDTH_RATIO = 0.10
MIN_TABLE_HEIGHT_RATIO = 0.02
MIN_TABLE_SIDE = 40
TABLE_LINE_COVERAGE = 0.5
MIN_TABLE_LINES = 5  # 横线 + 竖线条数；只有外框（4 条）的是文本框或页框，不当表格


def _line_count(mask, axis, extent):
    """掩膜里贯穿大半个区域的线条数，相邻的像素行/列算一条。"""
    full = np.count_nonzero(mask, axis=axis) >= extent * TABLE_LINE_COVERAGE
    return int(np.count_nonzero(full[1:] & ~full[:-1]) + full[0]) if full.size else 0


def table_regions(pixels):
    """整页里由表格线围成的表格区域，按阅读顺序；一张都没有返回空，由调用方退回整块内容。

    只认有线表：无线表、三线表的位置靠文字排布猜不准，交给用户框选。
    """
    import cv2
    from .grid import rule_masks
    height, width = pixels.shape[:2]
    horizontal, vertical = rule_masks(pixels)
    # 扫描件的表格线常有断点，膨胀几像素再取连通块，同一张表连成一块。
    gap = max(3, round(min(width, height) * 0.004))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * gap + 1, 2 * gap + 1))
    lines = cv2.bitwise_or(horizontal, vertical)
    joined = cv2.dilate(lines, kernel)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(joined, connectivity=8)
    regions = []
    for left, top, w, h, _area in stats[1:count]:
        if w < max(MIN_TABLE_SIDE, width * MIN_TABLE_WIDTH_RATIO) or h < max(MIN_TABLE_SIDE, height * MIN_TABLE_HEIGHT_RATIO):
            continue
        rows = _line_count(horizontal[top:top + h, left:left + w], 1, w)
        columns = _line_count(vertical[top:top + h, left:left + w], 0, h)
        if rows < 2 or columns < 2 or rows + columns < MIN_TABLE_LINES:
            continue
        # 按膨胀前的线条定边，再留 2 像素让外框线完整落在裁出的图里（贴着图边的表也不切线）。
        block = lines[top:top + h, left:left + w]
        ys, xs = np.flatnonzero(block.any(axis=1)), np.flatnonzero(block.any(axis=0))
        x0, y0 = max(0, left + xs[0] - 2), max(0, top + ys[0] - 2)
        x1, y1 = min(width, left + xs[-1] + 3), min(height, top + ys[-1] + 3)
        regions.append(QRect(int(x0), int(y0), int(x1 - x0), int(y1 - y0)))
    return reading_order(regions)


def reading_order(regions):
    """表格编号顺序：从上到下，同一行并排的先左后右（顶边相差不到最上面那张表高度的一半算同一行）。"""
    remaining = sorted(regions, key=lambda rect: (rect.top(), rect.left()))
    ordered = []
    while remaining:
        first = remaining[0]
        row = [rect for rect in remaining if rect.top() < first.top() + first.height() / 2]
        ordered += sorted(row, key=QRect.left)
        remaining = [rect for rect in remaining if not any(rect is other for other in row)]
    return ordered


def _shift(token, dx, dy):
    box = token.box
    return Token(token.text, token.score, Rect(box.left - dx, box.top - dy, box.right - dx, box.bottom - dy))


@dataclass(frozen=True)
class TableResult:
    """一张表的分析结果。rect 为它在整页里的位置（None 表示整张图），tokens 已换算到区域坐标。"""
    rect: object
    tokens: tuple
    suggestion: object
    note: str = ""


def analyze_region(pixels, tokens, rect=None):
    """在整页像素里裁出 rect 分析；只取中心落在框内的文字，框外的正文不进表格。"""
    if rect is not None:
        pixels = pixels[rect.top():rect.bottom() + 1, rect.left():rect.right() + 1].copy()
        tokens = tuple(_shift(token, rect.left(), rect.top()) for token in tokens
                       if rect.contains(round(token.box.center_x), round(token.box.center_y)))
    note = ""
    try:
        structure = SlanetPlus().suggest_grid(pixels)
    except TableStructureError as exc:
        # 结构模型对无线表、碎表经常给不出自洽结构；规则网格仍可用，不算失败。
        structure, note = None, str(exc)
    return TableResult(rect, tokens, suggest(pixels, tokens, structure), note)


def analyze(image, results=None, on_stage=None, tokens=None, regions=None):
    """返回 (整页文字框, 每张表的 TableResult)。

    截图、贴图和识别文档都先在整张图里找有线表格，一张都没有就裁掉空白边当一张表，不把图片边缘当表格边界。
    regions 为用户框选的区域时按框分析；tokens 为这张图已有的文字框时不再识别，
    改框后重新分析只跑结构，不重跑 OCR。
    """
    if tokens is None:
        if results is None:
            from ocr import OCREngine
            results = OCREngine().recognize_image(image, on_stage=on_stage)
        tokens = tokens_from_results(results, image)
    pixels = image_to_bgr(image)
    if regions is None:
        regions = table_regions(pixels) or [content_rect(tokens, pixels)]
    if on_stage is not None:
        on_stage("structure")
    return tokens, tuple(analyze_region(pixels, tokens, rect) for rect in regions)
