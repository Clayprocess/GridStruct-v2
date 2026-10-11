"""纯 provider 树回归：不创建窗口、不调用原生 UIA，也不读取桌面。"""

from pathlib import Path
import sys
import time
import ctypes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRect
from capture.snap import _UIAutomation


class ProviderTree(_UIAutomation):
    def __init__(self, children, rectangles):
        self._ctypes = ctypes
        self.children = children
        self.rectangles = rectangles
        self.visited = []
        self.released = []
        self.siblings = {}
        for nodes in children.values():
            for index, node in enumerate(nodes):
                self.siblings[node] = nodes[index + 1] if index + 1 < len(nodes) else 0

    def _relative(self, element, index):
        if index == 4:
            children = self.children.get(element, [])
            return children[0] if children else 0
        assert index == 6
        return self.siblings[element]

    def _call(self, element, index, argtypes, *args):
        if index == 38:
            args[0]._obj.value = 0
        else:
            assert index == 43
            self.visited.append(element)
            rect = self.rectangles[element]
            result = args[0]._obj
            result.left, result.top = rect.x(), rect.y()
            result.right, result.bottom = rect.x() + rect.width(), rect.y() + rect.height()

    def _release(self, element):
        self.released.append(element)


def check_overlapping_container():
    window = QRect(0, 0, 800, 600)
    button = QRect(40, 50, 120, 32)
    provider = ProviderTree({1: [2, 3], 3: [4]}, {2: window, 3: window, 4: button})
    actual = provider._descend(1, 70, 60, time.monotonic() + 1)
    assert actual == button, f"expected button {button.getRect()}, got {actual}; visited={provider.visited}"
    assert sorted(provider.released) == [2, 3, 4], provider.released
    print("PASS: overlapping sibling container does not hide button")


def check_rectless_container():
    button = QRect(40, 50, 120, 32)
    provider = ProviderTree({1: [2], 2: [3]}, {2: QRect(), 3: button})
    actual = provider._descend(1, 70, 60, time.monotonic() + 1)
    assert actual == button, f"expected child through structural container, got {actual}; visited={provider.visited}"
    assert sorted(provider.released) == [2, 3], provider.released
    print("PASS: structural container without bounds retains child traversal")


def check_nonmatching_branch():
    button = QRect(40, 50, 120, 32)
    provider = ProviderTree(
        {1: [2, 3], 2: [4]},
        {2: QRect(400, 400, 200, 100), 3: button, 4: QRect(420, 420, 100, 30)})
    actual = provider._descend(1, 70, 60, time.monotonic() + 1)
    assert actual == button
    assert provider.visited == [2, 3], provider.visited
    assert sorted(provider.released) == [2, 3], provider.released
    print("PASS: nonmatching subtree is pruned and references are released")


def check_timeout_release():
    provider = ProviderTree({1: [2]}, {2: QRect(0, 0, 800, 600)})
    try:
        provider._descend(1, 70, 60, time.monotonic() - 1)
    except TimeoutError:
        assert provider.released == [2], provider.released
    else:
        raise AssertionError("expired deadline did not stop provider traversal")
    print("PASS: timeout keeps COM-reference release contract")


if __name__ == "__main__":
    failures = []
    for check in (check_overlapping_container, check_rectless_container,
                  check_nonmatching_branch, check_timeout_release):
        try:
            check()
        except AssertionError as error:
            failures.append(f"FAIL {check.__name__}: {error}")
    for failure in failures:
        print(failure)
    sys.exit(bool(failures))
