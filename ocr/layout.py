"""按行高与空白沟槽组织本地 OCR 阅读顺序，不增加版面模型依赖。"""

from dataclasses import replace
from statistics import median


def _gap(items, horizontal, minimum):
    spans = sorted((item.box.left(), item.box.right()) if horizontal
                   else (item.box.top(), item.box.bottom()) for item in items)
    end = spans[0][1]
    gaps = []
    for start, stop in spans[1:]:
        if start - end > minimum:
            gaps.append((start - end, (start + end) / 2))
        end = max(end, stop)
    return max(gaps)[1] if gaps else None


def arrange_results(results):
    """先按整块空白分栏/分段，再按行排列；保留每个识别框供原图选字。"""
    items = [item for item in results if item.text and not item.box.isEmpty()]
    if not items:
        return []
    height = median(item.box.height() for item in items)
    pending, blocks = [items], []
    while pending:
        block = pending.pop()
        split = _gap(block, True, height * 1.5)
        horizontal = split is not None
        if split is None:
            split = _gap(block, False, height * .75)
        if split is None:
            blocks.append(block)
            continue
        first, second = [], []
        for item in block:
            coordinate = item.box.center().x() if horizontal else item.box.center().y()
            (first if coordinate < split else second).append(item)
        # 栈先处理上方/左侧；跨栏标题会先被水平留白分出，再识别下方列。
        pending.extend((second, first))
    ordered = []
    for paragraph, block in enumerate(blocks):
        rows = []
        for item in sorted(block, key=lambda value: (value.box.top(), value.box.left())):
            if rows:
                reference = rows[-1][0].box
                overlap = min(reference.bottom(), item.box.bottom()) - max(reference.top(), item.box.top()) + 1
                if overlap >= min(reference.height(), item.box.height()) * .5:
                    rows[-1].append(item)
                    continue
            rows.append([item])
        for line, row in enumerate(rows):
            ordered.extend(replace(item, paragraph=paragraph, line=line)
                           for item in sorted(row, key=lambda value: value.box.left()))
    return ordered


# 排版（同 PixPin 的 OCR 布局：自动 / 多行 / 去换行）。
LAYOUTS = (("paragraph", "段落", "同一段的几行合成一行，段与段之间换行"),
           ("lines", "原样换行", "保留图片里的每一处换行"),
           ("single", "一行", "全部合成一行"))


def saved_layout(settings):
    """识别窗口上次选的排版，直接复制也用它；设置里没有或写坏了就用“段落”。"""
    value = settings.value("ocr/layout", "paragraph")
    return value if value in {key for key, _label, _tip in LAYOUTS} else "paragraph"


def _wide(ch):
    # 汉字、假名、谚文和全角标点：这些字之间排版本来就不留空格。
    return ord(ch) >= 0x2E80


def join_prose(lines):
    """正文换行合并：只要一边是中日韩文字就不补空格，行尾连字符原样接上，其余（英文、数字）补一个空格。

    和表格格内换行（table.review.join_lines）不同：格内断开的多是长数字（“1,234 / 567”），正文里断开的是英文句子，
    “November 3, / 2026” 必须补空格。
    """
    text = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if text and not (_wide(text[-1]) or _wide(line[0]) or text[-1] == "-"):
            text += " "
        text += line
    return text


def _prose_paragraphs(lines):
    """“段落”排版再按行宽切段：没写满的一行（小标题、段尾）后面断开，首行缩进的一行前面断开。

    版面分段只认整块空白，紧挨着的小标题和正文会被并成一段；这里补上按行宽判断，阈值约两个字宽。
    """
    lefts = [min(item.box.left() for item in line) for line in lines]
    rights = [max(item.box.right() for item in line) for line in lines]
    left, right = min(lefts), max(rights)
    chunks = [[]]
    for line, start, end in zip(lines, lefts, rights):
        height = median(item.box.height() for item in line)
        if chunks[-1] and start - left > 1.5 * height:
            chunks.append([])
        chunks[-1].append(" ".join(item.text for item in line))
        if right - end > 2 * height:
            chunks.append([])
    return [join_prose(chunk) for chunk in chunks if chunk]


def format_text(results, layout="lines"):
    """同一行的片段空格连接；行与段按 layout 合并。兼容没有行段编号的历史结果。"""
    paragraphs, previous = [], None
    for index, item in enumerate(results):
        key = (item.paragraph, item.line) if item.paragraph >= 0 else (0, index)
        if previous is None or key[0] != previous[0]:
            paragraphs.append([[item]])
        elif key[1] != previous[1]:
            paragraphs[-1].append([item])
        else:
            paragraphs[-1][-1].append(item)
        previous = key
    if layout == "paragraph":
        return "\n".join(text for lines in paragraphs for text in _prose_paragraphs(lines))
    texts = [[" ".join(item.text for item in line) for line in lines] for lines in paragraphs]
    if layout == "single":
        return join_prose(line for lines in texts for line in lines)
    return "\n\n".join("\n".join(lines) for lines in texts)
