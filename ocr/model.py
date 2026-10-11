"""识别证据、派生布局与人工校正共享同一图像版本。"""

from dataclasses import dataclass, field, replace
from hashlib import sha256
import math
from uuid import uuid4

from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QImage, QPixmap

from .layout import arrange_results, format_text


@dataclass(frozen=True)
class OCRResult:
    """polygon 为原图物理像素四点，box 为按图像 DPR 换算的逻辑区域。"""
    box: QRect
    text: str
    score: float
    paragraph: int = -1
    line: int = -1
    item_id: str = field(default_factory=lambda: uuid4().hex)
    original_text: str | None = None
    polygon: tuple = ()
    source_ids: tuple = ()
    raw_index: int = -1

    def __post_init__(self):
        if self.original_text is None:
            object.__setattr__(self, "original_text", self.text)


def _image_key(image):
    if image.isNull():
        return "no-image"
    pixels = image.convertToFormat(QImage.Format_RGBA8888)
    content = sha256(bytes(pixels.constBits())).hexdigest()
    return f"{image.width()}x{image.height()}@{image.devicePixelRatio():.17g}:{content}"


class OCRDocument(QObject):
    """拥有独立图像快照；原始证据、版面和人工编辑分开保存。"""
    changed = Signal()
    focused = Signal(object)

    def __init__(self, results, image=None):
        super().__init__()
        self._image = image.toImage().copy() if isinstance(image, QPixmap) else image.copy() if image is not None else QImage()
        self.image_key = _image_key(self._image)
        self._raw = tuple(replace(item, box=QRect(item.box), text=item.original_text, source_ids=()) for item in results)
        self._layout = self._arrange()
        self._corrections = {}
        self._paragraphs = {}
        self._draft = None
        self._revision = 0
        self._draft_revision = 0
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
        by_id = {item.item_id: item for item in self._layout}
        grouped = {identifier: ids for ids in self._paragraphs for identifier in ids}
        emitted, output = set(), []
        for item in self._layout:
            ids = grouped.get(item.item_id)
            if ids is None:
                output.append(replace(item, box=QRect(item.box), text=self._corrections.get(item.item_id, item.original_text)))
            elif ids not in emitted:
                emitted.add(ids)
                members = [by_id[identifier] for identifier in ids]
                box = QRect()
                for member in members:
                    box = box.united(member.box)
                # 整段自由改写仅保留区域定位，不能伪造新文字到原行框的映射。
                output.append(replace(members[0], box=box, text=self._paragraphs[ids],
                                      original_text=format_text(members), polygon=(), source_ids=ids,
                                      score=min(member.score for member in members)))
        return output

    def __iter__(self):
        return iter(self.results)

    def __len__(self):
        return len(self.results)

    def __bool__(self):
        return bool(self._layout)

    def groups(self):
        groups = {}
        for item in self._layout:
            groups.setdefault(item.paragraph, []).append(item.item_id)
        return [tuple(ids) for ids in groups.values()]

    def text(self):
        return format_text(self.results)

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

    def correct(self, ids, text):
        ids = tuple(ids)
        if not isinstance(text, str) or not ids or not set(ids) <= {item.item_id for item in self._layout}:
            raise ValueError("校正条目或文字无效")
        if len(ids) > 1 and ids not in self.groups():
            raise ValueError("跨段合并请使用独立导出草稿")
        previous = self.text()
        if len(ids) == 1:
            for group in tuple(self._paragraphs):
                if ids[0] in group:
                    del self._paragraphs[group]
            self._corrections[ids[0]] = text
        else:
            self._paragraphs[ids] = text
        self._notify(previous)

    def reset_correction(self, ids):
        ids = tuple(ids)
        if not ids or not set(ids) <= {item.item_id for item in self._raw}:
            raise ValueError("重置条目不属于当前识别结果")
        previous = self.text()
        for group in tuple(self._paragraphs):
            if set(group).intersection(ids):
                del self._paragraphs[group]
        for identifier in ids:
            self._corrections.pop(identifier, None)
        self._notify(previous)

    def relayout(self):
        previous = self.text()
        self._layout = self._arrange()
        self._notify(previous)

    def _notify(self, previous):
        if self.text() != previous:
            self._revision += 1
        self.changed.emit()

    @property
    def draft(self):
        return self._draft

    @property
    def draft_stale(self):
        return self._draft is not None and self._draft_revision != self._revision

    def set_draft(self, text):
        if not isinstance(text, str):
            raise ValueError("导出草稿必须是文字")
        if self._draft is None:
            self._draft_revision = self._revision
        self._draft = text
        self.changed.emit()

    def to_payload(self):
        return {
            "version": 1, "image_key": self.image_key,
            "raw": [{"id": item.item_id, "box": list(item.box.getRect()), "text": item.original_text,
                     "score": item.score, "polygon": [list(point) for point in item.polygon],
                     "raw_index": item.raw_index} for item in self._raw],
            "layout": [{"id": item.item_id, "paragraph": item.paragraph, "line": item.line} for item in self._layout],
            "corrections": dict(self._corrections),
            "paragraphs": [{"ids": list(ids), "text": text} for ids, text in self._paragraphs.items()],
            "draft": self._draft, "revision": self._revision, "draft_revision": self._draft_revision,
        }

    @classmethod
    def from_payload(cls, payload, image):
        """持久化边界严格校验；失败由贴图恢复保留原记录并告知用户。"""
        if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] != 1:
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
        corrections, paragraphs = payload.get("corrections"), payload.get("paragraphs")
        if not isinstance(corrections, dict) or not set(corrections) <= used or any(not isinstance(text, str) for text in corrections.values()) or not isinstance(paragraphs, list):
            raise ValueError("OCR 校正格式无效")
        document._corrections = dict(corrections)
        grouped = set()
        for entry in paragraphs:
            if not isinstance(entry, dict) or not isinstance(entry.get("ids"), list) or any(not isinstance(identifier, str) for identifier in entry["ids"]) or not isinstance(entry.get("text"), str):
                raise ValueError("OCR 段落校正格式无效")
            ids = tuple(entry["ids"])
            if len(ids) < 2 or ids not in document.groups() or grouped.intersection(ids):
                raise ValueError("OCR 段落校正引用无效")
            grouped.update(ids)
            document._paragraphs[ids] = entry["text"]
        draft = payload.get("draft")
        revision, draft_revision = payload.get("revision"), payload.get("draft_revision")
        if draft is not None and not isinstance(draft, str):
            raise ValueError("OCR 导出草稿格式无效")
        if type(revision) is not int or type(draft_revision) is not int or not 0 <= draft_revision <= revision:
            raise ValueError("OCR 校正版本无效")
        document._draft, document._revision, document._draft_revision = draft, revision, draft_revision
        return document
