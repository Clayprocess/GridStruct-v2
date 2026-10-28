"""表格导出：剪贴板（TSV + HTML）、CSV、HTML、XLSX。

移植自 v1 application/exporting.py 与 xlsx_exporting.py。XLSX 一律按文本写入，
避免 Excel 把识别出的“=…”、网址或长数字自动解释成公式、链接或科学计数。
"""

import csv
from html import escape
from io import StringIO
from math import ceil
from unicodedata import east_asian_width

from styles import tokens as T
from .model import PENDING

SHEET_NAME = "识别结果"
FONT_NAME = "Microsoft YaHei UI"
MINIMUM_COLUMN_WIDTH = 8.0
MAXIMUM_COLUMN_WIDTH = 42.0
DEFAULT_ROW_HEIGHT = 22.0
MAXIMUM_ROW_HEIGHT = 110.0


def to_tsv(document):
    """Excel/WPS 粘贴用；格内换行、制表符按 CSV 规则加引号，不会把一格拆成多行。"""
    output = StringIO(newline="")
    csv.writer(output, delimiter="\t", lineterminator="\r\n").writerows(document.rows)
    return output.getvalue()


def to_csv(document):
    output = StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(document.rows)
    return output.getvalue()


def to_html(document):
    """合并单元格写成 rowspan/colspan；粘贴到 Excel/WPS/Word 时保留合并关系。"""
    grid, rows = document.grid, []
    for row, values in enumerate(document.rows):
        cells = []
        for column, value in enumerate(values):
            if grid.is_covered(row, column):
                continue
            merge = grid.merge_at(row, column)
            attributes = ""
            if merge is not None and merge.bottom - merge.top > 1:
                attributes += f' rowspan="{merge.bottom - merge.top}"'
            if merge is not None and merge.right - merge.left > 1:
                attributes += f' colspan="{merge.right - merge.left}"'
            cells.append(f"<td{attributes}>{escape(value).replace(chr(10), '<br>')}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table>{''.join(rows)}</table>"


def save(document, path):
    """按扩展名写文件；CSV 带 BOM，Excel 双击打开中文不乱码。"""
    suffix = path.rsplit(".", 1)[-1].lower()
    if suffix == "xlsx":
        _save_xlsx(document, path)
        return
    if suffix == "csv":
        content, encoding = to_csv(document), "utf-8-sig"
    else:
        content = ('<!doctype html><html><head><meta charset="utf-8"><title>表格识别结果</title></head><body>'
                   f"{to_html(document)}</body></html>")
        encoding = "utf-8"
    with open(path, "w", encoding=encoding, newline="") as handle:
        handle.write(content)


def _save_xlsx(document, path):
    import xlsxwriter
    grid = document.grid
    options = {"strings_to_formulas": False, "strings_to_urls": False, "strings_to_numbers": False}
    with xlsxwriter.Workbook(path, options) as workbook:
        sheet = workbook.add_worksheet(SHEET_NAME)
        base = {"border": 1, "border_color": T.LIGHT["border"], "font_name": FONT_NAME, "font_size": 10,
                "num_format": "@", "text_wrap": True, "valign": "vcenter"}
        normal = workbook.add_format(base)
        pending = workbook.add_format({**base, "bg_color": T.LIGHT["pending"]})
        widths = _column_widths(document)
        for column, width in enumerate(widths):
            sheet.set_column(column, column, width)
        for row, height in enumerate(_row_heights(document, widths)):
            sheet.set_row(row, height)
        for row, values in enumerate(document.cells):
            for column, cell in enumerate(values):
                if grid.is_covered(row, column):
                    continue
                style = pending if cell.status == PENDING else normal
                merge = grid.merge_at(row, column)
                if merge is not None:
                    sheet.merge_range(merge.top, merge.left, merge.bottom - 1, merge.right - 1, cell.text, style)
                else:
                    sheet.write_string(row, column, cell.text, style)
                if cell.status == PENDING:
                    sheet.write_comment(row, column, f"GridStruct：{_confidence(cell)}，尚未人工确认",
                                        {"author": "GridStruct", "visible": False})


def _confidence(cell):
    weighted, minimum = cell.weighted_confidence, cell.minimum_confidence
    if weighted is None:
        return "模型置信度未知"
    return f"模型置信度（加权平均）{weighted:.0%}，最低 {minimum:.0%}"


def _spans(document, row, column):
    merge = document.grid.merge_at(row, column)
    return (1, 1) if merge is None else (merge.bottom - merge.top, merge.right - merge.left)


def _column_widths(document):
    widths = [MINIMUM_COLUMN_WIDTH] * document.grid.column_count
    for row, values in enumerate(document.rows):
        for column, value in enumerate(values):
            if document.grid.is_covered(row, column):
                continue
            span = _spans(document, row, column)[1]
            preferred = min(MAXIMUM_COLUMN_WIDTH, max(MINIMUM_COLUMN_WIDTH, (_display_width(value) + 2) / span))
            for index in range(column, column + span):
                widths[index] = max(widths[index], preferred)
    return widths


def _row_heights(document, widths):
    heights = [DEFAULT_ROW_HEIGHT] * document.grid.row_count
    for row, values in enumerate(document.rows):
        for column, value in enumerate(values):
            if document.grid.is_covered(row, column):
                continue
            row_span, column_span = _spans(document, row, column)
            available = sum(widths[column:column + column_span])
            lines = sum(max(1, ceil(_display_width(line) / max(1, int(available) - 1))) for line in value.split("\n"))
            total = min(MAXIMUM_ROW_HEIGHT * row_span, max(DEFAULT_ROW_HEIGHT * row_span, 17.0 * lines + 5.0))
            for index in range(row, row + row_span):
                heights[index] = max(heights[index], total / row_span)
    return heights


def _display_width(value):
    """全角字符按 2 个字符宽度估算 Excel 列宽。"""
    return max((sum(4 if c == "\t" else 2 if east_asian_width(c) in {"F", "W"} else 1 for c in line)
                for line in value.split("\n")), default=0)
