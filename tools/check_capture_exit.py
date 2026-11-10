"""离屏验收：控件查询卡住时仍能退出、再次截图、销毁对象和退出进程。

用法：python -X utf8 tools/check_capture_exit.py。只用合成图、假控件查询和临时配置。
"""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_case(action):
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSettings, QTimer, Qt
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    import shiboken6
    from capture import DesktopCapture, ScreenFrame, ScreenshotFlow
    import capture.snap as snap
    from styles import tokens as T

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    main_thread = threading.get_ident()
    gate = threading.Event()
    entered = threading.Event()
    records = []
    window_rect, element_rect = QRect(0, 0, 800, 600), QRect(20, 20, 60, 40)

    class Provider:
        one_off = False

        def __init__(self):
            self.record = [threading.get_ident()]
            records.append(self.record)

        def hit_path(self, *_args):
            self.record.append(threading.get_ident())
            entered.set()
            gate.wait()
            return [element_rect]

        def ancestors(self, _interrupted):
            return None

        def warm(self, _hwnd, _cursor):
            pass

        def close(self):
            self.record.append(threading.get_ident())

    def flush():
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait_until(predicate):
        deadline = time.monotonic() + 1
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        assert predicate(), "预期事件未到达"

    def active_workers():
        return [thread for thread in threading.enumerate() if thread.name.startswith("GridStruct-snap")]

    with TemporaryDirectory() as temporary, ExitStack() as isolated:
        directory = Path(temporary)
        settings = QSettings(str(directory / "settings.ini"), QSettings.IniFormat)
        settings.setValue("config_version", 1)
        settings.setValue("snap_only_window", False)
        settings.sync()
        pixmap = QPixmap(800, 600)
        pixmap.fill(QColor(T.LIGHT["background"]))
        shot = DesktopCapture([ScreenFrame(app.primaryScreen(), window_rect, pixmap, QPoint())])
        isolated.enter_context(patch("settings.storage.data_directory", return_value=directory))
        isolated.enter_context(patch("capture.flow.capture_virtual_desktop", return_value=shot))
        isolated.enter_context(patch.object(snap, "_IS_WIN", True))
        isolated.enter_context(patch.object(snap, "_win32_api", return_value=SimpleNamespace(
            IsWindow=lambda _hwnd: True, IsWindowVisible=lambda _hwnd: True,
            GetCursorPos=lambda _point: 0, GetForegroundWindow=lambda: 0)))
        isolated.enter_context(patch.object(snap, "_snapshot_windows", return_value=[(7, window_rect)]))
        isolated.enter_context(patch.object(snap, "_try_init_msaa", side_effect=Provider))
        flow = ScreenshotFlow(app)
        flow.start()
        detector = flow._win._snap
        published = []
        detector.regionDetected.connect(published.append)
        detector.detect(QPoint(30, 30))
        wait_until(entered.is_set)
        started = time.perf_counter()
        if action == "confirm":
            flow._win._sel = QRect(20, 20, 300, 200)
            assert flow._win.confirm() and flow._ann is not None
            flow.cancel()
        else:
            QTest.keyClick(flow._win._windows[0], Qt.Key_Escape)
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert elapsed_ms < 250, f"退出被查询阻塞 {elapsed_ms:.1f}ms"
        assert flow._win is None and flow._ann is None
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        QTest.qWait(30)
        assert ticks, "退出后 GUI 事件循环没有继续处理事件"
        detector.deleteLater()
        flush()
        assert not shiboken6.isValid(detector), "后台查询保活了 Qt 对象"
        count = len(published)
        if action == "repeat":
            for _ in range(20):
                flow.start()
                current = flow._win._snap
                hits = []
                current.regionDetected.connect(hits.append)
                current.detect(QPoint(30, 30))
                assert hits[-1] == window_rect
                QTest.qWait(10)
                assert not current._hits, "繁忙不能缓存成空查询结果"
                assert len(active_workers()) <= snap._MAX_ABANDONED, "重复截图创建了额外的卡住线程"
                flow.cancel()
                flush()
            gate.set()
            wait_until(lambda: not active_workers())
            flow.start()
            hits = []
            flow._win._snap.regionDetected.connect(hits.append)
            flow._win._snap.detect(QPoint(30, 30))
            wait_until(lambda: hits and hits[-1] == element_rect)
            flow.cancel()
            wait_until(lambda: not active_workers())
            flush()
            assert len(published) == count, "旧会话仍在发布结果"
            assert all(len(set(record)) == 1 and record[0] != main_thread for record in records), \
                "COM 的创建、调用和释放应在同一条后台线程"
        # 单会话场景故意不放行：父进程还会验证解释器能正常退出，而不等待永久卡住的 daemon。
        app.aboutToQuit.emit()
        print(f"PASS {action}: exit={elapsed_ms:.1f}ms, GUI alive, QObject released", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("escape", "confirm", "repeat"))
    args = parser.parse_args()
    if args.action:
        run_case(args.action)
    else:
        for action in ("escape", "confirm", "repeat"):
            result = subprocess.run([sys.executable, "-u", "-X", "utf8", __file__, "--action", action],
                                    capture_output=True, text=True, encoding="utf-8", timeout=5)
            print(result.stdout, end="")
            print(result.stderr, end="")
            if result.returncode:
                raise SystemExit(result.returncode)
        print("RESULT: 3/3 passed")
