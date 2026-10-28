"""离屏合成内容：比较旧版与新版匹配，不采集用户桌面。"""

import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont, QTransform, QImage, QFontDatabase
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from capture.longshot_matching import find_overlap, find_shift, find_viewport_end
from styles import tokens as T


def make_page(height=4000):
    page = QPixmap(1200, height)
    page.fill(QColor(T.LIGHT["background"]))
    with QPainter(page) as painter:
        # 保持长页与普通视口使用相同的小画布绘制路径。
        for origin in range(0, height, 2000):
            tile = QPixmap(1200, min(2000, height - origin))
            tile.fill(QColor(T.LIGHT["background"]))
            with QPainter(tile) as text_painter:
                text_painter.setPen(QColor(T.LIGHT["foreground"]))
                font = QFont("Arial")
                font.setPixelSize(18)
                text_painter.setFont(font)
                for local_row in range(tile.height() // 25):
                    row = origin // 25 + local_row
                    y = local_row * 25
                    text_painter.drawText(20, y + 20,
                                         f"ROW {row:04d}  Invoice {row*7919:08d}  Product {row*137:06d}  Amount {row*23}.71")
                    text_painter.drawRect(780 + row % 13 * 5, y + 3, 30 + row % 17 * 7, 14)
            painter.drawPixmap(0, origin, tile)
    return page


def measure(function, frames):
    function(*frames[0][:2])
    times, outputs = [], []
    for source, target, expected in frames:
        start = perf_counter()
        outputs.append(function(source, target))
        times.append((perf_counter() - start) * 1000)
    return {"samples": len(times), "correct": sum(out == frame[2] for out, frame in zip(outputs, frames)),
            "p50_ms": round(float(np.percentile(times, 50)), 3),
            "p95_ms": round(float(np.percentile(times, 95)), 3), "max_ms": round(max(times), 3)}


def measure_feed(stitcher_type, page):
    stitcher = stitcher_type()
    start = perf_counter()
    initial = stitcher.feed(page.copy(0, 0, 1200, 900))
    first_ms = (perf_counter() - start) * 1000
    times, correct, accepted = [], 0, 0
    for index in range(1, 101):
        y = index * 137
        frame = page.copy(0, y, 1200, 900)
        start = perf_counter()
        accepted += bool(stitcher.feed(frame))
        times.append((perf_counter() - start) * 1000)
        correct += stitcher.result.toImage() == page.copy(0, 0, 1200, y + 900).toImage()
    return {"samples": len(times), "accepted": accepted, "correct_pixels": correct,
            "initial_accepted": initial, "initial_feed_ms": round(first_ms, 3),
            "final_height": stitcher.result.height(),
            "p50_ms": round(float(np.percentile(times, 50)), 3),
            "p95_ms": round(float(np.percentile(times, 95)), 3), "max_ms": round(max(times), 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    # offscreen 插件不自动枚举 Windows 字体，否则数字会全部变成相同的方框。
    QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / "arial.ttf"))
    page = make_page()
    frame = lambda y: page.copy(0, y, 1200, 900)
    cases = []
    for name, first, second, expected in [
        ("ordinary", 0, 137, 763), ("ordinary_next", 137, 274, 763),
        ("overlap_48", 0, 852, 48), ("overlap_48_next", 852, 1704, 48),
        ("small_tail_39", 137, 176, 861), ("no_overlap_similar_rows", 0, 1704, 0),
    ]:
        actual = find_overlap(frame(first), frame(second))
        cases.append({"case": name, "actual": actual, "expected": expected})
    blank = QPixmap(1200, 900)
    blank.fill(QColor(T.LIGHT["background"]))
    cases.append({"case": "blank", "actual": find_overlap(blank, blank), "expected": 0})
    separator_source, separator_target = blank.copy(), blank.copy()
    with QPainter(separator_source) as painter:
        painter.fillRect(20, 898, 1100, 2, QColor(T.LIGHT["foreground"]))
    with QPainter(separator_target) as painter:
        painter.fillRect(20, 22, 1100, 2, QColor(T.LIGHT["foreground"]))
    cases.append({"case": "separator_only_refused", "actual": find_overlap(separator_source, separator_target), "expected": 0})
    repeated = QPixmap(1200, 900)
    with QPainter(repeated) as painter:
        for y in range(0, 900, 25):
            painter.drawPixmap(0, y, page.copy(0, 0, 1200, 25))
    cases.append({"case": "repeated_rows", "actual": find_overlap(repeated, repeated), "expected": 0})
    dynamic = frame(137)
    with QPainter(dynamic) as painter:
        painter.fillRect(30, 30, 50, 30, QColor(T.LIGHT["foreground"]))
    cases.append({"case": "dynamic_overlap_refused", "actual": find_overlap(frame(0), dynamic), "expected": 0})
    outside_overlap = frame(137)
    with QPainter(outside_overlap) as painter:
        painter.fillRect(30, 850, 50, 30, QColor(T.LIGHT["foreground"]))
    cases.append({"case": "dynamic_outside_overlap", "actual": find_overlap(frame(0), outside_overlap), "expected": 763})
    # 容差后备：闪烁光标、抗锯齿抖动、吸顶/吸底栏要能接上，重复内容和空白仍拒绝。
    cases.append({"case": "shift_dynamic_overlap", "actual": find_shift(frame(0), dynamic)[0], "expected": 763})
    noisy = frame(137).toImage()
    for index in range(400):
        noisy.setPixelColor(index * 37 % 1200, index * 53 % 900, QColor(T.LIGHT["muted_foreground"]))
    cases.append({"case": "shift_noise", "actual": find_shift(frame(0), QPixmap.fromImage(noisy))[0], "expected": 763})

    def sticky(y):
        pm = frame(y)
        with QPainter(pm) as painter:
            painter.fillRect(0, 0, 1200, 60, QColor(T.LIGHT["primary"]))
            painter.fillRect(0, 850, 1200, 50, QColor(T.LIGHT["secondary"]))
            painter.fillRect(40, 862, 300, 16, QColor(T.LIGHT["foreground"]))
        return pm
    cases.append({"case": "shift_sticky_bars", "actual": list(find_shift(sticky(0), sticky(137))), "expected": [713, 60, 50]})
    tall_repeated = QPixmap(1200, 1000)
    with QPainter(tall_repeated) as painter:
        for y in range(0, 1000, 25):
            painter.drawPixmap(0, y, page.copy(0, 0, 1200, 25))
    cases.append({"case": "shift_repeated_refused", "expected": [0, 0, 0], "actual": list(
        find_shift(tall_repeated.copy(0, 0, 1200, 900), tall_repeated.copy(0, 30, 1200, 900)))})
    cases.append({"case": "shift_blank_refused", "actual": list(find_shift(blank, blank)), "expected": [0, 0, 0]})
    from capture.longshot import ImageStitcher
    stitcher = ImageStitcher()
    stitcher.feed(sticky(0))
    for y in range(211, 211 * 12, 211):
        stitcher.feed(sticky(y))
    expected_sticky = page.copy(0, 0, 1200, 211 * 11 + 900)
    with QPainter(expected_sticky) as painter:
        painter.fillRect(0, 0, 1200, 60, QColor(T.LIGHT["primary"]))
        bottom = 211 * 11 + 850
        painter.fillRect(0, bottom, 1200, 50, QColor(T.LIGHT["secondary"]))
        painter.fillRect(40, bottom + 12, 300, 16, QColor(T.LIGHT["foreground"]))
    cases.append({"case": "stitch_sticky_bars_pixels", "actual": stitcher.result.toImage() == expected_sticky.toImage(),
                  "expected": True})
    horizontal_page = page.transformed(QTransform().rotate(-90))
    a = horizontal_page.copy(0, 0, 900, 1200).transformed(QTransform().rotate(90))
    b = horizontal_page.copy(852, 0, 900, 1200).transformed(QTransform().rotate(90))
    cases.append({"case": "horizontal_48", "actual": find_overlap(a, b), "expected": 48})
    long_page = make_page(29000)
    for y in [0, 137, 13579, 28100]:
        start = perf_counter()
        actual = find_viewport_end(long_page, long_page.copy(0, y, 1200, 900))
        cases.append({"case": f"relocate_{y}", "actual": actual, "expected": y + 900,
                      "ms": round((perf_counter() - start) * 1000, 3)})
    cases.append({"case": "relocate_repeated_refused", "actual": find_viewport_end(repeated, repeated.copy(0, 0, 1200, 450)), "expected": 0})
    frames = [(frame(i * 7), frame(i * 7 + 137), 763) for i in range(100)]
    report = {"cases": cases, "new_warm": measure(find_overlap, frames)}
    if args.baseline:
        module = ast.parse(args.baseline.read_text(encoding="utf-8"))
        module.body = [node for node in module.body if isinstance(node, ast.FunctionDef)
                       and node.name in {"find_overlap", "_to_gray_small", "_gray_array", "_overlap_score"}]
        namespace = {"QPixmap": QPixmap, "QImage": QImage, "Qt": Qt}
        exec(compile(module, str(args.baseline), "exec"), namespace)
        report["old_warm"] = measure(namespace["find_overlap"], frames)
    del frames
    from capture.longshot import ImageStitcher
    feed_page = make_page(15000)
    report["new_feed_warm"] = measure_feed(ImageStitcher, feed_page)
    if args.baseline:
        baseline_spec = importlib.util.spec_from_file_location("capture._longshot_baseline", args.baseline)
        baseline_module = importlib.util.module_from_spec(baseline_spec)
        baseline_spec.loader.exec_module(baseline_module)
        report["old_feed_warm"] = measure_feed(baseline_module.ImageStitcher, feed_page)
    cold = subprocess.run([sys.executable, "-c",
                           "from time import perf_counter; t=perf_counter(); import capture.longshot; print((perf_counter()-t)*1000)"],
                          cwd=ROOT, capture_output=True, text=True, check=True)
    report["new_cold_module_import_ms"] = round(float(cold.stdout.strip()), 3)
    report["measurement_scope"] = "Matcher timing excludes module imports. Feed includes matching, full growing image copy and bookkeeping; excludes capture, UI preview and pixel assertion. Cold fresh-process module import includes numpy and Qt dependencies; no old cold-import comparison."
    report["passed"] = all(case["actual"] == case["expected"] for case in cases) and report["new_warm"]["correct"] == 100
    report["passed"] &= report["new_feed_warm"]["correct_pixels"] == 100 and report["new_feed_warm"]["accepted"] == 100
    content = json.dumps(report, ensure_ascii=False, indent=2)
    print(content)
    if args.output:
        args.output.write_text(content + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
