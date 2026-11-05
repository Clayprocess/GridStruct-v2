"""识别证据、派生版面与人工编辑的全文共享同一图像版本。"""

from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from hashlib import sha256
import math
from uuid import uuid4

from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QImage, QPixmap

from .layout import LAYOUTS, arrange_results, format_text


@dataclass(frozen=True)
class OCRResult:
    """polygon 为原图物理像素四点，box 为按图像 DPR 换算的逻辑区域。
    chars 为识别模型给出的逐字横向位置 ((字, left, right), ...)，原图物理像素，不含空格。"""
    box: QRect
    text: str
    score: float
    paragraph: int = -1
    line: int = -1
    item_id: str = field(default_factory=lambda: uuid4().hex)
    polygon: tuple = ()
    raw_index: int = -1
    chars: tuple = ()


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
        # 改过的全文里每条识别文字的位置；None 表示还没算（读回的会话数据），用时按文字比对补上。
        self._spans = None
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

    def recognized_text(self):
        """按当前排版整理的识别结果，即没改过时的全文。"""
        return format_text(self._layout, self._mode)

    def text(self):
        """复制、贴出去的文字：改过就是改过的全文，否则按当前排版整理识别结果。"""
        return self._draft if self._draft is not None else self.recognized_text()

    def spans(self):
        """每条识别文字在当前全文里的位置 [(起, 止, 条目)]（Python 下标）；改过、删掉的那条不在里面。"""
        if self._draft is None:
            return map_spans(self.recognized_text(), self.recognized_text(), self.results)
        if self._spans is None:
            self._spans = map_spans(self._draft, self.recognized_text(), self.results)
        return list(self._spans)

    def set_layout(self, mode):
        """换排版就是重新整理识别结果，手动改过的全文随之丢弃（界面先确认）。"""
        if mode not in {key for key, _label, _tip in LAYOUTS}:
            raise ValueError("未知排版")
        if mode != self._mode or self._draft is not None:
            self._mode, self._draft, self._spans = mode, None, None
            self.changed.emit()

    def set_draft(self, text, edit=None):
        """edit = (起点, 删掉几个字, 新加几个字)，是编辑框报来的这一次改动（Python 下标）。

        有它就按改动挪各条的位置：改动之前的不动，之后的整体平移，被改到的那条不再定位。
        只看改前改后两份文字分不清删的是两处相同文字里的哪一处（Clay 审查 PR #47 时复现），
        所以位置要跟着每一次改动走，只有读回的会话数据才退回按文字比对。
        """
        if not isinstance(text, str):
            raise ValueError("全文必须是文字")
        # 改回和排版结果一模一样，就等于没改过。
        draft = None if text == self.recognized_text() else text
        if draft == self._draft:
            return
        old, spans = self.text(), self.spans()
        self._draft, self._spans = draft, None
        if draft is not None and edit is not None:
            start, removed, added = edit
            if 0 <= start and start + removed <= len(old) and len(text) - len(old) == added - removed \
                    and text[:start] == old[:start] and text[start + added:] == old[start + removed:]:
                shift = added - removed
                kept = [(begin, end, item) if end <= start else (begin + shift, end + shift, item)
                        for begin, end, item in spans if end <= start or begin >= start + removed]
                original = self.recognized_text()
                self._spans = restore_spans(text, original, map_spans(original, original, self.results), kept)
        self.changed.emit()

    def to_payload(self):
        return {
            "version": 2, "image_key": self.image_key,
            "raw": [{"id": item.item_id, "box": list(item.box.getRect()), "text": item.text,
                     "score": item.score, "polygon": [list(point) for point in item.polygon],
                     "raw_index": item.raw_index,
                     "chars": [[text, left, right] for text, left, right in item.chars]} for item in self._raw],
            "layout": [{"id": item.item_id, "paragraph": item.paragraph, "line": item.line} for item in self._layout],
            "mode": self._mode, "draft": self._draft,
            # 改过的全文里各条的位置是跟着每次改动算出来的，读回时不能靠文字比对重猜（重复文字会认错）。
            "spans": None if self._draft is None else [[begin, end, item.item_id] for begin, end, item in self.spans()],
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
            chars = entry.get("chars", [])
            # 逐字位置是后加的可选字段：旧记录没有；格式不对就丢掉，表格分格会退回按字宽估算。
            if not isinstance(chars, list) or any(
                    not isinstance(item, list) or len(item) != 3 or not isinstance(item[0], str)
                    or len(item[0]) != 1
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in item[1:])
                    or item[1] >= item[2]
                    for item in chars):
                chars = []
            identifiers.add(identifier)
            records.append(OCRResult(QRect(*box), text, score, item_id=identifier,
                                     polygon=tuple(tuple(point) for point in polygon), raw_index=raw_index,
                                     chars=tuple((item[0], float(item[1]), float(item[2])) for item in chars)))
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
            spans = payload.get("spans")
            if spans is not None:
                if draft is None or not isinstance(spans, list):
                    raise ValueError("OCR 定位数据无效")
                order = {item.item_id: index for index, item in enumerate(document._layout)}
                items = {item.item_id: item for item in document._layout}
                restored, last, previous = [], 0, -1
                for entry in spans:
                    if not isinstance(entry, list) or len(entry) != 3 or type(entry[0]) is not int \
                            or type(entry[1]) is not int or entry[2] not in order:
                        raise ValueError("OCR 定位条目无效")
                    begin, end, identifier = entry
                    if not last <= begin < end <= len(draft) or order[identifier] <= previous \
                            or draft[begin:end] != items[identifier].text.strip():
                        raise ValueError("OCR 定位与全文不符")
                    restored.append((begin, end, replace(items[identifier], box=QRect(items[identifier].box))))
                    last, previous = end, order[identifier]
                document._spans = restored
        document._draft = draft
        return document


# 第 1 版全文之后又逐行改过时，两份修改合不起来，都留在全文里让用户挑。
_LEGACY_BOTH = "\n\n—— 升级前有两份修改：上面是后来逐行改的，下面是更早在“全文”里改的，保留需要的部分后删掉其余 ——\n\n"


def _legacy_draft(document, payload, draft):
    """第 1 版逐行/逐段校正并进全文，旧贴图里改过的字不丢；没改过就按新排版重新整理。"""
    corrections, paragraphs = payload.get("corrections"), payload.get("paragraphs")
    revision, draft_revision = payload.get("revision"), payload.get("draft_revision")
    if not isinstance(corrections, dict) or any(not isinstance(text, str) for text in corrections.values()) \
            or not isinstance(paragraphs, list):
        raise ValueError("OCR 校正格式无效")
    if type(revision) is not int or type(draft_revision) is not int or not 0 <= draft_revision <= revision:
        raise ValueError("OCR 校正版本无效")
    # 全文是在最后一次逐行校正之后改的，已经包含了那些校正。
    if not corrections and not paragraphs or draft is not None and draft_revision == revision:
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
    corrected = format_text(items, "lines")
    return corrected if draft is None else corrected + _LEGACY_BOTH + draft


def map_spans(text, original, results):
    """按文字比对找出每条识别文字在全文里的位置 [(起, 止, 条目)]（Python 下标）。

    original 是排版整理出的原文，条目在里面按阅读顺序依次出现，位置是确定的；再按改动把位置
    映射到 text：只有整条落在没改过的片段里才保留，改过、删掉的那条不再定位。
    两处相同的文字删了一处时分不清是哪处，这里只是兜底，编辑时以 set_draft 的逐次改动为准。
    """
    base, start = [], 0
    for item in results:
        needle = item.text.strip()
        found = original.find(needle, start) if needle else -1
        if found >= 0:
            base.append((found, found + len(needle), item))
            start = found + len(needle)
    if text == original:
        return base
    # 改动通常集中在一处：先去掉相同的头尾，只对中间做比对，长文也不卡。
    head = 0
    while head < min(len(text), len(original)) and text[head] == original[head]:
        head += 1
    tail = 0
    while tail < min(len(text), len(original)) - head and text[-1 - tail] == original[-1 - tail]:
        tail += 1
    blocks = [(0, 0, head), (len(original) - tail, len(text) - tail, tail)]
    matcher = SequenceMatcher(None, original[head:len(original) - tail], text[head:len(text) - tail], autojunk=False)
    blocks += [(a + head, b + head, size) for a, b, size in matcher.get_matching_blocks() if size]
    kept = []
    for begin, end, item in base:
        block = next((block for block in blocks if block[0] <= begin and end <= block[0] + block[2]), None)
        if block is not None:
            kept.append((begin + block[1] - block[0], end + block[1] - block[0], item))
    return kept


def restore_spans(text, original, base, kept):
    """撤销等把某条改回原样时，恢复它的定位。

    只看两条仍定位着的条目（或全文开头、结尾）之间的那一段：从左边那条往后、从右边那条往前，
    和识别原文逐字相同的部分里的条目，就是原来那几条。位置由两边的锚点决定，不在全文里搜相同的字，
    所以重复文字也不会认错（Clay 审查 PR #47：改两处、撤销一处后，撤回的那条要能重新定位）。
    """
    where = {item.item_id: (begin, end) for begin, end, item in kept}
    result, group = [], []
    original_at, text_at = 0, 0
    for begin, end, item in base + [(len(original), len(original), None)]:
        if item is not None and item.item_id not in where:
            group.append((begin, end, item))
            continue
        current = where[item.item_id] if item is not None else (len(text), len(text))
        if group:
            before, after = original[original_at:begin], text[text_at:current[0]]
            head = 0
            while head < min(len(before), len(after)) and before[head] == after[head]:
                head += 1
            tail = 0
            while tail < min(len(before), len(after)) - head and before[-1 - tail] == after[-1 - tail]:
                tail += 1
            for start, stop, missing in group:
                if stop <= original_at + head:
                    result.append((start - original_at + text_at, stop - original_at + text_at, missing))
                elif start >= begin - tail:
                    result.append((start - begin + current[0], stop - begin + current[0], missing))
            group = []
        if item is not None:
            result.append((current[0], current[1], item))
        original_at, text_at = end, current[1]
    return result
