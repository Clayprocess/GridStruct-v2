"""元素吸附诊断（仅 Windows，只用标准库）：在真实程序窗口里按网格取点，对比 MSAA 与 UIA 能拿到的元素层级。

每个点记录：
- MSAA：从窗口对象逐层 accHitTest 的 (角色, 矩形)、耗时；再沿 accParent 补出的祖先、耗时（与 capture/snap.py 同一套调用）；
- UIA：ElementFromPoint 的 (控件类型, 类名, 框架, 矩形) 及沿 RawViewWalker 到窗口的父链、耗时；
- 鼠标下子窗口的类名。
不记录任何名称、值或文字内容（Excel 单元格名称里有表格内容）。

用法（先把要测的窗口放到最前面，别被其他窗口挡住）：
    python tools/probe_snap_apps.py --process excel.exe --grid 16x10 --out probe-excel.jsonl
--process 可写多个；不写则测鼠标当前所在的窗口。结尾打印每个窗口的汇总。
"""

import argparse
import ctypes
import json
import statistics
import sys
import time
from ctypes import wintypes
from uuid import UUID

if sys.platform != "win32":
    sys.exit("只能在 Windows 上运行")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
oleacc = ctypes.WinDLL("oleacc")
ole32 = ctypes.OleDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")

for name, args, res in [
    ("WindowFromPoint", [wintypes.POINT], wintypes.HWND),
    ("GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
    ("GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
    ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
    ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
    ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
    ("GetCursorPos", [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
    ("SetProcessDpiAwarenessContext", [ctypes.c_void_p], wintypes.BOOL),
]:
    getattr(user32, name).argtypes, getattr(user32, name).restype = args, res
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
oleacc.AccessibleObjectFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_void_p)]
oleacc.AccessibleObjectFromWindow.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p)]
oleaut32.SysFreeString.argtypes, oleaut32.SysFreeString.restype = [ctypes.c_void_p], None


def guid(text):
    return (ctypes.c_byte * 16).from_buffer_copy(UUID(text).bytes_le)


IID_IACCESSIBLE = guid("618736e0-3c3d-11cf-810c-00aa00389b71")
CLSID_CUIAUTOMATION = guid("ff48dba4-60ef-4201-aa87-54103eef594e")
IID_IUIAUTOMATION = guid("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")
VT_I4, VT_DISPATCH = 3, 9
MSAA_ROLES = {9: "window", 10: "client", 11: "menupopup", 12: "menuitem", 15: "document", 16: "pane",
              20: "grouping", 22: "toolbar", 24: "table", 25: "columnheader", 26: "rowheader", 27: "column",
              28: "row", 29: "cell", 30: "link", 33: "list", 34: "listitem", 35: "outline", 36: "outlineitem",
              37: "pagetab", 41: "statictext", 42: "text", 43: "pushbutton", 44: "checkbutton",
              45: "radiobutton", 46: "combobox", 60: "pagetablist", 62: "splitbutton", 64: "grip"}
UIA_TYPES = {50000: "Button", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit", 50005: "Hyperlink",
             50006: "Image", 50007: "ListItem", 50008: "List", 50009: "Menu", 50010: "MenuBar",
             50011: "MenuItem", 50018: "Tab", 50019: "TabItem", 50020: "Text", 50021: "ToolBar",
             50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group", 50028: "DataGrid",
             50029: "DataItem", 50030: "Document", 50031: "SplitButton", 50032: "Window", 50033: "Pane",
             50034: "Header", 50035: "HeaderItem", 50036: "Table", 50037: "TitleBar"}


class VariantValue(ctypes.Union):
    _fields_ = [("lVal", ctypes.c_long), ("pointer", ctypes.c_void_p), ("pad", ctypes.c_byte * 16)]


class Variant(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("value", VariantValue)]


def call(pointer, index, argtypes, *args):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    result = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])(pointer, *args)
    if result < 0:
        raise OSError(f"HRESULT 0x{result & 0xffffffff:08x}")
    return result


def release(pointer):
    if pointer:
        call(pointer, 2, [])


def query(dispatch):
    accessible = ctypes.c_void_p()
    try:
        call(dispatch, 0, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
             ctypes.byref(IID_IACCESSIBLE), ctypes.byref(accessible))
    finally:
        release(dispatch)
    return accessible


def location(accessible, child=0):
    values = [ctypes.c_long() for _ in range(4)]
    call(accessible, 22, [ctypes.POINTER(ctypes.c_long)] * 4 + [Variant],
         *(ctypes.byref(v) for v in values), Variant(vt=VT_I4, value=VariantValue(lVal=child)))
    return [v.value for v in values]


def role(accessible, child=0):
    result = Variant()
    try:
        call(accessible, 13, [Variant, ctypes.POINTER(Variant)],
             Variant(vt=VT_I4, value=VariantValue(lVal=child)), ctypes.byref(result))
    except OSError:
        return "?"
    return MSAA_ROLES.get(result.value.lVal, str(result.value.lVal)) if result.vt == VT_I4 else "str"


def contains(rect, x, y):
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


def msaa(root, x, y):
    """与 capture/snap.py 相同的逐层 accHitTest，再沿 accParent 补祖先。"""
    path, accessible = [], root
    started = time.perf_counter()
    try:
        for _ in range(31):
            variant = Variant()
            call(accessible, 24, [ctypes.c_long, ctypes.c_long, ctypes.POINTER(Variant)], x, y, ctypes.byref(variant))
            if variant.vt == VT_DISPATCH and variant.value.pointer:
                child = query(variant.value.pointer)
                rect = location(child)
                if not contains(rect, x, y):
                    release(child)
                    break
                path.append((role(child), rect))
                if accessible.value != root.value:
                    release(accessible)
                accessible = child
            elif variant.vt == VT_I4 and variant.value.lVal:
                rect = location(accessible, variant.value.lVal)
                path.append((role(accessible, variant.value.lVal) + "#item", rect))
                break
            else:
                break
    except OSError as error:
        path.append(("error", str(error)))
    hit_ms = (time.perf_counter() - started) * 1000
    ancestors, current = [], accessible
    started = time.perf_counter()
    try:
        while current.value != root.value and len(ancestors) < 40:
            parent = ctypes.c_void_p()
            call(current, 7, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(parent))
            if not parent:
                break
            nxt = query(parent)
            if current.value != accessible.value:
                release(current)
            current = nxt
            rect = location(current)
            if not contains(rect, x, y):
                break
            ancestors.append((role(current), rect))
    except OSError as error:
        ancestors.append(("error", str(error)))
    finally:
        if current.value not in (accessible.value, root.value):
            release(current)
        if accessible.value != root.value:
            release(accessible)
    return path, hit_ms, ancestors[::-1], (time.perf_counter() - started) * 1000


class Uia:
    def __init__(self):
        self.automation = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(CLSID_CUIAUTOMATION), None, 1, ctypes.byref(IID_IUIAUTOMATION),
                               ctypes.byref(self.automation))
        self.walker = ctypes.c_void_p()
        call(self.automation, 16, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(self.walker))  # RawViewWalker

    def bstr(self, element, index):
        value = ctypes.c_void_p()
        call(element, index, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(value))
        text = ctypes.wstring_at(value.value) if value.value else ""
        oleaut32.SysFreeString(value)
        return text

    def describe(self, element):
        kind, rect = ctypes.c_int(), wintypes.RECT()
        call(element, 21, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(kind))
        call(element, 43, [ctypes.POINTER(wintypes.RECT)], ctypes.byref(rect))
        return (UIA_TYPES.get(kind.value, str(kind.value)), self.bstr(element, 30), self.bstr(element, 40),
                [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top])

    def chain(self, x, y, window_rect):
        element = ctypes.c_void_p()
        started = time.perf_counter()
        call(self.automation, 7, [wintypes.POINT, ctypes.POINTER(ctypes.c_void_p)],
             wintypes.POINT(x, y), ctypes.byref(element))
        hit_ms = (time.perf_counter() - started) * 1000
        found = []
        try:
            while element and len(found) < 40:
                info = self.describe(element)
                found.append(info)
                if info[3] == window_rect:
                    break
                parent = ctypes.c_void_p()
                call(self.walker, 3, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], element, ctypes.byref(parent))
                release(element)
                element = parent
        except OSError as error:
            found.append(("error", str(error), "", []))
        finally:
            release(element)
        return found[::-1], hit_ms, (time.perf_counter() - started) * 1000


def process_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return ""
    size, path = wintypes.DWORD(1024), ctypes.create_unicode_buffer(1024)
    ok = kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size))
    kernel32.CloseHandle(handle)
    return path.value.rsplit("\\", 1)[-1].lower() if ok else ""


def class_name(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def targets(processes, window_class=None):
    if window_class:
        user32.FindWindowW.argtypes, user32.FindWindowW.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.HWND
        return [user32.FindWindowW(window_class, None)]
    if not processes:
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        return [user32.GetAncestor(user32.WindowFromPoint(point), 2)]
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @proc
    def visit(hwnd, _):
        rect = wintypes.RECT()
        if (user32.IsWindowVisible(hwnd) and user32.GetWindowRect(hwnd, ctypes.byref(rect))
                and rect.right - rect.left > 200 and rect.bottom - rect.top > 150
                and process_name(hwnd) in processes):
            found.append(hwnd)
        return True
    user32.EnumWindows.argtypes = [proc, wintypes.LPARAM]
    user32.EnumWindows(visit, 0)
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process", nargs="*", default=[], help="进程名，如 excel.exe winword.exe")
    parser.add_argument("--class", dest="window_class", help="按窗口类名找窗口，如 Shell_TrayWnd（任务栏太矮，按进程名筛不到）")
    parser.add_argument("--grid", default="16x10", help="每个窗口取点的列x行")
    parser.add_argument("--out", default="probe-snap.jsonl")
    args = parser.parse_args()
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # 每屏 DPI 感知，坐标与 GridStruct 一致
    ole32.CoInitializeEx(None, 0)
    uia = Uia()
    columns, rows = (int(v) for v in args.grid.lower().split("x"))
    with open(args.out, "a", encoding="utf-8") as out:
        for hwnd in targets({p.lower() for p in args.process}, args.window_class):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            window_rect = [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top]
            root = ctypes.c_void_p()
            started = time.perf_counter()
            if oleacc.AccessibleObjectFromWindow(hwnd, 0, ctypes.byref(IID_IACCESSIBLE), ctypes.byref(root)) < 0:
                root = None
            root_ms = (time.perf_counter() - started) * 1000
            name, records = process_name(hwnd), []
            for j in range(rows):
                for i in range(columns):
                    x = rect.left + (i + 0.5) * window_rect[2] // columns
                    y = rect.top + (j + 0.5) * window_rect[3] // rows
                    child = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
                    if user32.GetAncestor(child, 2) != hwnd:
                        continue  # 被别的窗口挡住
                    record = {"process": name, "window_class": class_name(hwnd), "child_class": class_name(child),
                              "window": window_rect, "point": [int(x), int(y)], "root_ms": round(root_ms, 1)}
                    if root:
                        path, hit_ms, ancestors, walk_ms = msaa(root, int(x), int(y))
                        record.update(msaa=path, msaa_ms=round(hit_ms, 1), msaa_parents=ancestors,
                                      msaa_parents_ms=round(walk_ms, 1))
                    chain, uia_hit_ms, uia_ms = uia.chain(int(x), int(y), window_rect)
                    record.update(uia=chain, uia_hit_ms=round(uia_hit_ms, 1), uia_ms=round(uia_ms, 1))
                    records.append(record)
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
            release(root)
            summarize(name, class_name(hwnd), records)


def smallest(rects):
    sizes = [r[2] * r[3] for r in rects if len(r) == 4 and r[2] > 0 and r[3] > 0]
    return min(sizes) if sizes else None


def summarize(name, window_class, records):
    if not records:
        print(f"{name} [{window_class}]：没有可测的点（窗口被挡住？）")
        return
    def median(key):
        values = [r[key] for r in records if key in r]
        return round(statistics.median(values), 1) if values else None
    def depth(key):
        return round(statistics.mean(len(r.get(key, [])) for r in records), 1)
    msaa_small = [smallest([p[1] for p in r.get("msaa", []) + r.get("msaa_parents", []) if p[0] != "error"])
                  for r in records]
    uia_small = [smallest([c[3] for c in r["uia"] if c[0] != "error"]) for r in records]
    print(f"{name} [{window_class}] 点数 {len(records)}")
    print(f"  MSAA：命中中位 {median('msaa_ms')}ms，补父链中位 {median('msaa_parents_ms')}ms，"
          f"平均层数 {depth('msaa')}+{depth('msaa_parents')}，最小元素面积中位 "
          f"{statistics.median([s for s in msaa_small if s] or [0])}")
    print(f"  UIA ：命中中位 {median('uia_hit_ms')}ms，含父链中位 {median('uia_ms')}ms，平均层数 {depth('uia')}，"
          f"最小元素面积中位 {statistics.median([s for s in uia_small if s] or [0])}")
    kinds = {}
    for r in records:
        if r["uia"]:
            kinds[r["uia"][-1][0]] = kinds.get(r["uia"][-1][0], 0) + 1
    print(f"  UIA 最内层控件类型：{kinds}")


if __name__ == "__main__":
    main()
