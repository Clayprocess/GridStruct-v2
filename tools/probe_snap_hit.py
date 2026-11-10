"""吸附真机验收（仅 Windows）：直接调用 capture/snap.py 的点命中实现，在真实窗口里按网格取点。

和截图时走同一套代码（MSAA 逐层命中 + 浏览器二次命中 + Excel/Word/Zoom 的 UIA + 补父链），
但不打开截图遮罩。只记录矩形、层数和耗时，不记录任何名称或文字。
用法（目标窗口放最前、别被挡住）：
    python tools/probe_snap_hit.py --process excel.exe winword.exe --grid 16x10
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture.snap as snap
from probe_snap_apps import targets, process_name, user32, wintypes, ctypes

if sys.platform != "win32":
    sys.exit("只能在 Windows 上运行")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process", nargs="*", default=[])
    parser.add_argument("--grid", default="16x10")
    args = parser.parse_args()
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    provider = snap._Accessibility()
    columns, rows = (int(v) for v in args.grid.lower().split("x"))
    try:
        for hwnd in targets({p.lower() for p in args.process}):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width, height = rect.right - rect.left, rect.bottom - rect.top
            times, depths, smallest, first = [], [], [], None
            for j in range(rows):
                for i in range(columns):
                    x, y = rect.left + (2 * i + 1) * width // (2 * columns), rect.top + (2 * j + 1) * height // (2 * rows)
                    if user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(x, y)), 2) != hwnd:
                        continue
                    started = time.perf_counter()
                    rects = provider.hit_path(hwnd, x, y)
                    hit_ms = (time.perf_counter() - started) * 1000
                    full = provider.ancestors(lambda: False) or rects
                    total_ms = (time.perf_counter() - started) * 1000
                    first = first if first is not None else round(hit_ms, 1)
                    times.append((hit_ms, total_ms, provider.one_off))
                    depths.append(len(full))
                    if full:
                        smallest.append((full[-1].width(), full[-1].height()))
            if not times:
                print(f"{process_name(hwnd)}：没有可测的点")
                continue
            hits = [t[0] for t in times if not t[2]]
            print(f"{process_name(hwnd)} 点数 {len(times)}，首次 {first}ms，"
                  f"命中中位 {statistics.median(hits) if hits else 0:.1f}ms（最大 {max(hits) if hits else 0:.1f}），"
                  f"含补父链中位 {statistics.median(t[1] for t in times):.1f}ms，"
                  f"超过 300ms 的命中 {sum(h > 300 for h in hits)} 次，一次性大批量 {sum(t[2] for t in times)} 次，"
                  f"平均层数 {statistics.mean(depths):.1f}")
            sizes = sorted(smallest, key=lambda s: s[0] * s[1])
            print(f"  最内层大小：最小 {sizes[0] if sizes else None}，中位 {sizes[len(sizes) // 2] if sizes else None}")
    finally:
        provider.close()


if __name__ == "__main__":
    main()
