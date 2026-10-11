"""UIA 控件收集回归：不创建窗口、不调用原生 UIA，也不读取桌面。

验证 _UIAutomation.window_rectangles 的广度优先遍历、
offscreen 过滤、非法矩形过滤、引用释放契约。
"""

from pathlib import Path
import sys
import ctypes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRect
from capture.snap import _UIAutomation


class ProviderTree(_UIAutomation):
    """模拟 UIA 树：children 为 {父: [子]}，rectangles 为 {节点: QRect}。"""

    def __init__(self, children, rectangles, offscreen=()):
        self._ctypes = ctypes
        self._automation = object()  # 哑指针，仅用于 _call 分发
        self.children = children
        self.rectangles = rectangles
        self.offscreen = set(offscreen)
        self.visited = []
        self.released = []

    def _eid(self, element):
        # window_rectangles 的 pending 里是 c_void_p，_relative 返回 int，统一转 int
        v = element.value if isinstance(element, ctypes.c_void_p) else element
        return v if v else 0

    def _relative(self, element, index):
        eid = self._eid(element)
        if index == 4:  # first child
            kids = self.children.get(eid, [])
            return kids[0] if kids else 0
        assert index == 6  # next sibling
        for kids in self.children.values():
            if eid in kids:
                i = kids.index(eid)
                return kids[i + 1] if i + 1 < len(kids) else 0
        return 0

    def _call(self, target, index, argtypes, *args):
        if index == 6:  # ElementFromHandle -> 根节点为 1
            args[1]._obj.value = 1
        elif index == 43:  # get_CurrentBoundingRectangle
            eid = self._eid(target)
            self.visited.append(eid)
            r = self.rectangles[eid]
            obj = args[0]._obj
            obj.left, obj.top = r.x(), r.y()
            obj.right, obj.bottom = r.x() + r.width(), r.y() + r.height()
        elif index == 38:  # get_IsOffscreen
            args[0]._obj.value = 1 if self._eid(target) in self.offscreen else 0
        else:
            raise AssertionError(f"unexpected vtable index {index}")

    def _release(self, element):
        self.released.append(self._eid(element))


def check_bfs_order():
    """广度优先：浅层控件先于深层被收集。"""
    provider = ProviderTree(
        {1: [2, 3], 2: [4]},
        {1: QRect(0, 0, 800, 600), 2: QRect(0, 0, 800, 100),
         3: QRect(0, 100, 800, 500), 4: QRect(10, 10, 120, 32)})
    rects = provider.window_rectangles(12345)
    assert provider.visited == [1, 2, 3, 4], provider.visited
    assert len(rects) == 4
    assert sorted(provider.released) == [1, 2, 3, 4], provider.released
    print("PASS: BFS visits shallow controls before deep ones")


def check_offscreen_filtered():
    """offscreen 节点被过滤，其子树不再遍历。"""
    provider = ProviderTree(
        {1: [2, 3], 2: [4]},
        {1: QRect(0, 0, 800, 600), 2: QRect(0, 0, 800, 100),
         3: QRect(0, 100, 800, 500), 4: QRect(10, 10, 120, 32)},
        offscreen=(2,))
    rects = provider.window_rectangles(12345)
    assert QRect(0, 0, 800, 100) not in rects
    assert QRect(10, 10, 120, 32) not in rects  # 子树被剪掉
    assert len(rects) == 2
    print("PASS: offscreen node and its subtree are pruned")


def check_invalid_rect_filtered():
    """过小矩形被 _valid_rect 过滤，但遍历继续。"""
    provider = ProviderTree(
        {1: [2, 3]},
        {1: QRect(0, 0, 800, 600), 2: QRect(5, 5, 2, 2),
         3: QRect(40, 50, 120, 32)})
    rects = provider.window_rectangles(12345)
    assert rects == [QRect(0, 0, 800, 600), QRect(40, 50, 120, 32)], rects
    print("PASS: too-small rect filtered, traversal continues")


def check_overlapping_kept():
    """重叠的兄弟容器都保留（调用方按面积取最小）。"""
    window = QRect(0, 0, 800, 600)
    button = QRect(40, 50, 120, 32)
    provider = ProviderTree(
        {1: [2, 3], 3: [4]},
        {1: window, 2: window, 3: window, 4: button})
    rects = provider.window_rectangles(12345)
    assert button in rects
    assert rects.count(window) == 3
    print("PASS: overlapping sibling containers are all kept")


if __name__ == "__main__":
    failures = []
    for check in (check_bfs_order, check_offscreen_filtered,
                  check_invalid_rect_filtered, check_overlapping_kept):
        try:
            check()
        except AssertionError as error:
            failures.append(f"FAIL {check.__name__}: {error}")
    for failure in failures:
        print(failure)
    sys.exit(bool(failures))
