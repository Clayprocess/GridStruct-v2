"""点命中吸附回归：不调用原生 MSAA，也不读取桌面。

1. _Accessibility.hit_path 逐层 accHitTest 的路径、简单子元素、越界停止、引用释放，
   窗口对象同一会话只取一次；浏览器式 accHitTest 直接返回最深元素时，沿 accParent
   补回被跳过的表格、行；
2. RegionDetector 用常驻工作线程查询、信号回传：命中矩形内移动不再查询，
   换控件时保留旧框直到新结果到达（不闪回外层），滚轮能切到父层，慢窗口降级；
3. 静止悬停仍能细化懒加载控件；查询卡死时丢弃线程、拉黑窗口，新线程继续服务，丢弃迟到结果；
4. 查询完成与 UI 超时检查交错时不崩溃、不误判卡死。
用法：QT_QPA_PLATFORM=offscreen python tools/check_snap_hit.py
"""

from pathlib import Path
import ctypes
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRect, QPoint
from PySide6.QtWidgets import QApplication
import capture.snap as snap


class FakeAccessible(snap._Accessibility):
    """objects: {id: (rect, [子对象 id 或 ("item", 子元素编号, rect)])}，1 是窗口对象。
    deepest=True 模拟 Chrome：accHitTest 跳过中间层，直接返回最深的对象。"""

    def __init__(self, objects, deepest=False):
        self._ctypes = ctypes
        self._iid = (ctypes.c_byte * 16)()
        self._roots = {}
        self._com_owned = False
        self.objects = objects
        self.deepest = deepest
        self.parents = {c: node for node, (_rect, children) in objects.items()
                        for c in children if not isinstance(c, tuple)}
        self.released = []
        self.acquired = []
        self.calls = 0
        self.opened = 0
        fake = self

        class User32:
            @staticmethod
            def SendMessageTimeoutW(*_args):
                return 1

        class OleAcc:
            @staticmethod
            def AccessibleObjectFromWindow(_hwnd, _id, _iid, out):
                fake.opened += 1
                fake.acquired.append(1)
                out._obj.value = 1
                return 0

        self._user32, self._oleacc = User32(), OleAcc()

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
                    while self.deepest and self.objects[child][1]:
                        inner = next((c for c in self.objects[child][1] if not isinstance(c, tuple)
                                      and self.objects[c][0].contains(x, y)), None)
                        if inner is None:
                            break
                        child = inner
                    # 返回 IDispatch：用 1000+id 区分，QueryInterface 后换回真正的对象 id。
                    variant.vt, variant.value.pointer = snap._Accessibility._VT_DISPATCH, 1000 + child
                    self.acquired.append(1000 + child)
                    return 0
            variant.vt, variant.value.lVal = snap._Accessibility._VT_I4, 0
            return 0
        if index == 7:  # get_accParent
            parent = self.parents.get(node)
            args[0]._obj.value = None if parent is None else 1000 + parent
            if parent is not None:
                self.acquired.append(1000 + parent)
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
CLOSE = QRect(110, 12, 14, 14)  # 标签页关闭按钮这类小控件
NEXT_TAB = QRect(140, 4, 120, 32)
BODY = QRect(0, 40, 800, 560)


def tree():
    return {1: (WINDOW, [2, 5]), 2: (TOOLBAR, [3, 4]), 3: (TAB, [("item", 1, CLOSE)]),
            4: (NEXT_TAB, []), 5: (BODY, [])}


TABLE = QRect(100, 100, 400, 200)
ROW = QRect(100, 140, 400, 40)
CELL = QRect(200, 140, 100, 40)
TEXT = QRect(210, 150, 60, 20)


def page():
    """网页：窗口 → 正文 → 表格 → 行 → 单元格 → 文字。"""
    return {1: (WINDOW, [5]), 5: (BODY, [6]), 6: (TABLE, [7]), 7: (ROW, [8]), 8: (CELL, [9]), 9: (TEXT, [])}


class FakeUser32:
    @staticmethod
    def IsWindow(_hwnd):
        return True

    IsWindowVisible = IsWindow


def settle(app, detector):
    """等工作线程空闲、信号送达。"""
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.processEvents()
        worker = detector._worker
        if worker is None or (worker._pending is None and worker.busy is None):
            time.sleep(0.02)
            app.processEvents()
            return


def check_hit_path():
    provider = FakeAccessible(tree())
    assert provider.hit_path(1, 115, 18) == [TOOLBAR, TAB, CLOSE]
    assert provider.hit_path(1, 400, 300) == [BODY]
    assert provider.opened == 1, "同一窗口的 IAccessible 应只取一次"
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    provider = FakeAccessible(page(), deepest=True)
    assert provider.hit_path(1, 220, 155) == [BODY, TABLE, ROW, CELL, TEXT]
    assert provider.hit_path(1, 50, 400) == [BODY]
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    print("PASS: hit_path descends window → toolbar → tab → close button, recovers skipped table/row via accParent, "
          "reuses the window object, releases every reference")


def check_detector():
    app = QApplication.instance() or QApplication([])
    from capture import ScreenManager
    manager = ScreenManager.instance()
    provider = FakeAccessible(tree())
    original = (snap._IS_WIN, snap._try_init_msaa, snap._win32_api, snap._snapshot_windows)
    snap._IS_WIN, snap._win32_api, snap._try_init_msaa = True, FakeUser32, lambda: provider
    snap._snapshot_windows = lambda _ignored: [(7, WINDOW)]
    detector = snap.RegionDetector(only_window=False)
    # 离屏平台单屏、原点 (0,0)、DPR 1：逻辑坐标即物理坐标。
    assert manager.logical_to_physical(QPoint(115, 18), manager.screen_at(QPoint(0, 0))) == QPoint(115, 18)
    results = []
    detector.regionDetected.connect(results.append)
    try:
        detector.detect(QPoint(115, 18))
        assert results == [WINDOW], "刚进入窗口先框整个窗口"
        settle(app, detector)
        assert results[-1] == CLOSE, results
        calls = provider.calls
        detector.detect(QPoint(117, 20))
        settle(app, detector)
        assert provider.calls == calls, "命中矩形内移动不应再查询"
        detector.step_level(1)
        assert results[-1] == TAB, results
        detector.step_level(1)
        assert results[-1] == TOOLBAR, results
        detector.step_level(-2)
        assert results[-1] == CLOSE, results
        count = len(results)
        detector.detect(QPoint(200, 18))
        assert len(results) == count, "等结果时应保留旧框，不先退回外层"
        settle(app, detector)
        assert results[count:] == [NEXT_TAB], results[count:]
        detector.detect(QPoint(400, 300))
        settle(app, detector)
        assert results[-1] == BODY, results
        assert WINDOW not in results[1:], "换控件时不应闪回整个窗口"
        key = (7, WINDOW.getRect())
        detector._hits.clear()
        original_hit = provider.hit_path
        provider.hit_path = lambda *a: (time.sleep(0.35), original_hit(*a))[1]
        detector.detect(QPoint(115, 18))
        settle(app, detector)
        assert key in detector._slow, "超过 300ms 的窗口应降级"
        assert results[-1] == WINDOW, "降级时应立即退回窗口框，不沿用旧控件"
        calls = provider.calls
        detector._hits.clear()
        detector.detect(QPoint(400, 300))
        settle(app, detector)
        assert provider.calls == calls, "降级后的窗口不再点命中"
        assert results[-1] == WINDOW, results
        print("PASS: detector keeps the old box while waiting, skips repeat queries, steps to parents, demotes slow windows")
    finally:
        detector.shutdown()
        snap._IS_WIN, snap._try_init_msaa, snap._win32_api, snap._snapshot_windows = original


def check_hung_query():
    """点命中卡死：丢弃那条线程并拉黑窗口，新线程服务别的窗口，迟到结果被丢弃。"""
    import threading
    app = QApplication.instance() or QApplication([])
    release = threading.Event()
    other = QRect(0, 620, 400, 150)  # 离屏屏幕 800×800，放在 WINDOW 下方
    seen = []

    class Hanging:
        def hit_path(self, hwnd, x, y):
            seen.append(hwnd)
            if hwnd == 7:
                release.wait(5)
            return [TOOLBAR] if hwnd == 7 else []

        def close(self):
            pass

    original = (snap._IS_WIN, snap._try_init_msaa, snap._win32_api, snap._snapshot_windows, snap._HUNG_S)
    snap._IS_WIN, snap._win32_api, snap._HUNG_S = True, FakeUser32, 0.3
    snap._try_init_msaa = Hanging
    snap._snapshot_windows = lambda _ignored: [(7, WINDOW), (8, other)]
    detector = snap.RegionDetector(only_window=False)
    results = []
    detector.regionDetected.connect(results.append)
    try:
        detector.detect(QPoint(115, 18))
        deadline = time.monotonic() + 1
        while 7 not in seen and time.monotonic() < deadline:
            app.processEvents()
        key = (7, WINDOW.getRect())
        detector._hits[key] = (TAB, [TOOLBAR, TAB], time.monotonic() - snap._HIT_REUSE_S)
        detector._show(key, 115, 18)
        time.sleep(0.35)
        hung = detector._worker
        app.processEvents()
        assert 7 in snap._hung_hwnds, "卡住超过阈值的窗口应改用窗口吸附"
        assert detector._worker is None and snap._abandoned == 1
        assert results[-1] == WINDOW
        detector.detect(QPoint(100, 650))
        deadline = time.monotonic() + 2
        while 8 not in seen and time.monotonic() < deadline:
            app.processEvents()
        assert 8 in seen and detector._worker is not hung, "卡死线程不应占住后续查询"
        release.set()
        deadline = time.monotonic() + 2
        while snap._abandoned and time.monotonic() < deadline:
            app.processEvents()
        assert snap._abandoned == 0, "卡死线程返回后应归还计数"
        app.processEvents()
        assert TOOLBAR not in results, "被丢弃线程的迟到结果不应发布"
        print("PASS: hung point query abandons its thread, demotes its window, serves other windows and drops the late result")
    finally:
        release.set()
        detector.shutdown()
        snap._hung_hwnds.discard(7)
        snap._IS_WIN, snap._try_init_msaa, snap._win32_api, snap._snapshot_windows, snap._HUNG_S = original


def check_stationary_refinement():
    """首查只得到外层，之后不再发鼠标事件，也应按缓存时限查询到内层。"""
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    calls = []

    class LazyProvider:
        def hit_path(self, *_args):
            calls.append(time.monotonic())
            return [TOOLBAR] if len(calls) == 1 else [TOOLBAR, TAB, CLOSE]

        def close(self):
            pass

    with patch.object(snap, "_IS_WIN", True), \
            patch.object(snap, "_win32_api", FakeUser32), \
            patch.object(snap, "_snapshot_windows", return_value=[(7, WINDOW)]), \
            patch.object(snap, "_try_init_msaa", LazyProvider):
        detector = snap.RegionDetector(only_window=False)
        results = []
        detector.regionDetected.connect(results.append)
        try:
            detector.detect(QPoint(115, 18))
            settle(app, detector)
            assert results[-1] == TOOLBAR
            QTest.qWait(250)
            assert len(calls) == 1, "缓存未到期不应重复查询"
            QTest.qWait(1200)
            settle(app, detector)
            assert len(calls) >= 2 and results[-1] == CLOSE, \
                f"静止悬停未细化：queries={len(calls)}, results={results}"
            detector.shutdown()
            count = len(calls)
            QTest.qWait(1200)
            assert len(calls) == count, "退出后不能继续定时查询"
        finally:
            detector.shutdown()
    print("PASS: stationary hover refines a lazy accessibility tree and stops refreshing on shutdown")


def check_completion_during_timeout_check():
    """精确安排查询在 UI 读取超时时钟时结束，复现 busy 被清空的竞态。"""
    import threading
    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()

    class Provider:
        def hit_path(self, *_args):
            entered.set()
            release.wait(3)
            return [TOOLBAR]

        def close(self):
            pass

    with patch.object(snap, "_try_init_msaa", Provider):
        detector = snap.RegionDetector(only_window=False)
        key = (70, WINDOW.getRect())
        detector._request(key, 70, 115, 18)
        worker = detector._worker
        assert entered.wait(1)
        clock = time.monotonic

        def complete():
            release.set()
            deadline = clock() + 1
            while worker.busy is not None and clock() < deadline:
                time.sleep(0.001)
            assert worker.busy is None, "后台查询没有按预期完成"
            return clock()

        try:
            with patch.object(snap.time, "monotonic", side_effect=complete):
                detector._request(key, 70, 116, 18)
            assert 70 not in snap._hung_hwnds, "已完成的查询不能被判为卡死"
            settle(app, detector)
        finally:
            release.set()
            detector.shutdown()
    print("PASS: query completion during timeout checking neither crashes nor blacklists the window")


if __name__ == "__main__":
    failures = []
    for check in (check_hit_path, check_detector, check_hung_query, check_stationary_refinement,
                  check_completion_during_timeout_check):
        try:
            check()
        except AssertionError as error:
            failures.append(f"FAIL {check.__name__}: {error}")
    for failure in failures:
        print(failure)
    sys.exit(bool(failures))
