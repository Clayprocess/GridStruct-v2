"""按物理像素确认长截图重叠；指纹仅筛候选，不代替最终像素核对。"""

import numpy as np
from PySide6.QtGui import QImage, QPixmap


def _pixels(pixmap):
    image = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
    pixels = np.frombuffer(image.constBits(), dtype=np.uint32).reshape(
        image.height(), image.bytesPerLine() // 4)[:, :image.width()]
    return image, pixels


def _row_keys(pixels):
    # 全行总和加带位置的抽样，快速排除白底相似文字；碰撞仍由原像素确认。
    sparse = pixels[:, ::max(1, pixels.shape[1] // 160)]
    weights = np.arange(1, sparse.shape[1] + 1, dtype=np.uint64)
    return np.column_stack((pixels.sum(axis=1, dtype=np.uint64),
                            (sparse * weights).sum(axis=1, dtype=np.uint64)))


def _anchor_positions(source_keys, anchor):
    windows = np.lib.stride_tricks.sliding_window_view(source_keys, len(anchor), axis=0)
    return np.flatnonzero(np.all(windows == anchor.T, axis=(1, 2)))


def _has_position_detail(pixels, keys):
    # 纯色、单根分隔线和纵向静态边框不足以证明滚动位置。
    return len(np.unique(keys, axis=0)) >= 4 and bool(np.any(pixels[:, 1:] != pixels[:, :-1]))


def find_overlap(stitched: QPixmap, new: QPixmap, strip_h: int = 24) -> int:
    """返回唯一重叠高度；动态像素或重复位置无法确认时返回 0。"""
    height = min(stitched.height(), new.height())
    band = min(strip_h, max(4, height // 4))
    if stitched.width() != new.width() or height < band:
        return 0
    tail = stitched.copy(0, stitched.height() - height, stitched.width(), height)
    source_image, source = _pixels(tail)
    target_image, target = _pixels(new)
    source_keys, target_keys = _row_keys(source), _row_keys(target)
    positions = _anchor_positions(target_keys[:height], source_keys[-band:])
    match = 0
    for position in positions:
        cut = int(position) + band
        if not np.array_equal(source_keys[-cut:], target_keys[:cut]):
            continue
        overlap = source[-cut:]
        if not _has_position_detail(overlap, source_keys[-cut:]) or not np.array_equal(overlap, target[:cut]):
            continue
        if match:
            return 0
        match = cut
    return match


def find_viewport_end(stitched: QPixmap, frame: QPixmap) -> int:
    """完整视口在历史中唯一逐像素定位，返回其末端物理坐标。"""
    rows = frame.height()
    if stitched.width() != frame.width() or stitched.height() < rows:
        return 0
    source_image, source = _pixels(stitched)
    target_image, target = _pixels(frame)
    source_keys, target_keys = _row_keys(source), _row_keys(target)
    if not _has_position_detail(target, target_keys):
        return 0
    band = min(24, rows)
    positions = _anchor_positions(source_keys[rows - band:], target_keys[-band:])
    match = 0
    for position in positions:
        start = int(position)
        if not np.array_equal(source_keys[start:start + rows], target_keys):
            continue
        if not np.array_equal(source[start:start + rows], target):
            continue
        if match:
            return 0
        match = start + rows
    return match
