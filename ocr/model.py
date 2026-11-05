"""识别证据、派生版面与人工编辑的全文共享同一图像版本。"""

from dataclasses import dataclass, field, replace
from hashlib import sha256
import math
from uuid import uuid4

from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QImage, QPixmap

from .layout import LAYOUTS, arrange_results, format_text


@dataclass(frozen=True)
class OCRResult:
    """polygon 为原图物理像素四点，box 为按图像 DPR 换算的逻辑区域。"""
    box: QRect
    text: str
    score: float
    paragraph: int = -1
    line: int = -1
    item_id: str = field(default_factory=lambda: uuid4().hex)
    polygon: tuple = ()
    raw_index: int = -1


def _image_key(image):
    if image.isNull():
        return "no-image"
    pixels = image.convertToFormat(QImage.Format_RGBA8888)
    content = sha256(bytes(pixels.constBits())).hexdigest()
    return f"{image.width()}x{image.height()}@{image.devicePixelRatio():.17g}:{content}"


class OCRDocument(QObject):
    """拥有独立图像快照；原始证据、版面和人工编辑分开保存。

    人工编辑只有一份全文（draft），不回写到每个识别框：原图文字层是透明的，改了也看不见（实测决定）。
    """
    changed = Signal()
    focused = Signal(object)

    def __init__(self, results, image=None):
        super().__init__()
        self._image = image.toImage().copy() if isinstance(image, QPixmap) else image.copy() if image is not None else QImage()
        self.image_key = _image_key(self._image)
        self._raw = tuple(replace(item, box=QRect(item.box)) for item in results)
        self._layout = self._arrange()
        self._mode = "paragraph"
        self._draft = None
        self.focused_ids = ()

    @property
    def image(self):
        return QImage(self._image)

    @property
    def raw_results(self):
        return tuple(replace(item, box=QRect(item.box)) for item in self._raw)

    def _arrange(self):
        arranged = arrange_results(self._raw)
        if self._image.isNull():
            # 无原图的历史文字结果仍可复制；无几何的条目不冒充原图定位。
            paragraph = max((item.paragraph for item in arranged), default=-1) + 1
            arranged.extend(replace(item, paragraph=paragraph, line=index)
                            for index, item in enumerate(self._raw) if item.text and item.box.isEmpty())
        return arranged

    @property
    def results(self):
        return [replace(item, box=QRect(item.box)) for item in self._layout]

    def __iter__(self):
        return iter(self.results)

    def __len__(self):
        return len(self._layout)

    def __bool__(self):
        return bool(self._layout)

    def matches(self, image):
        image = image.toImage() if isinstance(image, QPixmap) else image
        return not image.isNull() and self.image_key == _image_key(image)

    def focus(self, ids):
        ids = tuple(ids)
        known = {item.item_id for item in self._raw}
        if not set(ids) <= known:
            raise ValueError("定位条目不属于当前识别结果")
        self.focused_ids = ids
        self.focused.emit(ids)

    @property
    def layout(self):
        return self._mode

    @property
    def draft(self):
        """人工改过的全文；没改过为 None。"""
        return self._draft

    def text(self):
        """复制、贴出去的文字：改过就是改过的全文，否则按当前排版整理识别结果。"""
        return self._draft if self._draft is not None else format_text(self._layout, self._mode)

    def set_layout(self, mode):
        """换排版就是重新整理识别结果，手动改过的全文随之丢弃（界面先确认）。"""
        if mode not in {key for key, _label, _tip in LAYOUTS}:
            raise ValueError("未知排版")
        if mode != self._mode or self._draft is not None:
            self._mode, self._draft = mode, None
            self.changed.emit()

    def set_draft(self, text):
        if not isinstance(text, str):
            raise ValueError("全文必须是文字")
        # 改回和排版结果一模一样，就等于没改过。
        draft = None if text == format_text(self._layout, self._mode) else text
        if draft != self._draft:
            self._draft = draft
            self.changed.emit()

    def to_payload(self):
        return {
            "version": 2, "image_key": self.image_key,
            "raw": [{"id": item.item_id, "box": list(item.box.getRect()), "text": item.text,
                     "score": item.score, "polygon": [list(point) for point in item.polygon],
                     "raw_index": item.raw_index} for item in self._raw],
            "layout": [{"id": item.item_id, "paragraph": item.paragraph, "line": item.line} for item in self._layout],
            "mode": self._mode, "draft": self._draft,
        }

    @classmethod
    def from_payload(cls, payload, image):
        """持久化边界严格校验；失败由贴图恢复保留原记录并告知用户。"""
        if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] not in (1, 2):
            raise ValueError("不支持的 OCR 数据版本")
        image = image.toImage() if isinstance(image, QPixmap) else image
        if image.isNull() or payload.get("image_key") != _image_key(image):
            raise ValueError("OCR 结果对应的图像内容或缩放比例不一致")
        raw, layout = payload.get("raw"), payload.get("layout")
        if not isinstance(raw, list) or not isinstance(layout, list):
            raise ValueError("OCR 原始结果或布局格式无效")
        records, identifiers = [], set()
        for entry in raw:
            if not isinstance(entry, dict):
                raise ValueError("OCR 条目格式无效")
            identifier, box, polygon = entry.get("id"), entry.get("box"), entry.get("polygon")
            if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier) or identifier in identifiers:
                raise ValueError("OCR 条目标识无效或重复")
            if not isinstance(box, list) or len(box) != 4 or any(type(value) is not int or not -(2**30) < value < 2**30 for value in box):
                raise ValueError("OCR 矩形格式无效")
            if box[2] < 0 or box[3] < 0 or not isinstance(polygon, list) or len(polygon) not in (0, 4):
                raise ValueError("OCR 几何数据无效")
            for point in polygon:
                if not isinstance(point, list) or len(point) != 2 or any(type(value) not in (int, float) or not -(2**30) < value < 2**30 or not math.isfinite(value) for value in point):
                    raise ValueError("OCR 四点框格式无效")
            text, score, raw_index = entry.get("text"), entry.get("score"), entry.get("raw_index")
            if not isinstance(text, str) or type(score) not in (float, int) or not 0 <= score <= 1 or not math.isfinite(score) or type(raw_index) is not int:
                raise ValueError("OCR 原始文字或置信度无效")
            identifiers.add(identifier)
            records.append(OCRResult(QRect(*box), text, score, item_id=identifier,
                                     polygon=tuple(tuple(point) for point in polygon), raw_index=raw_index))
        document = cls(records, image)
        by_id = {item.item_id: item for item in document._raw}
        arranged, used = [], set()
        for entry in layout:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or entry["id"] not in by_id or entry["id"] in used:
                raise ValueError("OCR 布局引用无效或重复")
            if any(type(entry.get(key)) is not int or entry[key] < 0 for key in ("paragraph", "line")):
                raise ValueError("OCR 行段编号无效")
            used.add(entry["id"])
            arranged.append(replace(by_id[entry["id"]], paragraph=entry["paragraph"], line=entry["line"]))
        if used != {item.item_id for item in document._layout}:
            raise ValueError("OCR 布局遗漏可用条目")
        document._layout = arranged
        draft = payload.get("draft")
        if draft is not None and not isinstance(draft, str):
            raise ValueError("OCR 全文格式无效")
        if payload["version"] == 1:
            draft = _legacy_draft(document, payload, draft)
            # 旧版全文是原样换行的；按它显示，切排版时才会提示丢掉修改。
            if draft is not None:
                document._mode = "lines"
        else:
            mode = payload.get("mode")
            if mode not in {key for key, _label, _tip in LAYOUTS}:
                raise ValueError("OCR 排版无效")
            document._mode = mode
        document._draft = draft
        return document


def _legacy_draft(document, payload, draft):
    """第 1 版逐行/逐段校正并进全文，旧贴图里改过的字不丢；没改过就按新排版重新整理。"""
    corrections, paragraphs = payload.get("corrections"), payload.get("paragraphs")
    if not isinstance(corrections, dict) or any(not isinstance(text, str) for text in corrections.values()) \
            or not isinstance(paragraphs, list):
        raise ValueError("OCR 校正格式无效")
    if draft is not None or not corrections and not paragraphs:
        return draft
    items = [replace(item, text=corrections.get(item.item_id, item.text)) for item in document._layout]
    for entry in paragraphs:
        if not isinstance(entry, dict) or not isinstance(entry.get("ids"), list) or not isinstance(entry.get("text"), str):
            raise ValueError("OCR 段落校正格式无效")
        ids = set(entry["ids"])
        members = [index for index, item in enumerate(items) if item.item_id in ids]
        if members:
            # 整段改写的文字放在段首那一条，段里其余条目不再重复输出。
            items[members[0]] = replace(items[members[0]], text=entry["text"])
            for index in reversed(members[1:]):
                del items[index]
    return format_text(items, "lines")
