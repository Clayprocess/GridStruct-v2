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
        stopped = time.monotonic()
        detector.shutdown()
        assert time.monotonic() - stopped < 0.25, "关闭吸附不应等待外部查询"
        detector.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not shiboken6.isValid(detector), "关闭后 detector 应可释放"
finally:
    overlay.close()
    target.close()

print("PASS: detector shutdown is non-blocking and QObject can be released")

from PySide6.QtWidgets import QPushButton
target = QWidget()
target.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
target.setGeometry(QRect(app.primaryScreen().availableGeometry().topLeft() + QPoint(100, 100), QSize(300, 200)))
button = QPushButton("x", target)
button.setGeometry(40, 40, 16, 16)  # 标签页关闭按钮大小的小控件
target.show()
app.processEvents()
detector = snap.RegionDetector(only_window=False)
try:
    # Qt GUI 线程可能已用 STA 初始化 COM；按正式路径在后台创建 MSAA，避免在 GUI 线程误测 MTA。
    results = []
    detector.regionDetected.connect(results.append)
    started = time.monotonic()
    detector.detect(button.mapToGlobal(QPoint(8, 8)))
    logical_button = QRect(button.mapToGlobal(QPoint()), button.size())
    wait_until(lambda: results and results[-1] == logical_button)
    print(f"PASS: native background MSAA reaches a 16px button in {(time.monotonic() - started) * 1000:.1f}ms")
finally:
    detector.shutdown()
    target.close()

import ctypes
from ctypes import wintypes
from PySide6.QtGui import QPainter, QColor
from PySide6.QtTest import QTest
from capture.wheelhook import WheelHook
from tray import GlobalHotkey
from tray.hotkey import WM_HOTKEY
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
received = []
try:
    # F24 组合避免占用运行中的截图快捷键。
    assert manager.register("Ctrl+Alt+Shift+F24", lambda: received.append(True)) == 1
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
