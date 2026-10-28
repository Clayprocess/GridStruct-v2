"""SLANet_plus 表格结构模型，直接用 onnxruntime 加载。

不引入 rapid-table：它会再拉一套模型下载和匹配逻辑。预处理、解码和 bbox 还原
按 rapid-table 1.0.3 的 TablePreprocess / TableLabelDecode / adapt_slanet_plus 实现，
模型文件与它包内的 slanet-plus.onnx 是同一份（SHA256 见 MODEL_SHA256）。
结构转网格移植自 v1 infrastructure/slanet_adapter.py。
"""

import os
from itertools import pairwise
from statistics import median
from threading import Lock

import numpy as np

from .model import CellMerge, GridSpec

MODEL_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "models", "slanet-plus.onnx"))
MODEL_SHA256 = "d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b"
INPUT_SIZE = 488
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
MIN_CELL_SIZE = 3
MAX_GRID_AXIS = 200
MAX_STRUCTURE_CELLS = 2000
CELL_TOKENS = ("<td>", "<td", "<td></td>")


class TableStructureError(RuntimeError):
    """结构识别失败；调用方退回规则网格或手动网格。"""


class SlanetPlus:
    """懒加载单例；同一会话串行推理。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._session = None
            cls._instance._lock = Lock()
        return cls._instance

    def _ensure(self):
        if self._session is not None:
            return
        import onnxruntime as ort
        if not os.path.isfile(MODEL_PATH):
            raise FileNotFoundError(f"缺少表格结构模型：{MODEL_PATH}")
        session = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
        # 词表写在模型元数据里；首尾补上 sos/eos 后与输出的 50 类对齐。
        tokens = session.get_modelmeta().custom_metadata_map["character"].splitlines()
        self._characters = ["sos", *tokens, "eos"]
        self._input = session.get_inputs()[0].name
        self._session = session

    def structure(self, image):
        """image 为 BGR uint8；返回 (HTML 结构 token, 每格 [x0, y0, x1, y1] 原图坐标)。"""
        import cv2
        height, width = image.shape[:2]
        ratio = INPUT_SIZE / max(height, width)
        resized = cv2.resize(image, (int(width * ratio), int(height * ratio)))
        tensor = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.float32)
        tensor[:resized.shape[0], :resized.shape[1]] = (resized.astype(np.float32) / 255 - MEAN) / STD
        tensor = tensor.transpose(2, 0, 1)[None]
        with self._lock:
            self._ensure()
            locations, probabilities = self._session.run(None, {self._input: tensor})
        end = len(self._characters) - 1
        structure, boxes = [], []
        for index, char_index in enumerate(probabilities[0].argmax(axis=1)):
            if index > 0 and char_index == end:
                break
            if char_index in (0, end):
                continue
            text = self._characters[char_index]
            structure.append(text)
            if text in CELL_TOKENS:
                # SLANet_plus 的框按 488 方形画布归一化，还原时除以缩放比，而不是乘原图宽高。
                points = locations[0, index] * INPUT_SIZE / ratio
                boxes.append((points[0::2].min(), points[1::2].min(), points[0::2].max(), points[1::2].max()))
        return structure, boxes

    def suggest_grid(self, image):
        structure, boxes = self.structure(image)
        return grid_from_structure(logic_points(structure), boxes,
                                   image_width=image.shape[1], image_height=image.shape[0])


def logic_points(structure):
    """HTML token → 每格 (起行, 止行, 起列, 止列)，止行/止列包含在内。"""
    cells, occupied = [], set()
    row = column = 0
    index = 0
    while index < len(structure):
        token = structure[index]
        if token == "<tr>":
            column = 0
        elif token == "</tr>":
            row += 1
        elif token.startswith("<td"):
            rowspan = colspan = 1
            if token != "<td></td>":
                index += 1
                while index < len(structure) and not structure[index].startswith(">"):
                    name, _, value = structure[index].strip().partition("=")
                    if name == "colspan":
                        colspan = int(value.strip("\"'"))
                    elif name == "rowspan":
                        rowspan = int(value.strip("\"'"))
                    index += 1
            while (row, column) in occupied:
                column += 1
            cells.append((row, row + rowspan - 1, column, column + colspan - 1))
            occupied.update((r, c) for r in range(row, row + rowspan) for c in range(column, column + colspan))
            column += colspan
        index += 1
    return cells


def grid_from_structure(logical, boxes, *, image_width, image_height):
    """把逻辑格和框合成可编辑网格；结构不自洽时抛 TableStructureError。"""
    if not logical:
        raise TableStructureError("未检测到表格单元格")
    if len(logical) != len(boxes):
        raise TableStructureError("表格逻辑结构与单元格边界数量不一致")
    if len(logical) > MAX_STRUCTURE_CELLS:
        raise TableStructureError("检测到的表格单元格数量异常")
    row_count = max(cell[1] for cell in logical) + 1
    column_count = max(cell[3] for cell in logical) + 1
    if row_count > MAX_GRID_AXIS or column_count > MAX_GRID_AXIS:
        raise TableStructureError("检测到的表格行列数异常")
    if row_count * MIN_CELL_SIZE > image_height or column_count * MIN_CELL_SIZE > image_width:
        raise TableStructureError("检测到的表格行列数超出截图尺寸")

    occupied = set()
    horizontal = [[] for _ in range(row_count + 1)]
    vertical = [[] for _ in range(column_count + 1)]
    merges = []
    for (row_start, row_end, column_start, column_end), box in zip(logical, boxes):
        left, top, right, bottom = (float(np.clip(value, 0, limit)) for value, limit in
                                    zip(box, (image_width, image_height, image_width, image_height)))
        if right - left < 1 or bottom - top < 1:
            raise TableStructureError("检测到尺寸异常的表格单元格")
        covered = {(r, c) for r in range(row_start, row_end + 1) for c in range(column_start, column_end + 1)}
        if occupied & covered:
            raise TableStructureError("检测到相互重叠的表格单元格")
        occupied |= covered
        horizontal[row_start].append(top)
        horizontal[row_end + 1].append(bottom)
        vertical[column_start].append(left)
        vertical[column_end + 1].append(right)
        if row_end > row_start or column_end > column_start:
            merges.append(CellMerge(row_start, column_start, row_end + 1, column_end + 1))
    if len(occupied) != row_count * column_count:
        raise TableStructureError("检测到的表格结构不完整")
    return GridSpec(_build_axis(horizontal, image_height), _build_axis(vertical, image_width), tuple(merges))


def _build_axis(samples, extent):
    """每条线取样本中位数，缺样本的线在两侧已知线之间插值，再保证最小格宽。"""
    raw = [median(values) if values else None for values in samples]
    raw[0], raw[-1] = 0.0, float(extent)
    known = [index for index, value in enumerate(raw) if value is not None]
    for left, right in pairwise(known):
        for index in range(left + 1, right):
            raw[index] = raw[left] + (raw[right] - raw[left]) * (index - left) / (right - left)
    count = len(raw) - 1
    lines = [round(value) for value in raw]
    for index in range(1, count):
        lines[index] = max(index * MIN_CELL_SIZE, min(extent - (count - index) * MIN_CELL_SIZE, lines[index]))
    for index in range(1, count + 1):
        lines[index] = max(lines[index], lines[index - 1] + MIN_CELL_SIZE)
    lines[-1] = extent
    for index in range(count - 1, 0, -1):
        lines[index] = min(lines[index], lines[index + 1] - MIN_CELL_SIZE)
    lines[0] = 0
    return tuple(lines)
