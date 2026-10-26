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


# ---------- 容差匹配（精确重叠找不到时的后备） ----------
# 参照 PixPin 3.5.5.1 反编译的位移评分：灰度差 >10 记为不一致，只统计有细节的像素，
# 取不一致比例最小的位移；位移超过 2/3 帧视为不可信。与 PixPin 不同的是这里还要求
# 最优位移唯一，重复内容宁可不拼也不重复（PixPin 在 Word 回滚时出现过行号回跳）。
_GRAY_TOLERANCE = 10
_MAX_ERROR = 0.1
_AMBIGUOUS_MARGIN = 0.05
_MIN_DETAIL = 200


def _gray(pixmap):
    _image, pixels = _pixels(pixmap)
    r, g, b = (pixels >> 16) & 0xff, (pixels >> 8) & 0xff, pixels & 0xff
    return ((r * 77 + g * 150 + b * 29) >> 8).astype(np.int16)


def _static_rows(a, b, limit):
    """两帧逐行比较：返回顶部、底部原地不动的行数（吸顶栏、底部工具栏），各不超过 limit。"""
    still = np.all(np.abs(a - b) <= _GRAY_TOLERANCE, axis=1)
    top = int(np.argmin(still)) if not still.all() else len(still)
    bottom = int(np.argmin(still[::-1])) if not still.all() else len(still)
    return min(top, limit), min(bottom, limit)


def _error(a, b):
    detail = (np.abs(np.diff(a, axis=1, prepend=a[:, :1])) > _GRAY_TOLERANCE) | \
             (np.abs(np.diff(b, axis=1, prepend=b[:, :1])) > _GRAY_TOLERANCE)
    count = int(detail.sum())
    if count < _MIN_DETAIL:
        return 1.0
    return float((np.abs(a - b)[detail] > _GRAY_TOLERANCE).sum()) / count


def find_shift(previous: QPixmap, new: QPixmap):
    """向前滚动的容差定位，返回 (cut, footer)：new[cut:] 是新内容（含底栏），
    已拼接结果需先去掉末尾 footer 行旧底栏；无法唯一确认时返回 (0, 0)。"""
    if previous.size() != new.size():
        return 0, 0
    a, b = _gray(previous), _gray(new)
    height = a.shape[0]
    top, bottom = _static_rows(a, b, height // 3)
    rows = height - top - bottom
    if rows < 16:
        return 0, 0
    # 先用抽样列的逐行均值粗筛位移，再在抽样列上算误差，最后全分辨率确认。
    step = max(1, a.shape[1] // 256)
    sa, sb = a[top:height - bottom, ::step], b[top:height - bottom, ::step]
    profile_a, profile_b = sa.mean(axis=1), sb.mean(axis=1)
    limit = rows * 2 // 3
    distances = np.array([np.mean((profile_a[s:] - profile_b[:rows - s]) ** 2) for s in range(1, limit + 1)])
    candidates = np.argsort(distances)[:16] + 1
    scored = sorted((_error(sa[s:], sb[:rows - s]), int(s)) for s in candidates)
    best_error, shift = scored[0]
    rivals = [error for error, s in scored[1:] if abs(s - shift) > 2]
    if best_error > _MAX_ERROR or (rivals and rivals[0] < best_error + _AMBIGUOUS_MARGIN):
        return 0, 0
    full_a, full_b = a[top:height - bottom], b[top:height - bottom]
    if _error(full_a[shift:], full_b[:rows - shift]) > _MAX_ERROR:
        return 0, 0
    return height - bottom - shift, bottom
