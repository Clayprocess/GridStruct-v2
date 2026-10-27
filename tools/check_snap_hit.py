"""点命中吸附回归：不调用原生 MSAA，也不读取桌面。

1. _Accessibility.hit_path 逐层 accHitTest 的路径、简单子元素、越界停止、引用释放；
2. RegionDetector 把整窗遍历缓存和点命中路径合并成由内到外的层级链，
   命中矩形内移动不再查询，慢窗口降级，滚轮能切到点命中补出的父层。
用法：QT_QPA_PLATFORM=offscreen python tools/check_snap_hit.py
"""

from pathlib import Path
import ctypes
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRect, QPoint
from PySide6.QtWidgets import QApplication
import capture.snap as snap


class FakeAccessible(snap._Accessibility):
    """objects: {id: (rect, [子对象 id 或 ("item", 子元素编号, rect)])}，1 是窗口对象。"""

    def __init__(self, objects):
        self._ctypes = ctypes
        self._iid = (ctypes.c_byte * 16)()
        self.objects = objects
        self.released = []
        self.acquired = [1]
        self.calls = 0

        class User32:
            @staticmethod
            def SendMessageTimeoutW(*_args):
                return 1

        class OleAcc:
            @staticmethod
            def AccessibleObjectFromWindow(_hwnd, _id, _iid, out):
                out._obj.value = 1
                return 0

        self._user32, self._oleacc = User32(), OleAcc()

    def close(self):
        pass

    @staticmethod
    def _id(pointer):
        return pointer.value if isinstance(pointer, ctypes.c_void_p) else pointer

    def _call(self, pointer, index, argtypes, *args):
        node = self._id(pointer)
        if index == 24:  # accHitTest
            self.calls += 1
            x, y, variant = args[0], args[1], args[2]._obj
            for child in self.objects[node][1]:
                if isinstance(child, tuple):
                    if child[2].contains(x, y):
                        variant.vt, variant.value.lVal = snap._Accessibility._VT_I4, child[1]
                        return 0
                elif self.objects[child][0].contains(x, y):
                    # 返回 IDispatch：用 1000+id 区分，QueryInterface 后换回真正的对象 id。
                    variant.vt, variant.value.pointer = snap._Accessibility._VT_DISPATCH, 1000 + child
                    self.acquired.append(1000 + child)
                    return 0
            variant.vt, variant.value.lVal = snap._Accessibility._VT_I4, 0
            return 0
        if index == 0:  # QueryInterface
            args[1]._obj.value = node - 1000
            self.acquired.append(node - 1000)
            return 0
        if index == 22:  # accLocation
            child = args[4].value.lVal
            rect = self.objects[node][0] if child == 0 else next(
                c[2] for c in self.objects[node][1] if isinstance(c, tuple) and c[1] == child)
            for slot, value in zip(args[:4], (rect.x(), rect.y(), rect.width(), rect.height())):
                slot._obj.value = value
            return 0
        if index == 2:
            self.released.append(node)
            return 0
        raise AssertionError(f"unexpected vtable index {index}")


WINDOW = QRect(0, 0, 800, 600)
TOOLBAR = QRect(0, 0, 800, 40)
TAB = QRect(10, 4, 120, 32)
CLOSE = QRect(110, 12, 14, 14)  # 标签页关闭按钮：BFS 预算内常常到不了这一层
BODY = QRect(0, 40, 800, 560)


def tree():
    return {1: (WINDOW, [2, 5]), 2: (TOOLBAR, [3]), 3: (TAB, [("item", 1, CLOSE)]), 5: (BODY, [])}


def check_hit_path():
    provider = FakeAccessible(tree())
    assert provider.hit_path(1, 115, 18) == [TOOLBAR, TAB, CLOSE]
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    provider = FakeAccessible(tree())
    assert provider.hit_path(1, 400, 300) == [BODY]
    print("PASS: hit_path descends window → toolbar → tab → close button and releases every reference")


def check_detector():
    app = QApplication.instance() or QApplication([])
    from capture.screen import ScreenManager
    manager = ScreenManager.instance()
    provider = FakeAccessible(tree())

    class FakeUIA:
        def window_rectangles(self, _hwnd):
            return [WINDOW, TOOLBAR, TAB]  # 遍历只到标签页这一层

        def close(self):
            pass

    class FakeUser32:
        @staticmethod
        def IsWindow(_hwnd):
            return True

        IsWindowVisible = IsWindow

    original = (snap._IS_WIN, snap._try_init_uia, snap._try_init_msaa,
                snap._win32_api, snap._snapshot_windows)
    snap._try_init_uia, snap._try_init_msaa = FakeUIA, lambda: provider
    # 走 Windows 构造路径：构造时就会刷新一次窗口快照。
    snap._IS_WIN, snap._win32_api = True, FakeUser32
    snap._snapshot_windows = lambda _ignored: [(7, WINDOW)]
    detector = snap.RegionDetector(only_window=False)
    detector._snapshot_timer.stop()
    # 离屏平台单屏、原点 (0,0)、DPR 1：逻辑坐标即物理坐标。
    assert manager.logical_to_physical(QPoint(115, 18), manager.screen_at(QPoint(0, 0))) == QPoint(115, 18)
    results = []
    detector.regionDetected.connect(results.append)

    def settle():
        deadline = time.monotonic() + 3
        while (detector._inflight or detector._point_inflight) and time.monotonic() < deadline:
            app.processEvents()
        app.processEvents()

    try:
        detector.detect(QPoint(115, 18))
        settle()
        assert results[-1] == CLOSE, results
        calls = provider.calls
        detector.detect(QPoint(117, 20))
        settle()
        assert provider.calls == calls, "命中矩形内移动不应再查询"
        detector.step_level(1)
        assert results[-1] == TAB, results
        detector.step_level(1)
        assert results[-1] == TOOLBAR, results
        detector.detect(QPoint(400, 300))
        settle()
        assert results[-1] == BODY, results
        key = (7, WINDOW.getRect())
        detector._hits.clear()
        original_hit = provider.hit_path
        provider.hit_path = lambda *a: (time.sleep(0.35), original_hit(*a))[1]
        detector.detect(QPoint(115, 18))
        settle()
        assert key in detector._slow, "超过 300ms 的窗口应降级"
        calls = provider.calls
        detector._hits.clear()
        detector.detect(QPoint(400, 300))
        settle()
        assert provider.calls == calls, "降级后的窗口不再点命中"
        print("PASS: detector merges BFS cache with hit path, skips repeat queries, steps to parents, demotes slow windows")
    finally:
        detector.shutdown()
        (snap._IS_WIN, snap._try_init_uia, snap._try_init_msaa,
         snap._win32_api, snap._snapshot_windows) = original


if __name__ == "__main__":
    failures = []
    for check in (check_hit_path, check_detector):
        try:
            check()
        except AssertionError as error:
            failures.append(f"FAIL {check.__name__}: {error}")
    for failure in failures:
        print(failure)
    sys.exit(bool(failures))
