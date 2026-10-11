"""Windows 原生验收：只检查本进程合成窗口及其覆盖的屏幕区域。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6
import capture.snap as snap

app = QApplication([])

def wait_until(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        app.processEvents()
    assert predicate(), "异步结果超时"

def physical(rect, dpr):
    origin = app.screenAt(rect.center()).geometry().topLeft()
    return QRect(origin.x() + round((rect.x() - origin.x()) * dpr),
                 origin.y() + round((rect.y() - origin.y()) * dpr),
                 round(rect.width()*dpr), round(rect.height()*dpr))

target = QWidget()
target.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
target.setGeometry(100, 100, 300, 200)
child = QWidget(target)
child.setAttribute(Qt.WA_NativeWindow)
child.setGeometry(30, 30, 100, 80)
target.show()
overlay = QWidget()
overlay.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
overlay.setGeometry(80, 80, 380, 280)
overlay.show()
app.processEvents()
ignored = int(overlay.winId())
dpr = target.devicePixelRatioF()
point = round(150 * dpr)
try:
    # 快照点查：overlay 自身被排除，应命中 target（窗口级）
    probe = snap.RegionDetector(ignored_hwnd=ignored)
    try:
        probe._refresh_snapshot()
        hit = probe._lookup(point, point)
        assert hit is not None, "快照未命中窗口"
        hwnd, rect = hit
        assert hwnd != ignored, "应排除 overlay 自身"
        assert rect == physical(target.geometry(), dpr), (rect, physical(target.geometry(), dpr))
    finally:
        probe.shutdown()
    detector = snap.RegionDetector(ignored_hwnd=ignored)
    results = []
    detector.regionDetected.connect(results.append)
    try:
        detector.detect(QPoint(150, 150))
        wait_until(lambda: results)
        assert results[-1] == target.geometry(), (results, target.geometry())
        print(f"PASS: snapshot lookup excludes overlay and hits top-level, DPR={dpr} logical result")
    finally:
        worker = detector._worker
        detector.shutdown()
        assert not detector._thread.isRunning()
        assert not shiboken6.isValid(worker), "worker 未在所属线程释放"
finally:
    overlay.close()
    target.close()

class _FakeUIA:
    """模拟 UIA：按窗口返回固定控件矩形（物理坐标），验证缓存路径。"""
    def __init__(self):
        self.seen = []
    def window_rectangles(self, hwnd):
        self.seen.append(hwnd)
        return [QRect(10, 10, 50, 40)]
    def close(self):
        pass

fake = _FakeUIA()
original_try = snap._try_init_uia
snap._try_init_uia = lambda: fake
detector = snap.RegionDetector()
# 直接注入快照：hwnd=12345 的 1000x1000 物理窗口
detector._windows = [(12345, QRect(0, 0, 1000, 1000))]
results = []
detector.regionDetected.connect(results.append)
try:
    key = (12345, (0, 0, 1000, 1000))
    detector.detect(QPoint(100, 100))
    # 同步快照命中，立即发布窗口矩形（逻辑坐标）
    assert results == [QRect(0, 0, 1000, 1000)], results
    wait_until(lambda: key in detector._cache)
    # UIA 结果已缓存，同一窗口内移动不再跨 COM
    calls = len(fake.seen)
    detector.detect(QPoint(200, 200))
    app.processEvents()
    assert len(fake.seen) == calls, "缓存命中后不应再次调用 UIA"
    # 缓存的控件矩形被采用（物理 10,10,50,40 -> 逻辑）
    assert results[-1] == QRect(10, 10, 50, 40), results
    print("PASS: per-window UIA cache, no recompute inside cached window")
finally:
    detector.shutdown()
    snap._try_init_uia = original_try
print("PASS: detector thread shutdown and worker deletion")

import ctypes
from ctypes import wintypes
from PySide6.QtGui import QPainter, QColor
from PySide6.QtTest import QTest
from capture.wheelhook import WheelHook
from tray import GlobalHotkey
from tray.hotkey import WM_HOTKEY, MOD_CONTROL, MOD_ALT, MOD_SHIFT
from capture.longshot import LongShotBar
from styles import tokens as T
from styles import apply_theme

hook = WheelHook()
try:
    assert hook.start() and hook._hook, "滚轮钩子安装失败"
finally:
    hook.stop()
assert hook._hook is None
print("PASS: native wheel hook install/uninstall")

manager = GlobalHotkey(app)
manager.set_hotkey(MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x87)  # F24，避免占用运行中的截图快捷键。
received = []
try:
    assert manager.register(lambda: received.append(True))
    post = ctypes.windll.user32.PostThreadMessageW
    post.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    assert post(ctypes.windll.kernel32.GetCurrentThreadId(), WM_HOTKEY, 1, 0)
    wait_until(lambda: received)
    assert received == [True]
finally:
    manager.unregister()
print("PASS: registered Win32 hotkey message reaches application callback")

class SolidWindow(QWidget):
    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.fillRect(self.rect(), QColor(T.ANNOTATION_COLORS[0]))

apply_theme(app, "light")
background = SolidWindow()
background.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
background.setGeometry(app.primaryScreen().availableGeometry().adjusted(20, 20, -20, -20))
bar = LongShotBar()
try:
    background.show()
    assert QTest.qWaitForWindowExposed(background)
    bar.move(background.pos() + QPoint(30, 30))
    bar.show()
    assert QTest.qWaitForWindowExposed(bar)
    assert background.geometry().contains(bar.geometry()), "合成背景不足以覆盖完整长截图操作条"
    def bar_pixels():
        app.processEvents()
        ctypes.windll.dwmapi.DwmFlush()
        return app.primaryScreen().grabWindow(0, bar.x(), bar.y(), bar.width(), bar.height()).toImage()
    def pixel_color():
        return bar_pixels().pixelColor(8, 8).name()
    wait_until(lambda: pixel_color() == T.LIGHT["background"])
    assert bar.exclude_from_capture(), "Windows 拒绝排除控制条"
    wait_until(lambda: pixel_color() == T.ANNOTATION_COLORS[0])
    print("PASS: Qt desktop capture excludes control bar and preserves synthetic background")
finally:
    bar.close()
    background.close()
