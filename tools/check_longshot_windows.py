"""真实 Windows 长截图验收；只采集本进程合成窗口，不写入用户桌面图像。"""
import ctypes
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QPainter, QTransform
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
import numpy as np

from capture import DesktopCapture, LongShotFlow, ScreenFrame, ScreenManager
import capture.longshot as longshot
from capture.longshot import _check_scroll_target, _focus_scroll_target, _send_scroll
from styles import apply_theme, tokens as T


class ScrollPage(QWidget):
    """以原生像素移动页面，避免分数 DPR 下重绘字体产生不同抗锯齿。"""

    def __init__(self, app, horizontal=False):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("GridStruct synthetic longshot acceptance")
        self.setGeometry(app.primaryScreen().availableGeometry().x() + 80,
                         app.primaryScreen().availableGeometry().y() + 100, 824, 624)
        self.horizontal = horizontal
        self.offset = 0
        self.next_offset = None
        self.step = 137
        self.wheels = []
        self.smooth_steps = 0
        self.dpr = app.primaryScreen().devicePixelRatio()
        self.region_local = QRect(12, 12, 800, 600)
        self.pixel_width = round(800 * self.dpr)
        self.pixel_height = round(600 * self.dpr)
        width = self.pixel_height if horizontal else self.pixel_width
        self.page = QImage(width, 7200, QImage.Format_RGB32)
        self.page.fill(QColor(T.LIGHT["background"]))
        with QPainter(self.page) as painter:
            painter.setFont(QFont("Arial", 14))
            for row, y in enumerate(range(0, 7200, 25)):
                painter.setPen(QColor(T.LIGHT["foreground"]))
                painter.drawText(12, y + 19, f"ROW {row:04d}  order {row * 7919:09d}  amount {row * 13.17:.2f}")
                painter.fillRect(width // 2 + (row * 17) % 70, y + 4,
                                 25 + (row * 29) % 130, 15, QColor(T.ANNOTATION_COLORS[row % 5]))
                # 每行独立指纹使 48px 重叠有唯一证据，同时仍保留常见白底表格结构。
                for bit in range(10):
                    if row & (1 << bit):
                        painter.fillRect(width - 140 + bit * 12, y + 3, 7, 17,
                                         QColor(T.LIGHT["foreground"]))
        if horizontal:
            self.page = self.page.transformed(QTransform().rotate(-90))
        self.maximum = (self.page.width() - self.pixel_width if horizontal
                        else self.page.height() - self.pixel_height)
        self.limit = self.maximum

    @property
    def region(self):
        return QRect(self.mapToGlobal(self.region_local.topLeft()), self.region_local.size())

    @property
    def span(self):
        return self.pixel_width if self.horizontal else self.pixel_height

    def expected(self, end):
        return self.page.copy(0, 0, end if self.horizontal else self.pixel_width,
                              self.pixel_height if self.horizontal else end)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.fillRect(self.rect(), QColor(T.LIGHT["background"]))
            painter.setClipRect(self.region_local)
            painter.scale(1 / self.dpr, 1 / self.dpr)
            x = round(self.region_local.x() * self.dpr) - (self.offset if self.horizontal else 0)
            y = round(self.region_local.y() * self.dpr) - (0 if self.horizontal else self.offset)
            painter.drawImage(x, y, self.page)

    def set_offset(self, offset):
        self.offset = min(self.limit, max(0, offset))
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().x() if self.horizontal else event.angleDelta().y()
        self.wheels.append(delta)
        # Qt 将 WM_MOUSEHWHEEL 的原生正向转换成负 angleDelta.x()。
        forward = delta < 0
        destination = (self.next_offset if self.next_offset is not None
                       else self.offset + (self.step if forward else -self.step))
        self.next_offset = None
        if self.smooth_steps:
            origin = self.offset
            for index in range(1, self.smooth_steps + 1):
                position = round(origin + (destination - origin) * index / self.smooth_steps)
                QTimer.singleShot(index * 16, self, lambda value=position: self.set_offset(value))
        else:
            self.set_offset(destination)
        event.accept()


def equal_pixels(actual, expected):
    return actual.convertToFormat(QImage.Format_RGB32) == expected.convertToFormat(QImage.Format_RGB32)


def main():
    assert sys.platform == "win32", "此脚本需要真实 Windows 会话"
    app = QApplication([])
    assert app.platformName() == "windows", "不能使用 offscreen 后端冒充真实窗口验收"
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app, "light")
    manager = ScreenManager.instance()
    old_cursor = QCursor.pos()
    measurements = {"platform": platform.platform(), "processor": platform.processor(),
                    "screens": [{"name": screen.name(), "logical": screen.geometry().getRect(),
                                 "physical": manager._records[screen][1].getRect(),
                                 "dpr": screen.devicePixelRatio()} for screen in app.screens()],
                    "capture_ms": [], "confirm_ms": [], "overlap_match_ms": [],
                    "viewport_match_ms": [], "feed_ms": [], "feed_nonmatch_ms": [],
                    "preview_ms": [], "cases": []}
    heartbeats = []
    heartbeat = QTimer()
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: heartbeats.append(time.perf_counter()))
    heartbeat.start()
    active = []

    def wait(predicate, message, timeout=5):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        raise AssertionError(message() if callable(message) else message)

    def settled():
        QTest.qWait(80)
        ctypes.windll.dwmapi.DwmFlush()

    def position_cursor(page):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        point = manager.logical_to_physical(page.region.center(), app.primaryScreen())
        if not user32.SetCursorPos(point.x(), point.y()):
            raise ctypes.WinError(ctypes.get_last_error())

    def new_page(horizontal=False):
        page = ScrollPage(app, horizontal)
        active.append(page)
        page.show()
        assert QTest.qWaitForWindowExposed(page), "合成窗口未显示"
        page.raise_()
        page.activateWindow()
        position_cursor(page)
        settled()
        _focus_scroll_target(0, int(page.winId()))
        assert manager.screen_at(page.region.topLeft()) == manager.screen_at(page.region.bottomRight()), "选区需要完整位于主屏"
        return page

    def send(page, destination, multiplier=1, flow=None, controls_foreground=False):
        # 发出系统输入前，确认光标下与前台均为本进程创建的目标窗口。
        position_cursor(page)
        controls = int(flow._bar.winId()) if controls_foreground else 0
        _check_scroll_target(int(page.winId()), flow._mask_hwnd() if flow else 0, controls)
        page.next_offset = destination
        count = len(page.wheels)
        _send_scroll(page.horizontal, multiplier)
        wait(lambda: len(page.wheels) > count, "真实滚轮未到达合成窗口")

    def activate(window):
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.GetForegroundWindow.restype = wintypes.HWND
        assert user32.SetForegroundWindow(int(window.winId())), "无法激活本进程验收窗口"
        assert user32.GetForegroundWindow() == int(window.winId()), "本进程窗口没有取得前台"

    def start(page, automatic=False, controls_foreground=False):
        initial = manager.capture_region(page.region)
        assert equal_pixels(initial.toImage(), page.expected(page.span)), "可见首帧与合成像素不一致"
        frame = ScreenFrame(app.primaryScreen(), page.region, initial,
                            manager.logical_to_physical(page.region.topLeft(), app.primaryScreen()))
        delivered = []
        flow = LongShotFlow(app, delivered.append)
        active.append(flow)
        flow.start_region(DesktopCapture([frame]), page.region)
        assert flow._hook and flow._hook.isRunning(), "真实 WheelHook 未启动"
        flow._acceptance_wheels = []
        (flow._hook.horizontalWheel if page.horizontal else flow._hook.wheel).connect(flow._acceptance_wheels.append)
        flow._bar.direction.setCurrentIndex(1 if page.horizontal else 0)
        flow._bar.mode.setCurrentIndex(1 if automatic else 0)
        assert not flow._bar.geometry().intersects(page.region), "控制条与合成选区相交"
        original_grab = flow._grab

        def timed_grab():
            begin = time.perf_counter()
            result = original_grab()
            measurements["capture_ms"].append((time.perf_counter() - begin) * 1000)
            return result

        flow._grab = timed_grab
        if controls_foreground:
            activate(flow._bar)
        flow._bar._toggle.click()
        if not automatic and not controls_foreground:
            page.activateWindow()
            position_cursor(page)
        settled()
        return flow, delivered

    def check_result(page, flow, end):
        assert flow._stitcher.height == end, (flow._stitcher.height, end, flow._bar._status.text())
        assert equal_pixels(flow._stitcher.result.toImage(), page.expected(end)), "结果出现漏行、重复或像素变化"

    def cleanup(flow):
        hook = flow._hook
        windows = [flow._bar, *flow._masks]
        flow.cancel()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not hook.isRunning() and hook._hook is None, "残留原生滚轮钩子"
        assert all(not timer.isActive() for timer in (flow._sample_timer, flow._auto_timer, flow._ack_timer)), "残留定时器"
        assert flow._bar is None and not flow._masks and not flow.is_active
        import shiboken6
        assert all(not shiboken6.isValid(window) for window in windows), "残留采集窗口"

    original_overlap = longshot.find_overlap
    original_viewport = longshot._find_viewport_end
    original_feed = longshot.ImageStitcher.feed
    original_preview = longshot.LongShotBar.set_preview
    matching_total = [0.0]

    def timed_match(function, bucket):
        def wrapped(*args, **kwargs):
            begin = time.perf_counter()
            result = function(*args, **kwargs)
            elapsed = (time.perf_counter() - begin) * 1000
            measurements[bucket].append(elapsed)
            matching_total[0] += elapsed
            return result
        return wrapped

    def timed_feed(stitcher, *args, **kwargs):
        matching_before = matching_total[0]
        begin = time.perf_counter()
        result = original_feed(stitcher, *args, **kwargs)
        elapsed = (time.perf_counter() - begin) * 1000
        measurements["feed_ms"].append(elapsed)
        # 扣除本次 feed 内的匹配后仍包含转换、复制、拼接及状态维护，不能称为纯复制。
        measurements["feed_nonmatch_ms"].append(elapsed - (matching_total[0] - matching_before))
        return result

    def timed_preview(bar, *args, **kwargs):
        begin = time.perf_counter()
        result = original_preview(bar, *args, **kwargs)
        measurements["preview_ms"].append((time.perf_counter() - begin) * 1000)
        return result

    longshot.find_overlap = timed_match(original_overlap, "overlap_match_ms")
    longshot._find_viewport_end = timed_match(original_viewport, "viewport_match_ms")
    longshot.ImageStitcher.feed = timed_feed
    longshot.LongShotBar.set_preview = timed_preview
    try:
        for horizontal in (False, True):
            page = new_page(horizontal)
            send(page, 0)
            desired = -120
            multiplier = 1 if page.wheels[-1] == desired else -1
            flow, delivered = start(page)
            print("RUN", "horizontal" if horizontal else "vertical", "DPR", page.dpr, "multiplier", multiplier, flush=True)
            positions = [page.span - 48, 2 * (page.span - 48)]
            for position in positions:
                begin = time.perf_counter()
                send(page, position, multiplier, flow)
                wait(lambda: flow._stitcher.height == page.span + position,
                     lambda: f"48px 快滚未接上：{flow._bar._status.text()} offset={page.offset} height={flow._stitcher.height} wheel={page.wheels} hook={flow._acceptance_wheels} reverse={flow._reverse} running={flow._running}")
                measurements["confirm_ms"].append((time.perf_counter() - begin) * 1000)
                check_result(page, flow, page.span + position)
            history_end = page.span + positions[-1]
            send(page, history_end + 200, multiplier, flow)
            wait(lambda: flow._recovering, "无重叠未进入恢复状态")
            check_result(page, flow, history_end)
            assert "已保留" in flow._bar._status.text()
            assert not any(widget.isModal() for widget in app.topLevelWidgets()), "恢复弹出阻塞窗口"
            if horizontal:
                send(page, positions[0], -multiplier, flow)
            else:
                # 模拟拖动滚动条：可见画面改变，但不产生任何滚轮信号。
                wheel_count = len(page.wheels)
                page.set_offset(positions[0])
            wait(lambda: not flow._recovering, "回滚没有自动定位")
            if not horizontal:
                assert len(page.wheels) == wheel_count, "无滚轮恢复场景意外产生滚轮事件"
            check_result(page, flow, history_end)
            send(page, positions[-1], multiplier, flow)
            wait(lambda: flow._stitcher.viewport_start == positions[-1], "经过已有内容未更新视口")
            check_result(page, flow, history_end)
            send(page, positions[-1] + 137, multiplier, flow)
            wait(lambda: flow._stitcher.height == history_end + 137, "恢复后新增内容未接上")
            check_result(page, flow, history_end + 137)
            send(page, positions[-1], -multiplier, flow)
            wait(lambda: flow._stitcher.height == history_end, "正常反向滚动没有保留原裁尾语义")
            check_result(page, flow, history_end)
            send(page, positions[-1] + 137, multiplier, flow)
            wait(lambda: flow._stitcher.height == history_end + 137, "正常反向裁尾后未能重新追加")
            check_result(page, flow, history_end + 137)
            # 第二次失配直接完成，交付必须只含已确认图像。
            send(page, history_end + 400, multiplier, flow)
            wait(lambda: flow._recovering, "停止场景未进入恢复状态")
            hook = flow._hook
            begin = time.perf_counter()
            flow._finish()
            wait(lambda: bool(delivered), "恢复期间停止未交付结果")
            assert equal_pixels(delivered[0].toImage(), page.expected(history_end + 137))
            assert not hook.isRunning() and hook._hook is None
            assert all(not timer.isActive() for timer in (flow._sample_timer, flow._auto_timer, flow._ack_timer))
            measurements["cases"].append({"name": "horizontal" if horizontal else "vertical",
                                          "physical_viewport": [page.pixel_width, page.pixel_height],
                                          "finish_ms": (time.perf_counter() - begin) * 1000,
                                          "passed": "48px overlap, loss, relocate, preserve history, extend, ordinary reverse, finish",
                                          "recovery_input": "native wheel" if horizontal else "visible offset change without wheel"})
            page.close()

        page = new_page()
        page.limit = 2 * page.step + 39
        page.smooth_steps = 4
        flow, delivered = start(page, automatic=True)
        wait(lambda: not flow._running, "自动滚动未在短尾处暂停", timeout=12)
        assert page.offset == page.limit, (page.offset, page.limit, flow._bar._status.text())
        check_result(page, flow, page.span + page.limit)
        assert not flow._recovering, "自动平滑短尾被误判失配"
        cleanup(flow)
        measurements["cases"].append({"name": "automatic_smooth_short_tail",
                                      "tail_px": 39, "passed": "stable capture, exact pixels, bottom pause, cleanup"})
        page.close()

        page = new_page()
        send(page, 0)
        multiplier = 1 if page.wheels[-1] == -120 else -1
        page.step = page.span + 200
        flow, delivered = start(page, automatic=True)
        wait(lambda: flow._recovering, "自动越过整屏后没有进入恢复状态")
        assert not flow._auto_timer.isActive() and not flow._auto_pending
        check_result(page, flow, page.span)
        page.step = 137
        page.limit = 2 * page.step + 39
        send(page, 0, -multiplier, flow)
        wait(lambda: not flow._recovering, "自动模式回滚后未定位")
        assert flow._running and flow._auto_timer.isActive(), "目标与光标不变时没有恢复自动滚动"
        wait(lambda: not flow._running, "恢复自动滚动后没有到底暂停", timeout=10)
        assert page.offset == page.limit
        check_result(page, flow, page.span + page.limit)
        cleanup(flow)
        measurements["cases"].append({"name": "automatic_recovery",
                                      "passed": "injection stops, rollback relocates, same target resumes injection, exact pixels"})
        page.close()

        page = new_page()
        send(page, 0)
        multiplier = 1 if page.wheels[-1] == -120 else -1
        flow, delivered = start(page)
        send(page, page.span + 200, multiplier, flow)
        wait(lambda: flow._recovering, "取消场景未进入恢复状态")
        cleanup(flow)
        assert not delivered
        measurements["cases"].append({"name": "cancel_recovery", "passed": "no result or surviving hook/timer/window"})
        page.close()

        routing = ctypes.c_uint()
        assert ctypes.windll.user32.SystemParametersInfoW(0x201C, 0, ctypes.byref(routing), 0), "无法读取系统滚轮路由设置"
        if routing.value == 2:
            page = new_page()
            send(page, 0)
            multiplier = 1 if page.wheels[-1] == -120 else -1
            flow, delivered = start(page, controls_foreground=True)
            for position in (137, 274):
                send(page, position, multiplier, flow, controls_foreground=True)
                wait(lambda: flow._stitcher.height == page.span + position,
                     lambda: f"控制条前台时手动滚动未采集：{flow._bar._status.text()}")
                check_result(page, flow, page.span + position)
                assert ctypes.windll.user32.GetForegroundWindow() == int(flow._bar.winId()), "案例意外重新激活了目标窗口"
            cleanup(flow)
            measurements["cases"].append({"name": "manual_controls_foreground", "wheel_routing": routing.value,
                                          "passed": "two real background wheel events append exact pixels while controls retain foreground"})
            page.close()
        else:
            measurements["cases"].append({"name": "manual_controls_foreground", "wheel_routing": routing.value,
                                          "skipped": "Windows inactive-window wheel routing is not enabled; no system settings changed"})

        page = new_page()
        send(page, 0)
        multiplier = 1 if page.wheels[-1] == -120 else -1
        flow, delivered = start(page)
        # 延长单个样本等待，为真实 wheel 与下一次采样之间插入确定的窗口切换。
        flow._sample_timer.setInterval(250)
        send(page, 137, multiplier, flow)
        wait(lambda: flow._sample_timer.isActive(), "滚轮没有排队采样")
        assert flow._samples == 0, "尚未插入窗口切换，采样已提前执行"
        other = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        active.append(other)
        other.setWindowTitle("GridStruct synthetic focus switch")
        other.setGeometry(page.geometry().right() + 40, page.geometry().bottom() - 100, 180, 80)
        other.show()
        activate(other)
        wait(lambda: not flow._running, "wheel 后采样前切换前台没有暂停")
        check_result(page, flow, page.span)
        assert flow._samples == 0 and not flow._recovering, "窗口变化后不应捕获或进入普通失配恢复"
        assert "窗口已变化" in flow._bar._status.text()
        cleanup(flow)
        measurements["cases"].append({"name": "focus_switch_before_sample",
                                      "passed": "real wheel queued, foreground switched to own second window, sample guard pauses before capture"})
        other.close()
        page.close()
    finally:
        longshot.find_overlap = original_overlap
        longshot._find_viewport_end = original_viewport
        longshot.ImageStitcher.feed = original_feed
        longshot.LongShotBar.set_preview = original_preview
        heartbeat.stop()
        for item in reversed(active):
            if isinstance(item, LongShotFlow):
                item.cancel()
            else:
                item.close()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        QCursor.setPos(old_cursor)

    for key in ("capture_ms", "confirm_ms", "overlap_match_ms", "viewport_match_ms",
                "feed_ms", "feed_nonmatch_ms", "preview_ms"):
        samples = measurements[key]
        measurements[key] = {"count": len(samples), "p50": float(np.percentile(samples, 50)),
                             "p95": float(np.percentile(samples, 95)), "max": max(samples)}
    gaps = np.diff(heartbeats) * 1000
    measurements["gui_heartbeat_gap_ms"] = {"count": len(gaps), "p95": float(np.percentile(gaps, 95)), "max": float(gaps.max())}
    measurements["limits"] = "Only actual current primary-screen DPR; mixed-DPI/negative-origin and animation stress require separate checks."
    measurements["timing_scope"] = {
        "feed_nonmatch_ms": "feed minus both matching functions called within that feed; includes image conversion, copy, assembly and bookkeeping; not pure copy",
        "feed_ms": "all feed calls in existing cases, including initial frames, accepted frames and rejections",
        "preview_ms": "set_preview computation and QLabel pixmap assignment; excludes later compositor presentation",
        "confirm_ms": "four fast-scroll actions until confirmed stitched output; too few samples for a formal performance gate",
    }
    print(json.dumps(measurements, ensure_ascii=False, indent=2), flush=True)
    print("PASS: Windows visible synthetic longshot acceptance", flush=True)


if __name__ == "__main__":
    main()
