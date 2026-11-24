"""金额预览纯数据冒烟：python tools/check_amounts.py，无需模型或界面。"""

from pathlib import Path
import sys

# 单独验证纯数据模块，避免 table 包入口加载 Qt 和模型控制器。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "table"))
from amounts import preview_amount


def main():
    changes = {
        "1234": "1,234",
        "1234,50": "1,234.50",
        "1.234,5600": "1,234.5600",
        "1 234,56": "1,234.56",
        "1\u00a0234,56": "1,234.56",
        "1\u202f234.56": "1,234.56",
        "1'234.50": "1,234.50",
        "1’234.50": "1,234.50",
        "-1.234,00": "-1,234.00",
        "+1234.50": "+1,234.50",
        "−1234,50": "−1,234.50",
        "(1.234,50)": "(1,234.50)",
        "1.234.567": "1,234,567",
        "0,123": "0.123",
        "12,0000": "12.0000",
        "123456789012345678901234567890,00100": "123,456,789,012,345,678,901,234,567,890.00100",
    }
    count = 0
    for raw, after in changes.items():
        preview = preview_amount(raw)
        assert (preview.before, preview.after, preview.status) == (raw, after, "changed"), preview
        count += 1
    for raw in ("1,234", "1.234", "12,345", "12.345", "-1,234", "(1.234)"):
        preview = preview_amount(raw)
        assert preview.status == "ambiguous" and preview.after == raw and preview.reason, preview
        count += 1
    for raw in ("0", "120", "1,234.56", "1,234,567", "0.123", "-0.00", "(1,234.50)"):
        preview = preview_amount(raw)
        assert preview.status == "unchanged" and preview.after == raw, preview
        count += 1
    for raw in (
        "", "  ", "金额", "USD 1,234.56", "¥1,234", "001234", "001,234", "01.23", "12O",
        "2026-11-25", "2026/11/25", "2026.11.25", "25.11.2026", "12:30", "20%",
        "=1+2", "=SUM(A1:A3)", "+SUM(A1:A3)", "1e6", "1,23,456", "1.2.3",
        "1 23,45", "1\t234,50", "1\n234,50", "(-1.234,50)", "--123", "1,", ".5",
    ):
        preview = preview_amount(raw)
        assert preview.status == "skipped" and preview.after == raw and preview.reason, preview
        count += 1
    try:
        preview_amount(1234)
    except TypeError:
        count += 1
    else:
        raise AssertionError("拒绝非字符串输入")
    print(f"金额预览：{count} 项通过；保留歧义、非金额与所有有效小数位。")


if __name__ == "__main__":
    main()
