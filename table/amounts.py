"""金额分隔符的保守预览：含义不唯一时保留原文，不使用浮点数。"""

from dataclasses import dataclass
import re
from typing import Literal


@dataclass(frozen=True)
class AmountPreview:
    before: str
    after: str
    status: Literal["changed", "unchanged", "ambiguous", "skipped"]
    reason: str = ""


_FORMATS = ((".", ","), (",", "."), (" ", ","), (" ", "."), ("'", "."))


def _candidate(text: str, group: str, decimal: str) -> str | None:
    parts = text.split(decimal)
    if len(parts) > 2 or (len(parts) == 2 and not re.fullmatch(r"[0-9]+", parts[1])):
        return None
    integer = parts[0]
    if not re.fullmatch(r"[0-9]+", integer) and not re.fullmatch(
        r"[0-9]{1,3}(?:" + re.escape(group) + r"[0-9]{3})+", integer
    ):
        return None
    digits = integer.replace(group, "")
    # 前导零常见于编号；不猜测它是否是金额，也不丢失这些零。
    if len(digits) > 1 and digits.startswith("0"):
        return None
    formatted = re.sub(r"\B(?=([0-9]{3})+(?![0-9]))", ",", digits)
    return formatted + ("." + parts[1] if len(parts) == 2 else "")


def preview_amount(raw: str) -> AmountPreview:
    """返回原表预览；调用方只应用 changed，并将整批应用放入一次撤销。"""
    if not isinstance(raw, str):
        raise TypeError("金额单元格必须是字符串")
    text = raw.strip()
    if not text:
        return AmountPreview(raw, raw, "skipped", "空白")

    prefix = suffix = ""
    if text.startswith("(") and text.endswith(")"):
        prefix, suffix, text = "(", ")", text[1:-1]
    elif text[0] in "+-−":
        prefix, text = text[0], text[1:]
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("’", "'")
    candidates = {
        prefix + value + suffix
        for group, decimal in _FORMATS
        if (value := _candidate(text, group, decimal)) is not None
    }
    if not candidates:
        return AmountPreview(raw, raw, "skipped", "非金额或无法识别，保留原值")
    if len(candidates) > 1:
        return AmountPreview(raw, raw, "ambiguous", "分隔符有多种含义，保留原值")
    after = candidates.pop()
    return AmountPreview(raw, after, "unchanged" if after == raw else "changed")
