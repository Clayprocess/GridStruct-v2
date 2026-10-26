"""阶段 A 离屏验收：像素、恢复状态、停止清理与明暗控制条；不读取桌面。"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPixmap, QTransform, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from capture.longshot import ImageStitcher, LongShotBar, LongShotFlow, MAX_STITCH_LENGTH
from styles import apply_theme


app = QApplication([])
app.setQuitOnLastWindowClosed(False)
for font in ("arial.ttf", "msyh.ttc"):
    QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / font))
errors = []
sys.excepthook = lambda kind, value, tb: errors.append(f"{kind.__name__}: {value}")
out = Path(__file__).resolve().parents[1] / ".scratch/longshot-stage-a"
out.mkdir(parents=True, exist_ok=True)


def flush():
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not errors, errors


def texture(width, height):
    data = np.random.default_rng(903).integers(0, 256, (height, width, 3), dtype=np.uint8)
    return QPixmap.fromImage(QImage(data.data, width, height, data.strides[0], QImage.Format_RGB888).copy())


source = texture(160, 2600)


def frame(y, dpr=1, horizontal=False):
    pm = source.copy(0, y, 160, 400)
    if horizontal:
        pm = pm.transformed(QTransform().rotate(-90))
    pm.setDevicePixelRatio(dpr)
    return pm


def pixels(pm):
    image = pm.toImage()
    image.setDevicePixelRatio(1)
    return image


def check_pixels():
    for horizontal in (False, True):
        for dpr in (1, 1.25, 1.5, 2, 2.25):
            for positions in ((0, 137, 274), (0, 352, 704), (0, 137, 176)):
                stitch = ImageStitcher(horizontal)
                for y in positions:
                    assert stitch.feed(frame(y, dpr, horizontal)), (horizontal, dpr, y)
                expected = source.copy(0, 0, 160, positions[-1] + 400)
                if horizontal:
                    expected = expected.transformed(QTransform().rotate(-90))
                assert pixels(stitch.result) == pixels(expected), (horizontal, dpr, positions)
                assert stitch.result.devicePixelRatio() == dpr
                original = pixels(stitch.result)
                assert not stitch.feed(frame(1900, dpr, horizontal))
                assert pixels(stitch.result) == original
                assert stitch.relocate(frame(0, dpr, horizontal))
                assert stitch.viewport_start == 0
                assert pixels(stitch.result) == original
                assert stitch.feed(frame(100, dpr, horizontal), reverse=True)
                assert pixels(stitch.result) == original, "恢复回滚裁掉已有历史"
                for y in (positions[-1], positions[-1] + 123):
                    assert stitch.feed(frame(y, dpr, horizontal))
                expected = source.copy(0, 0, 160, positions[-1] + 523)
                if horizontal:
                    expected = expected.transformed(QTransform().rotate(-90))
                assert pixels(stitch.result) == pixels(expected)
                assert stitch.feed(frame(90, dpr, horizontal), reverse=True)
                assert stitch.height == 490, "普通反向滚动应保留既有裁尾语义"
    tall = texture(64, MAX_STITCH_LENGTH + 400)
    stitch = ImageStitcher()
    assert stitch.feed(tall.copy(0, 0, 64, MAX_STITCH_LENGTH - 200))
    assert stitch.feed(tall.copy(0, MAX_STITCH_LENGTH - 500, 64, 700))
    assert stitch.height == MAX_STITCH_LENGTH
    assert pixels(stitch.result) == pixels(tall.copy(0, 0, 64, MAX_STITCH_LENGTH))
    print("PASS: 30 pixel/DPR/axis cases; recovery preserves history; normal reverse; length cap")


class Hook:
    stopped = False
    def cancel_scroll(self): pass
    def stop(self): self.stopped = True


def setup_flow(automatic=False):
    delivered = []
    flow = LongShotFlow(app, delivered.append)
    flow._manager = SimpleNamespace(cursor_position=lambda: QPoint(30, 30),
                                    screen_at=lambda point: app.primaryScreen())
    flow._screen_region = flow._region = QRect(0, 0, 160, 400)
    flow._bar = LongShotBar()
    flow._bar.move(1000, 0)
    flow._bar.finished.connect(flow._finish)
    flow._bar.cancelled.connect(flow._cleanup)
    flow._bar.toggled.connect(flow._toggle_capture)
    flow._hook = Hook()
    flow._started = flow._running = flow._has_increment = True
    flow._bar.set_running(True)
    flow._bar.mode.setCurrentIndex(1 if automatic else 0)
    for y in (0, 137, 274):
        assert flow._stitcher.feed(frame(y))
    flow._last_frame = frame(274)
    return flow, delivered


def sample(flow, pm, delta=-120, expire=False):
    flow._queue_sample(delta)
    if expire:
        flow._sample_deadline = -1
    with patch.object(flow, "_grab", return_value=pm):
        flow._sample_frame()


def check_flow():
    with patch("capture.longshot.show_message", side_effect=AssertionError("unexpected modal")):
        flow, delivered = setup_flow()
        saved = pixels(flow._stitcher.result)
        sample(flow, frame(1800), expire=True)
        assert flow._recovering and flow._running
        assert not flow._auto_timer.isActive()
        assert "结果已保留" in flow._bar._status.text()
        sample(flow, frame(1800), expire=True)
        assert flow._recovering and pixels(flow._stitcher.result) == saved
        assert flow._sample_timer.isActive(), "恢复必须能观察滚动条拖动，不能只等待滚轮事件"
        QTest.mouseClick(flow._bar._toggle, Qt.LeftButton)
        assert not flow._running and flow._recovering
        QTest.mouseClick(flow._bar._toggle, Qt.LeftButton)
        assert flow._running and flow._recovering
        with patch.object(flow, "_grab", return_value=frame(137)):
            flow._sample_frame()
        assert not flow._recovering and flow._stitcher.viewport_start == 137
        assert pixels(flow._stitcher.result) == saved
        sample(flow, frame(274))
        assert pixels(flow._stitcher.result) == saved
        sample(flow, frame(411))
        assert pixels(flow._stitcher.result) == pixels(source.copy(0, 0, 160, 811))
        sample(flow, frame(1800), expire=True)
        hook = flow._hook
        QTest.mouseClick(flow._bar._finish_button, Qt.LeftButton)
        assert not delivered
        flush()
        assert len(delivered) == 1 and pixels(delivered[0]) == pixels(source.copy(0, 0, 160, 811))
        assert hook.stopped and flow._bar is None and flow._hook is None
        assert not any(t.isActive() for t in (flow._sample_timer, flow._auto_timer, flow._ack_timer))
        flow.deleteLater()
        flush()

        for outcome in ("continue", "cursor_left", "window_changed"):
            flow, _ = setup_flow(automatic=True)
            flow._enter_recovery()
            with patch("capture.longshot._send_scroll", side_effect=AssertionError("wheel during recovery")):
                flow._auto_tick()
            if outcome == "cursor_left":
                flow._manager.cursor_position = lambda: QPoint(-100, -100)
            error = OSError("changed") if outcome == "window_changed" else None
            with patch("capture.longshot._check_scroll_target", side_effect=error):
                assert flow._recover_frame(frame(137))
            assert flow._auto_timer.isActive() == (outcome == "continue")
            assert flow._running == (outcome == "continue")
            assert flow._stitcher.height == 674
            hook = flow._hook
            flow.cancel()
            assert hook.stopped and not flow.is_active
            flow.deleteLater()
            flush()

        flow, _ = setup_flow()
        sample(flow, flow._last_frame, expire=True)
        assert not flow._recovering, "不变帧不应进入恢复"
        sample(flow, frame(1800))
        assert not flow._recovering and flow._saw_change
        flow._sample_deadline = -1
        with patch.object(flow, "_grab", return_value=flow._last_frame):
            flow._sample_frame()
        assert not flow._recovering and not flow._saw_change, "瞬态回到确认视口仍误报失配"
        saved = pixels(flow._stitcher.result)
        flow._scroll_target = 123
        with patch("capture.longshot.QApplication.platformName", return_value="windows"), \
                patch("capture.longshot._IS_WIN", True), \
                patch("capture.longshot._check_capture_target", side_effect=OSError("window changed")), \
                patch.object(flow, "_grab", side_effect=AssertionError("captured changed target")):
            flow._sample_frame()
        assert not flow._running and pixels(flow._stitcher.result) == saved
        flow._running = True
        flow._enter_recovery()
        flow._screens_changed()
        assert flow._layout_changed and not flow._running and not flow._bar._toggle.isEnabled()
        hook = flow._hook
        flow._bar.close()
        assert hook.stopped and not flow.is_active
        flow.deleteLater()
        flush()
    print("PASS: loss/rollback/resume; automatic resume guards; pause/finish/cancel; layout change; no modal")


def render():
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        flow, _ = setup_flow()
        flow._enter_recovery()
        flow._sample_timer.stop()
        flow._bar.set_preview(flow._stitcher.result, frame(137), viewport_start=137)
        flow._bar.show()
        flush()
        assert flow._bar.grab().save(str(out / f"recovery-{mode}.png"))
        flow.cancel()
        flow.deleteLater()
        flush()
    print(f"PASS: light/dark controls rendered to {out}")


check_pixels()
check_flow()
render()
