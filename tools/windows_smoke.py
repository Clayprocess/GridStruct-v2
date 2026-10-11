"""Windows 原生验收：只检查本进程合成窗口及其覆盖的屏幕区域。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QRect, QSize, Qt
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
target.setGeometry(QRect(app.primaryScreen().availableGeometry().topLeft() + QPoint(100, 100),
                         QSize(300, 200)))
child = QWidget(target)
child.setAttribute(Qt.WA_NativeWindow)
child.setGeometry(30, 30, 100, 80)
target.show()
overlay = QWidget()
overlay.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
overlay.setGeometry(QRect(target.pos() - QPoint(20, 20), QSize(380, 280)))
overlay.show()
app.processEvents()
ignored = int(overlay.winId())
dpr = target.devicePixelRatioF()
logical_point = target.pos() + QPoint(50, 50)
point = physical(QRect(logical_point, QSize(1, 1)), dpr).topLeft()
try:
    # 快照点查：overlay 自身被排除，应命中 target（窗口级）
    probe = snap.RegionDetector(ignored_hwnd=ignored)
    try:
        probe._refresh_snapshot()
        hit = probe._lookup(point.x(), point.y())
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
        detector.detect(logical_point)
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
    def __init__(self, rect):
        self.seen = []
        self.rect = rect
    def window_rectangles(self, hwnd):
        self.seen.append(hwnd)
        return [QRect(self.rect)]
    def close(self):
        pass

target.show()
app.processEvents()
hwnd = int(target.winId())
window_rect = target.geometry()
button_rect = QRect(window_rect.topLeft() + QPoint(30, 30), QSize(50, 40))
native_window = physical(window_rect, dpr)
fake = _FakeUIA(physical(button_rect, dpr))
original_try = snap._try_init_uia
snap._try_init_uia = lambda: fake
detector = snap.RegionDetector(only_window=False)
# 保留真实 HWND 校验，只隔离桌面其他窗口及周期刷新对缓存用例的影响。
detector._snapshot_timer.stop()
detector._windows = [(hwnd, native_window)]
results = []
detector.regionDetected.connect(results.append)
try:
    key = (hwnd, native_window.getRect())
    background_point = window_rect.bottomRight() - QPoint(20, 20)
    detector.detect(background_point)
    assert results == [window_rect], results
    wait_until(lambda: key in detector._cache)
    assert fake.seen == [hwnd], "必须先完成一次 UIA 查询，避免零调用的假通过"
    request_id = detector._request_id
    detector.detect(button_rect.center())
    assert results[-1] == button_rect, results
    signal_count = len(results)
    for offset in range(10):
        detector.detect(button_rect.topLeft() + QPoint(10 + offset, 10))
    assert len(results) == signal_count, "同一控件内移动不应重复发布高亮"
    detector.detect(background_point)
    assert results[-1] == window_rect, results
    assert detector._request_id == request_id and not detector._requests, "窗内移动不应派发新请求"
    assert fake.seen == [hwnd], "缓存命中后不应再次调用 UIA"
    print(f"PASS: per-window UIA cache, button/background transitions, one UIA query, DPR={dpr}")
finally:
    detector.shutdown()
    snap._try_init_uia = original_try
    target.close()
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
