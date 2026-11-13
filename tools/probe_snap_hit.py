"""吸附真机验收（仅 Windows）：直接调用 capture/snap.py 的点命中实现，在真实窗口里按网格取点。

和截图时走同一套代码（MSAA 逐层命中 + 浏览器二次命中 + Excel/Word/Zoom 的 UIA + 补父链），
但不打开截图遮罩。只记录矩形、层数和耗时，不记录任何名称或文字。
用法（目标窗口放最前、别被挡住）：
    python tools/probe_snap_hit.py --process excel.exe winword.exe --grid 16x10
    python tools/probe_snap_hit.py --class Shell_TrayWnd --grid 40x1      # 任务栏（太矮，按进程名找不到）
    python tools/probe_snap_hit.py --class Shell_TrayWnd --point 1300,1420  # 打印该点 UIA 每层子元素数和空矩形数
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
    parser.add_argument("--class", dest="window_class", help="按窗口类名找窗口，如 Shell_TrayWnd")
    parser.add_argument("--grid", default="16x10")
    parser.add_argument("--point", help="只测这一点（屏幕物理坐标 x,y），并打印 UIA 逐层的子元素情况")
    args = parser.parse_args()
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    provider = snap._Accessibility()
    columns, rows = (int(v) for v in args.grid.lower().split("x"))
    try:
        if args.window_class:
            user32.FindWindowW.argtypes, user32.FindWindowW.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.HWND
            hwnds = [user32.FindWindowW(args.window_class, None)]
        else:
            hwnds = targets({p.lower() for p in args.process})
        if args.point:
            x, y = (int(v) for v in args.point.split(","))
            for hwnd in hwnds:
                print(f"{process_name(hwnd)} 命中路径：{[r.getRect() for r in provider.hit_path(hwnd, x, y)]}")
                uia = provider._uia
                if uia is None:
                    print("  （该窗口没走 UIA）")
                    continue
                node = uia._root(hwnd)
                for depth in range(31):
                    kids = uia._kids(node)
                    inside = [(c, r) for c, r in kids if r.contains(x, y)]
                    empty = sum(1 for _c, r in kids if r.isEmpty())
                    print(f"  第 {depth} 层：子元素 {len(kids)} 个，空矩形 {empty} 个，"
                          f"含该点 {[(r.getRect(), uia._types.get(c.value)) for c, r in inside]}（矩形, 控件类型）")
                    if not inside:
                        break
                    node = min(inside, key=lambda item: item[1].width() * item[1].height())[0]
            return
        for hwnd in hwnds:
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
