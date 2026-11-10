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
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRect, QPoint
from PySide6.QtWidgets import QApplication
import capture.snap as snap


class FakeAccessible(snap._Accessibility):
    """objects: {id: (rect, [子对象 id 或 ("item", 子元素编号, rect)])}，1 是窗口对象。
    deepest=True 模拟 Chrome：accHitTest 跳过中间层，直接返回最深的对象；
    也可给 {对象 id: 它命中时直接返回的对象 id}，只让某一层跳层。
    broken: accLocation 会失败（抛 OSError）的对象 id。failing_parents: 这些对象各有一次 get_accParent 失败。
    browsers: 算作浏览器类程序的窗口 hwnd。cold: 前几次在非窗口对象上的 accHitTest 只返回自己（树还没建好）。
    uia: 走 UIA 的窗口 hwnd，配合 provider._uia 注入假树。"""

    def __init__(self, objects, deepest=False, broken=(), browsers=(), cold=0, uia=()):
        self._ctypes = ctypes
        self._iid = (ctypes.c_byte * 16)()
        self._roots = {}
        self._deep = None
        self._walks = {}
        self._names = {}
        self._woken = set()
        self._uia = None
        self.browser_hwnds = set(browsers)
        self.uia_hwnds = set(uia)
        self.cold = cold
        self.name_calls = 0
        self._com_owned = False
        self.objects = objects
        self.deepest = deepest
        self.broken = set(broken)
        self.parents = {c: node for node, (_rect, children) in objects.items()
                        for c in children if not isinstance(c, tuple)}
        self.released = []
        self.acquired = []
        self.calls = 0
        self.parent_calls = 0
        self.failing_parents = []
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

    def _is_browser(self, hwnd):
        return hwnd in self.browser_hwnds

    def _uses_uia(self, hwnd):
        return hwnd in self.uia_hwnds

    @staticmethod
    def _id(pointer):
        return pointer.value if isinstance(pointer, ctypes.c_void_p) else pointer

    def _call(self, pointer, index, argtypes, *args):
        node = self._id(pointer)
        if index == 24:  # accHitTest
            self.calls += 1
            x, y, variant = args[0], args[1], args[2]._obj
            if self.cold and node != 1:
                self.cold -= 1
                variant.vt, variant.value.lVal = snap._Accessibility._VT_I4, 0
                return 0
            for child in self.objects[node][1]:
                if isinstance(child, tuple):
                    if child[2].contains(x, y):
                        variant.vt, variant.value.lVal = snap._Accessibility._VT_I4, child[1]
                        return 0
                elif self.objects[child][0].contains(x, y):
                    if isinstance(self.deepest, dict) and node in self.deepest:
                        child = self.deepest[node]
                    while self.deepest is True and self.objects[child][1]:
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
            self.parent_calls += 1
            if node in self.failing_parents:
                self.failing_parents.remove(node)
                raise OSError(-2147467259, "E_FAIL")
            parent = self.parents.get(node)
            args[0]._obj.value = None if parent is None else 1000 + parent
            if parent is not None:
                self.acquired.append(1000 + parent)
            return 0
        if index == 10:  # get_accName
            self.name_calls += 1
            return 0
        if index == 0:  # QueryInterface
            args[1]._obj.value = node - 1000
            self.acquired.append(node - 1000)
            return 0
        if index == 22:  # accLocation
            if node in self.broken:
                raise OSError(-2147467259, "E_FAIL")  # 真实 _call 把负 HRESULT 转成 OSError
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
    cursor = (0, 0)
    foreground = 0

    @staticmethod
    def IsWindow(_hwnd):
        return True

    IsWindowVisible = IsWindow

    @classmethod
    def GetCursorPos(cls, point):
        point._obj.x, point._obj.y = cls.cursor
        return 1

    @classmethod
    def GetForegroundWindow(cls):
        return cls.foreground

    @staticmethod
    def GetAncestor(hwnd, _flags):
        return hwnd


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


def full_path(provider, x, y):
    """两段合起来：点命中，再补祖先（与工作线程的顺序一致）。"""
    rects = provider.hit_path(1, x, y)
    return provider.ancestors(lambda: False) or rects


def check_hit_path():
    provider = FakeAccessible(tree())
    assert full_path(provider, 115, 18) == [TOOLBAR, TAB, CLOSE]
    assert full_path(provider, 400, 300) == [BODY]
    assert provider.opened == 1, "同一窗口的 IAccessible 应只取一次"
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    full = [BODY, TABLE, ROW, CELL, TEXT]
    provider = FakeAccessible(page(), deepest=True)
    assert provider.hit_path(1, 220, 155) == [TEXT], "第一段只做点命中，不等补祖先"
    assert provider.ancestors(lambda: False) == full
    parents = provider.parent_calls
    assert full_path(provider, 220, 155) == full
    assert provider.parent_calls == parents, "同一元素补过祖先后应直接用缓存"
    assert provider.hit_path(1, 220, 155) == full, "补过父链的元素第一段就给完整路径，静止刷新不退回短路径"
    provider.hit_path(1, 285, 175)
    assert provider.ancestors(lambda: True) is None, "有新请求时应放弃补祖先"
    assert full_path(provider, 50, 400) == [BODY]
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    # 只有窗口一层跳到单元格，单元格往下正常逐层命中：单元格之上的行、表格也要补回。
    provider = FakeAccessible(page(), deepest={1: 8})
    assert provider.hit_path(1, 220, 155) == [CELL, TEXT]
    assert provider.ancestors(lambda: False) == full
    # 取父对象偶发失败：这次先用已有路径，不缓存，下次重试能补全。
    provider = FakeAccessible(page(), deepest=True)
    provider.failing_parents = [9]
    assert full_path(provider, 220, 155) == [TEXT]
    assert full_path(provider, 220, 155) == full, "补链失败后应重试"
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    # 单元格和文字之间有一层同样大小的包装：不能因为矩形重复就停止向上。
    wrapped = page()
    wrapped[8], wrapped[10] = (CELL, [10]), (TEXT, [9])
    provider = FakeAccessible(wrapped, deepest=True)
    assert full_path(provider, 220, 155) == full
    provider.hit_path(1, 220, 155)  # 留一个未补的对象，close 也要释放
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    # 最深对象取位置失败：保留已有路径，也不能漏放它的引用。
    provider = FakeAccessible(page(), deepest=True, broken={9})
    for _ in range(20):
        full_path(provider, 220, 155)
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    # 浏览器类程序第一次命中只给大容器（树还没建好）：同一点再查一次，取更小的。
    provider = FakeAccessible(page(), browsers={1}, cold=1)
    assert provider.hit_path(1, 220, 155) == [BODY, TABLE, ROW, CELL, TEXT]
    provider = FakeAccessible(page(), cold=1)
    assert provider.hit_path(1, 220, 155) == [BODY], "非浏览器不做二次命中"
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    provider = FakeAccessible(page(), browsers={1})
    calls = provider.calls
    assert provider.hit_path(1, 220, 155) == [BODY, TABLE, ROW, CELL, TEXT]
    assert provider.calls == calls + 12, "浏览器每次命中查两遍"
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    # 预热：建好窗口对象；浏览器再取一次客户区名字唤醒无障碍树。
    provider = FakeAccessible(page())
    provider.warm(1, None)
    assert provider.opened == 1 and provider.name_calls == 0
    provider = FakeAccessible(page(), browsers={1})
    provider.warm(1, None)
    assert provider.opened == 2 and provider.name_calls == 1, (provider.opened, provider.name_calls)
    provider.hit_path(1, 220, 155)
    assert provider.opened == 2, "预热过的窗口对象应复用"
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    print("PASS: hit_path descends window → toolbar → tab → close button; ancestors() recovers skipped table/row, "
          "caches the walk, stops on a new request, releases every reference")


def check_two_phase():
    """浏览器式跳层：先发点命中结果出框，停住后再发补全路径，滚轮能切到整张表格。"""
    app = QApplication.instance() or QApplication([])
    provider = FakeAccessible(page(), deepest=True)
    original = (snap._IS_WIN, snap._try_init_msaa, snap._win32_api, snap._snapshot_windows)
    snap._IS_WIN, snap._win32_api, snap._try_init_msaa = True, FakeUser32, lambda: provider
    snap._snapshot_windows = lambda _ignored: [(7, WINDOW)]
    detectors = []

    def start():
        detector = snap.RegionDetector(only_window=False)
        detectors.append(detector)
        results = []
        detector.regionDetected.connect(results.append)
        return detector, results

    try:
        detector, results = start()
        detector.detect(QPoint(220, 155))
        settle(app, detector)
        assert results[-1] == TEXT, results
        for expected in (CELL, ROW, TABLE, BODY):
            detector.step_level(1)
            assert results[-1] == expected, (expected, results)
        assert (7, WINDOW.getRect()) not in detector._slow
        print("PASS: two-phase query shows the hit first, then the wheel reaches the whole table")
        # 补链还没回来时滚轮已切到窗口：补全后仍选中窗口，不能跳回单元格。
        provider = FakeAccessible(page(), deepest=True)
        gate = threading.Event()
        walk = provider.ancestors
        provider.ancestors = lambda interrupted: (gate.wait(3), walk(interrupted))[1]
        detector, results = start()
        detector.detect(QPoint(220, 155))
        deadline = time.monotonic() + 3
        while results[-1] != TEXT and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert results[-1] == TEXT, results
        detector.step_level(1)
        assert results[-1] == WINDOW, results
        gate.set()
        settle(app, detector)
        assert detector._chain[-1] == WINDOW and len(detector._chain) == 6, detector._chain
        assert results[-1] == WINDOW, ("补链完成不应改变已选层级", results)
        detector.step_level(-1)
        assert results[-1] == BODY, results
        detector.shutdown()
        # 补链中途失败，滚轮选中单元格；静止到期自动重试时仍选中单元格，不能先退成整窗。
        provider = FakeAccessible(page(), deepest=True)
        provider.failing_parents = [8]
        detector, results = start()
        detector.detect(QPoint(220, 155))
        settle(app, detector)
        assert detector._chain == [TEXT, CELL, WINDOW], detector._chain
        detector.step_level(1)
        assert results[-1] == CELL, results
        count = len(results)
        deadline = time.monotonic() + snap._HIT_REUSE_S + 1
        while len(detector._chain) < 6 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        settle(app, detector)
        assert len(detector._chain) == 6, detector._chain
        assert set(results[count:]) <= {CELL}, ("自动重试不应改变已选层级", results[count:])
        assert results[-1] == CELL, results
        print("PASS: completing the chain keeps the level the wheel already selected, also across a retried walk")
    finally:
        for detector in detectors:
            detector.shutdown()
        snap._IS_WIN, snap._try_init_msaa, snap._win32_api, snap._snapshot_windows = original


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
        one_off = False

        def hit_path(self, hwnd, x, y):
            seen.append(hwnd)
            if hwnd == 7:
                release.wait(5)
            return [TOOLBAR] if hwnd == 7 else []

        def ancestors(self, _interrupted):
            return None

        def warm(self, _hwnd, _cursor):
            pass

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
        assert snap._hung(7), "卡住超过阈值的窗口应改用窗口吸附"
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
        snap._hung_hwnds.pop(7, None)
        snap._IS_WIN, snap._try_init_msaa, snap._win32_api, snap._snapshot_windows, snap._HUNG_S = original


def check_stationary_refinement():
    """首查只得到外层，之后不再发鼠标事件，也应按缓存时限查询到内层。"""
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    calls = []

    class LazyProvider:
        one_off = False

        def hit_path(self, *_args):
            calls.append(time.monotonic())
            return [TOOLBAR] if len(calls) == 1 else [TOOLBAR, TAB, CLOSE]

        def ancestors(self, _interrupted):
            return None

        def warm(self, _hwnd, _cursor):
            pass

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
        one_off = False

        def hit_path(self, *_args):
            entered.set()
            release.wait(3)
            return [TOOLBAR]

        def ancestors(self, _interrupted):
            return None

        def warm(self, _hwnd, _cursor):
            pass

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
            assert not snap._hung(70), "已完成的查询不能被判为卡死"
            settle(app, detector)
        finally:
            release.set()
            detector.shutdown()
    print("PASS: query completion during timeout checking neither crashes nor blacklists the window")


class FakeUiaTree(snap._UiaTree):
    """tree: {节点: [(子节点, 矩形)]}，记录每个节点取子元素的次数。"""

    def __init__(self, tree):
        self.tree, self.fetched, self.closed, self.big_fetch = tree, [], False, False
        self._children = {}

    def _root(self, _hwnd):
        return SimpleNamespace(value="window")

    def _kids(self, node):
        if node.value not in self._children:
            self.fetched.append(node.value)
            self._children[node.value] = [(SimpleNamespace(value=name), rect) for name, rect in self.tree.get(node.value, [])]
            self.big_fetch |= len(self._children[node.value]) >= snap._BIG_FETCH
        return self._children[node.value]

    def close(self):
        self.closed = True


def check_uia():
    """Excel/Word/Zoom 走 UIA：逐层取含鼠标的最小子元素并缓存；UIA 取不到时退回 MSAA。"""
    grid = QRect(0, 100, 800, 500)
    cells = [(f"cell{r}{c}", QRect(c * 72, 100 + r * 19, 72, 19)) for r in range(20) for c in range(11)]
    tree = {"window": [("ribbon", QRect(0, 0, 800, 100)), ("desk", QRect(0, 100, 800, 500))],
            "desk": [("grid", grid), ("scrollbar", QRect(780, 100, 20, 500))],
            "grid": cells}
    uia = FakeUiaTree(tree)
    cell = QRect(72, 119, 72, 19)
    assert uia.path(1, 100, 125) == [QRect(0, 100, 800, 500), grid, cell], uia.path(1, 100, 125)
    assert uia.path(1, 790, 300) == [QRect(0, 100, 800, 500), QRect(780, 100, 20, 500)], "重叠时取最小的"
    assert uia.path(1, 10, 10) == [QRect(0, 0, 800, 100)]
    assert uia.fetched == ["window", "desk", "grid", "cell11", "scrollbar", "ribbon"], uia.fetched
    provider = FakeAccessible(page(), uia={1})
    provider._uia = uia
    assert provider.hit_path(1, 100, 125)[-1] == cell and provider.calls == 0, "走 UIA 时不做 MSAA 点命中"
    assert not provider.one_off, "已缓存的查询照常计入慢窗口判断"
    uia.big_fetch = False
    uia._children.pop("grid")
    provider.hit_path(1, 100, 125)
    assert provider.one_off, "一次取回 220 个单元格算一次性开销"
    # 截图开始预热：沿鼠标位置和窗口中心取好子元素，第一次移进表格区不再有大批量等待。
    provider = FakeAccessible(page(), uia={1})
    provider._uia = FakeUiaTree(tree)
    provider.warm(1, (10, 10))
    assert {"window", "desk", "grid"} <= set(provider._uia.fetched), provider._uia.fetched
    provider.hit_path(1, 100, 125)
    assert not provider.one_off, "预热后首次进入表格区不应再大批量取子元素"
    provider._uia = FakeUiaTree({})
    assert provider.hit_path(1, 220, 155) == [BODY, TABLE, ROW, CELL, TEXT], "UIA 没有结果时退回 MSAA"
    provider.close()
    assert provider._uia.closed
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    # 按进程名/窗口类分流：名单外的 Electron（Qoder）当浏览器；任务栏走 UIA；普通资源管理器窗口不变。
    routing = snap._Accessibility.__new__(snap._Accessibility)
    routing._names = {1: "qoder.exe", 2: "explorer.exe", 3: "explorer.exe", 4: "excel.exe"}
    routing._classes = {1: "Chrome_WidgetWin_1", 2: "Shell_TrayWnd", 3: "CabinetWClass", 4: "XLMAIN"}
    assert routing._is_browser(1) and not routing._uses_uia(1)
    assert routing._uses_uia(2) and not routing._is_browser(2)
    assert not routing._uses_uia(3) and not routing._is_browser(3)
    assert routing._uses_uia(4)
    # 截图开始时没预热到的浏览器类窗口：第一次命中时唤醒一次。
    provider = FakeAccessible(page(), browsers={1})
    provider.hit_path(1, 220, 155)
    provider.hit_path(1, 220, 155)
    assert provider.name_calls == 1, provider.name_calls
    provider.close()
    assert sorted(provider.released) == sorted(provider.acquired), (provider.released, provider.acquired)
    print("PASS: UIA path descends to the smallest element containing the point, caches children, falls back to MSAA; "
          "taskbar routes to UIA, unlisted Electron windows are woken and treated as browsers")


def check_warm():
    """截图开始就在工作线程里预热鼠标下和前台窗口的无障碍对象；只吸附窗口时不开线程。"""
    app = QApplication.instance() or QApplication([])
    other = QRect(900, 0, 100, 100)
    windows = [(7, WINDOW), (8, other)]
    original = (FakeUser32.cursor, FakeUser32.foreground)
    try:
        FakeUser32.cursor, FakeUser32.foreground = (400, 300), 0
        assert snap._warm_targets(FakeUser32, windows) == [(7, (400, 300))]
        FakeUser32.foreground = 8
        assert snap._warm_targets(FakeUser32, windows) == [(7, (400, 300)), (8, (400, 300))]
        FakeUser32.foreground = 99  # 自身遮罩等不在快照里的窗口不预热
        assert snap._warm_targets(FakeUser32, windows) == [(7, (400, 300))]
        FakeUser32.foreground = 0
        provider = FakeAccessible(tree())
        with patch.object(snap, "_IS_WIN", True), patch.object(snap, "_win32_api", FakeUser32), \
                patch.object(snap, "_try_init_msaa", lambda: provider), \
                patch.object(snap, "_snapshot_windows", lambda _ignored: windows):
            detector = snap.RegionDetector(only_window=True)
            assert detector._worker is None
            detector.shutdown()
            detector = snap.RegionDetector(only_window=False)
            try:
                deadline = time.monotonic() + 3
                while provider.opened == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert provider.opened == 1 and provider.calls == 0, "应在首次悬停前建好窗口对象"
                detector.detect(QPoint(115, 18))
                settle(app, detector)
                assert provider.opened == 1, "悬停复用预热的对象"
            finally:
                detector.shutdown()
    finally:
        FakeUser32.cursor, FakeUser32.foreground = original
    print("PASS: capture start prewarms the window under the cursor and the foreground window")


if __name__ == "__main__":
    failures = []
    for check in (check_hit_path, check_two_phase, check_detector, check_hung_query, check_stationary_refinement,
                  check_completion_during_timeout_check, check_warm, check_uia):
        try:
            check()
        except AssertionError as error:
            failures.append(f"FAIL {check.__name__}: {error}")
    for failure in failures:
        print(failure)
    sys.exit(bool(failures))
