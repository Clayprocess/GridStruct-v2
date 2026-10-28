"""自动网格：原图表格线 + OCR 行列空白 + SLANet_plus 结构，打分择优。

整体移植自 v1 infrastructure/hybrid_grid_suggester.py（Clay 在 v1 实测过的方案），
常量与打分权重保持原值，只改成 numpy BGR 图像输入、去掉 v1 的协议层。
"""

import math
from bisect import bisect_right
from dataclasses import dataclass
from itertools import pairwise
from statistics import median

import numpy as np

from .model import GridSpec, Rect

# 表格线检测：核长按原图比例，始终在原分辨率上做，不缩放。
MIN_RULE_KERNEL = 12
RULE_KERNEL_RATIO = 0.04
RULE_EVIDENCE_THRESHOLD = 0.10
STRONG_RULE_SUPPORT = 0.34
STRONG_RULE_SPAN = 0.42
MIN_HORIZONTAL_RULES = 2
MIN_VERTICAL_RULES = 2
MIN_RULE_INTERSECTIONS = 4
MIN_INTERSECTION_DENSITY = 0.35
ADAPTIVE_THRESHOLD_C = 7

# OCR 行列版面。
ROW_OVERLAP_THRESHOLD = 0.45
ROW_CENTER_DISTANCE_RATIO = 0.68
MIN_TEXT_ROWS = 2
MIN_COLUMN_GAP_HEIGHT_RATIO = 0.90
MIN_COLUMN_GAP_WIDTH_RATIO = 0.018
COLUMN_ALIGNMENT_TOLERANCE_RATIO = 0.035
COLUMN_REPEAT_RATIO = 0.45
MIN_COLUMN_REPEAT = 2
BULLET_WIDTH_HEIGHT_RATIO = 0.90
BULLET_LEFT_MARGIN_RATIO = 0.08
BULLET_MARKS = frozenset("•·●○▪▫■□◆◇▶▷►▸-–—*+☐☑✓✔")

# 几何与打分。
TOKEN_INSET_RATIO = 0.10
MIN_GRID_CELL_SIZE = 3
MIN_SNAP_RADIUS = 8
SNAP_RADIUS_RATIO = 0.025
MAX_SNAP_RADIUS = 56
RULE_MATCH_TOLERANCE_RATIO = 0.008
RULE_MATCH_MIN_TOLERANCE = 3

SCORE_BASE = 0.08
NON_CROSSING_WEIGHT = 0.28
RULE_SUPPORT_WEIGHT = 0.24
WHITESPACE_SUPPORT_WEIGHT = 0.12
REPEATED_ALIGNMENT_WEIGHT = 0.08
TOPOLOGY_VALIDITY_WEIGHT = 0.18
STRATEGY_EVIDENCE_WEIGHT = 0.06
CROSSING_PENALTY_WEIGHT = 0.42
UNEXPLAINED_RULE_PENALTY_WEIGHT = 0.30
TEXT_TOPOLOGY_VALIDITY = 0.65
MIN_AUTOMATIC_SCORE = 0.45
AMBIGUOUS_SCORE_DELTA = 0.10
RULE_SNAP_SUPPORT_WEIGHT = 0.75
RULE_SNAP_SPAN_WEIGHT = 0.25
RULE_SNAP_DISTANCE_PENALTY = 0.10
WHITESPACE_DISTANCE_PENALTY = 0.05
WEAK_STRUCTURE_ALIGNMENT_SCALE = 0.67
MULTICOLUMN_TEXT_EVIDENCE = 0.50
AMBIGUOUS_CONFIDENCE_SCALE = 0.65

STRUCTURE = "structure"
TEXT_ROWS = "text_rows"
MANUAL = "manual"
STRATEGY_LABELS = {STRUCTURE: "按表格结构", TEXT_ROWS: "按文字行", MANUAL: "仅外框"}
MESSAGES = {
    STRUCTURE: "已按表格结构生成网格，并吸附到原图线条",
    TEXT_ROWS: "未检测到稳定分栏，已按文字行生成网格",
    MANUAL: "自动分析可信度较低，已保留外框，请手动划线",
}


@dataclass(frozen=True, slots=True)
class RuleLine:
    position: int
    support: float
    span_start: int
    span_end: int

    @property
    def span(self):
        return self.span_end - self.span_start


@dataclass(frozen=True, slots=True)
class RuleEvidence:
    horizontal_lines: tuple
    vertical_lines: tuple
    strong: bool
    image_width: int
    image_height: int


@dataclass(frozen=True, slots=True)
class Candidate:
    grid: GridSpec
    strategy: str
    score: float


@dataclass(frozen=True, slots=True)
class Suggestion:
    selected: Candidate
    alternatives: tuple
    confidence: float
    message: str


@dataclass(slots=True)
class _TextRow:
    tokens: list
    top: int
    bottom: int

    @property
    def center_y(self):
        return (self.top + self.bottom) / 2


def detect_rules(image):
    """在原分辨率上找长横线/竖线；Otsu 叠加自适应阈值，兼顾浅色细线和扫描底色。"""
    import cv2
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    threshold_type = cv2.THRESH_BINARY_INV if float(np.median(gray)) >= 128 else cv2.THRESH_BINARY
    _, ink = cv2.threshold(gray, 0, 255, threshold_type | cv2.THRESH_OTSU)
    block = min(width, height, 51)
    if block >= 15:
        block -= 0 if block % 2 else 1
        adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, threshold_type,
                                         block, ADAPTIVE_THRESHOLD_C)
        ink = cv2.bitwise_or(ink, adaptive)
    horizontal_mask = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(MIN_RULE_KERNEL, round(width * RULE_KERNEL_RATIO)), 1)))
    vertical_mask = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(MIN_RULE_KERNEL, round(height * RULE_KERNEL_RATIO)))))
    horizontal = _extract_lines(horizontal_mask, np.count_nonzero(horizontal_mask, axis=1) / width, True)
    vertical = _extract_lines(vertical_mask, np.count_nonzero(vertical_mask, axis=0) / height, False)

    strong_horizontal = _strong(horizontal, width)
    strong_vertical = _strong(vertical, height)
    strong_intersections = sum(
        1 for h in strong_horizontal for v in strong_vertical
        if h.span_start - 2 <= v.position <= h.span_end + 2 and v.span_start - 2 <= h.position <= v.span_end + 2)
    possible = len(strong_horizontal) * len(strong_vertical)
    strong = (len(strong_horizontal) >= MIN_HORIZONTAL_RULES and len(strong_vertical) >= MIN_VERTICAL_RULES
              and strong_intersections >= MIN_RULE_INTERSECTIONS
              and strong_intersections / possible >= MIN_INTERSECTION_DENSITY)
    return RuleEvidence(horizontal, vertical, strong, width, height)


def _strong(lines, extent):
    return tuple(line for line in lines
                 if line.support >= STRONG_RULE_SUPPORT and line.span / extent >= STRONG_RULE_SPAN)


def _extract_lines(mask, evidence, horizontal):
    groups = []
    for index in np.flatnonzero(evidence >= RULE_EVIDENCE_THRESHOLD):
        if groups and index == groups[-1][-1] + 1:
            groups[-1].append(int(index))
        else:
            groups.append([int(index)])
    lines = []
    for group in groups:
        weights = np.maximum(evidence[group], 1e-6)
        position = round(float(np.dot(group, weights) / weights.sum()))
        region = mask[group, :] if horizontal else mask[:, group]
        start, end = _longest_run(np.any(region > 0, axis=0 if horizontal else 1))
        lines.append(RuleLine(position, min(1.0, float(evidence[group].max())), start, end))
    return tuple(lines)


def _longest_run(values):
    padded = np.concatenate(([False], values, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    if not len(edges):
        return 0, 0
    starts, ends = edges[0::2], edges[1::2]
    best = int(np.argmax(ends - starts))
    return int(starts[best]), int(ends[best])


# ---- 按 OCR 行和列空白建网格 ----

def text_grid(tokens, width, height):
    """返回 (网格, 列对齐重复度)；文字行不足时返回 None。"""
    usable = [token for token in tokens if token.text.strip()]
    if len(usable) < MIN_TEXT_ROWS:
        return None
    rows = _cluster_rows(usable)
    if len(rows) < MIN_TEXT_ROWS:
        return None
    insets = [_inset(token.box) for token in usable]
    horizontal = [0]
    for previous, following in pairwise(rows):
        horizontal.append(_nearest_clear(round((previous.bottom + following.top) / 2),
                                         round(previous.center_y) + 1, round(following.center_y) - 1,
                                         insets, horizontal=True))
    horizontal.append(height)
    vertical, alignment = _column_lines(rows, width)
    return GridSpec(_regularize(horizontal, height), vertical), alignment


def _cluster_rows(tokens):
    median_height = float(median(token.box.height for token in tokens))
    rows = []
    for token in sorted(tokens, key=lambda item: (item.box.center_y, item.box.left)):
        best, best_distance = None, math.inf
        for row in rows[-3:]:
            overlap = max(0, min(row.bottom, token.box.bottom) - max(row.top, token.box.top))
            ratio = overlap / min(row.bottom - row.top, token.box.height)
            distance = abs(row.center_y - token.box.center_y)
            if ((ratio >= ROW_OVERLAP_THRESHOLD or distance <= median_height * ROW_CENTER_DISTANCE_RATIO)
                    and distance < best_distance):
                best, best_distance = row, distance
        if best is None:
            rows.append(_TextRow([token], token.box.top, token.box.bottom))
        else:
            best.tokens.append(token)
            best.top = min(best.top, token.box.top)
            best.bottom = max(best.bottom, token.box.bottom)
    for row in rows:
        row.tokens.sort(key=lambda item: item.box.left)
    rows.sort(key=lambda row: row.center_y)
    return rows


def _column_lines(rows, extent):
    """同一位置的列间空白在足够多行里重复出现才算一列；行首项目符号不算。"""
    median_height = float(median(token.box.height for row in rows for token in row.tokens))
    minimum_gap = max(median_height * MIN_COLUMN_GAP_HEIGHT_RATIO, extent * MIN_COLUMN_GAP_WIDTH_RATIO)
    observations = []
    for row_index, row in enumerate(rows):
        for pair_index, (left, right) in enumerate(pairwise(row.tokens)):
            if right.box.left - left.box.right < minimum_gap:
                continue
            if pair_index == 0 and _is_bullet(left, median_height, extent):
                continue
            observations.append((row_index, left.box.right, right.box.left))
    tolerance = max(6, round(extent * COLUMN_ALIGNMENT_TOLERANCE_RATIO))
    clusters = []
    for observation in sorted(observations, key=lambda value: value[2]):
        matching = [cluster for cluster in clusters
                    if abs(median(item[2] for item in cluster) - observation[2]) <= tolerance]
        if matching:
            min(matching, key=lambda cluster: abs(median(item[2] for item in cluster) - observation[2])).append(observation)
        else:
            clusters.append([observation])
    required = max(MIN_COLUMN_REPEAT, math.ceil(len(rows) * COLUMN_REPEAT_RATIO))
    accepted = [cluster for cluster in clusters if len({item[0] for item in cluster}) >= required]
    if not accepted:
        return (0, extent), 0.0

    insets = [_inset(token.box) for row in rows for token in row.tokens]
    positions, supports = [], []
    for cluster in accepted:
        corridor_start = max(item[1] for item in cluster)
        corridor_end = min(item[2] for item in cluster)
        target = (round((corridor_start + corridor_end) / 2) if corridor_start < corridor_end
                  else round(median(item[2] for item in cluster)))
        position = _nearest_clear(target, max(MIN_GRID_CELL_SIZE, target - tolerance),
                                  min(extent - MIN_GRID_CELL_SIZE, target + tolerance), insets, horizontal=False)
        if not positions or abs(position - positions[-1]) >= MIN_GRID_CELL_SIZE:
            positions.append(position)
            supports.append(min(1.0, len({item[0] for item in cluster}) / len(rows)))
    vertical = _regularize((0, *sorted(positions), extent), extent)
    return vertical, _clamp(sum(supports) / len(supports))


def _is_bullet(token, median_height, extent):
    value = token.text.strip()
    return bool(value) and len(value) <= 2 and (
        all(character in BULLET_MARKS for character in value)
        or (token.box.width <= median_height * BULLET_WIDTH_HEIGHT_RATIO
            and token.box.left <= extent * BULLET_LEFT_MARGIN_RATIO))


# ---- 把 SLANet 的近似坐标吸到原图证据上，拓扑不变 ----

def snap_grid(grid, rules, tokens):
    insets = [_inset(token.box) for token in tokens]
    horizontal = _snap_axis(grid.horizontal_lines, rules.image_height, rules.horizontal_lines, insets, True)
    vertical = _snap_axis(grid.vertical_lines, rules.image_width, rules.vertical_lines, insets, False)
    return GridSpec(horizontal, vertical, grid.merges)


def _snap_axis(lines, extent, rule_lines, insets, horizontal):
    snapped = [0]
    for index, target in enumerate(lines[1:-1], start=1):
        neighbour = max(MIN_SNAP_RADIUS, min(target - lines[index - 1], lines[index + 1] - target) // 2)
        radius = min(MAX_SNAP_RADIUS, max(MIN_SNAP_RADIUS, round(extent * SNAP_RADIUS_RATIO)), neighbour)
        candidates = [line for line in rule_lines
                      if abs(line.position - target) <= radius and line.support >= RULE_EVIDENCE_THRESHOLD]
        if candidates:
            snapped.append(max(candidates, key=lambda line: (
                line.support * RULE_SNAP_SUPPORT_WEIGHT + min(1.0, line.span / extent) * RULE_SNAP_SPAN_WEIGHT
                - abs(line.position - target) / radius * RULE_SNAP_DISTANCE_PENALTY)).position)
        else:
            snapped.append(_nearest_clear(target, max(MIN_GRID_CELL_SIZE, target - radius),
                                          min(extent - MIN_GRID_CELL_SIZE, target + radius), insets,
                                          horizontal=horizontal))
    snapped.append(extent)
    return _regularize(snapped, extent)


# ---- 打分：不同来源的候选在同一套原图证据上比较 ----

def score(grid, strategy, tokens, rules, *, alignment, topology):
    crossing = _crossing_ratio(grid, tokens)
    if strategy == STRUCTURE:
        evidence = 1.0 if rules.strong else alignment * WEAK_STRUCTURE_ALIGNMENT_SCALE
    else:
        evidence = 1.0 if not rules.strong and grid.column_count == 1 else MULTICOLUMN_TEXT_EVIDENCE
    raw = (SCORE_BASE
           + NON_CROSSING_WEIGHT * (1.0 - crossing)
           + RULE_SUPPORT_WEIGHT * _rule_support(grid, rules)
           + WHITESPACE_SUPPORT_WEIGHT * _whitespace_support(grid, tokens)
           + REPEATED_ALIGNMENT_WEIGHT * _clamp(alignment)
           + TOPOLOGY_VALIDITY_WEIGHT * _clamp(topology)
           + STRATEGY_EVIDENCE_WEIGHT * evidence
           - CROSSING_PENALTY_WEIGHT * crossing
           - UNEXPLAINED_RULE_PENALTY_WEIGHT * _unexplained_rules(grid, rules))
    return Candidate(grid, strategy, _clamp(raw))


def _crossing_ratio(grid, tokens):
    """被网格线穿过的文字占比；合并单元格内部不画的线不算穿过。"""
    if not tokens:
        return 0.0
    crossed = 0
    for token in tokens:
        box = _inset(token.box)
        column = _cell_index(grid.vertical_lines, round(token.box.center_x))
        row = _cell_index(grid.horizontal_lines, round(token.box.center_y))
        crossed += any(
            box.top < value < box.bottom
            and not any(merge.top < index < merge.bottom and merge.left <= column < merge.right for merge in grid.merges)
            for index, value in enumerate(grid.horizontal_lines[1:-1], start=1)
        ) or any(
            box.left < value < box.right
            and not any(merge.left < index < merge.right and merge.top <= row < merge.bottom for merge in grid.merges)
            for index, value in enumerate(grid.vertical_lines[1:-1], start=1)
        )
    return crossed / len(tokens)


def _rule_support(grid, rules):
    values = (_axis_rule_support(grid.horizontal_lines, rules.horizontal_lines, rules.image_height)
              + _axis_rule_support(grid.vertical_lines, rules.vertical_lines, rules.image_width))
    return sum(values) / len(values)


def _axis_rule_support(lines, rule_lines, extent):
    tolerance = max(RULE_MATCH_MIN_TOLERANCE, round(extent * RULE_MATCH_TOLERANCE_RATIO))
    return [max((rule.support for rule in rule_lines if abs(rule.position - line) <= tolerance), default=0.0)
            for line in lines]


def _unexplained_rules(grid, rules):
    """有线表里明显的线没被网格用上，说明候选漏了行列。"""
    if not rules.strong:
        return 0.0
    pairs = ((_strong(rules.horizontal_lines, rules.image_width), grid.horizontal_lines, rules.image_height),
             (_strong(rules.vertical_lines, rules.image_height), grid.vertical_lines, rules.image_width))
    total = unexplained = 0.0
    for strong, lines, extent in pairs:
        tolerance = max(RULE_MATCH_MIN_TOLERANCE, round(extent * RULE_MATCH_TOLERANCE_RATIO))
        for rule in strong:
            total += rule.support
            if not any(abs(rule.position - line) <= tolerance for line in lines):
                unexplained += rule.support
    return unexplained / total if total else 0.0


def _whitespace_support(grid, tokens):
    internal = grid.row_count - 1 + grid.column_count - 1
    if internal <= 0 or not tokens:
        return 0.0
    boxes = [_inset(token.box) for token in tokens]
    supported = (sum(not any(box.top < value < box.bottom for box in boxes) for value in grid.horizontal_lines[1:-1])
                 + sum(not any(box.left < value < box.right for box in boxes) for value in grid.vertical_lines[1:-1]))
    return supported / internal


def _repeated_alignment(grid, tokens):
    internal = grid.vertical_lines[1:-1]
    if not internal or not tokens:
        return 0.0
    values = []
    for line in internal:
        aligned = 0
        for row in range(grid.row_count):
            row_tokens = [token for token in tokens
                          if grid.horizontal_lines[row] <= token.box.center_y <= grid.horizontal_lines[row + 1]]
            if (any(token.box.right <= line for token in row_tokens)
                    and any(token.box.left >= line for token in row_tokens)
                    and not any(token.box.left < line < token.box.right for token in row_tokens)):
                aligned += 1
        values.append(min(1.0, aligned / max(2, grid.row_count * COLUMN_REPEAT_RATIO)))
    return sum(values) / len(values)


# ---- 汇总 ----

def suggest(image, tokens, structure=None):
    """structure 为 SLANet 网格（失败时 None）；返回按分数排好的候选。"""
    height, width = image.shape[:2]
    rules = detect_rules(image)
    candidates = []
    if structure is not None:
        snapped = snap_grid(structure, rules, tokens)
        candidates.append(score(snapped, STRUCTURE, tokens, rules,
                                alignment=_repeated_alignment(snapped, tokens), topology=1.0))
    text = text_grid(tokens, width, height) if tokens else None
    if text is not None:
        candidates.append(score(text[0], TEXT_ROWS, tokens, rules, alignment=text[1], topology=TEXT_TOPOLOGY_VALIDITY))

    # 有线表证据强时，同分优先信结构候选。
    ranked, seen = [], set()
    for candidate in sorted(candidates, key=lambda item: (item.score, rules.strong and item.strategy == STRUCTURE),
                            reverse=True):
        if candidate.grid not in seen:
            seen.add(candidate.grid)
            ranked.append(candidate)
    if not ranked or ranked[0].score < MIN_AUTOMATIC_SCORE:
        manual = Candidate(GridSpec((0, height), (0, width)), MANUAL, 0.0)
        confidence = _clamp(MIN_AUTOMATIC_SCORE - ranked[0].score) if ranked else 0.0
        return Suggestion(manual, tuple(item for item in ranked if item.grid != manual.grid), confidence,
                          MESSAGES[MANUAL])
    selected, alternatives = ranked[0], tuple(ranked[1:])
    confidence = selected.score
    if alternatives and selected.score - alternatives[0].score < AMBIGUOUS_SCORE_DELTA:
        confidence *= AMBIGUOUS_CONFIDENCE_SCALE
    return Suggestion(selected, alternatives, _clamp(confidence), MESSAGES[selected.strategy])


# ---- 几何工具 ----

def _inset(rect):
    """文字框四边各收 10%，避免框边擦线被误判为穿过。"""
    dx = min(max(1, round(rect.width * TOKEN_INSET_RATIO)), max(1, (rect.width - 1) // 2))
    dy = min(max(1, round(rect.height * TOKEN_INSET_RATIO)), max(1, (rect.height - 1) // 2))
    left, right, top, bottom = rect.left + dx, rect.right - dx, rect.top + dy, rect.bottom - dy
    if right <= left:
        left, right = rect.left, rect.right
    if bottom <= top:
        top, bottom = rect.top, rect.bottom
    return Rect(left, top, right, bottom)


def _nearest_clear(target, lower, upper, boxes, *, horizontal):
    """区间内穿过文字最少、离文字边缘最远且靠近目标的位置。"""
    if lower > upper:
        return target
    best, best_rank = max(lower, min(upper, target)), (-math.inf, -math.inf)
    spans = [(box.top, box.bottom) if horizontal else (box.left, box.right) for box in boxes]
    for position in range(lower, upper + 1):
        crossings = sum(start < position < end for start, end in spans)
        clearance = min((min(abs(position - start), abs(position - end)) for start, end in spans), default=0)
        rank = (-crossings, clearance - abs(position - target) * WHITESPACE_DISTANCE_PENALTY)
        if rank > best_rank:
            best, best_rank = position, rank
    return best


def _regularize(lines, extent):
    result = [0]
    internal = len(lines) - 2
    for offset, value in enumerate(lines[1:-1]):
        lower = result[-1] + MIN_GRID_CELL_SIZE
        upper = extent - (internal - offset + 1) * MIN_GRID_CELL_SIZE
        if lower > upper:
            raise ValueError("网格放不进图像范围")
        result.append(max(lower, min(upper, round(value))))
    result.append(extent)
    return tuple(result)


def _cell_index(lines, coordinate):
    return max(0, min(len(lines) - 2, bisect_right(lines, coordinate) - 1))


def _clamp(value):
    return max(0.0, min(1.0, float(value)))
