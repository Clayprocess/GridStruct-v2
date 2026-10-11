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


def format_text(results):
    """同一行片段空格连接、行间换行、段落间空行；兼容未标记的历史结果。"""
    output, previous = [], None
    for index, item in enumerate(results):
        key = (item.paragraph, item.line) if item.paragraph >= 0 else (0, index)
        if previous is not None:
            output.append("\n\n" if key[0] != previous[0] else "\n" if key[1] != previous[1] else " ")
        output.append(item.text)
        previous = key
    return "".join(output)
