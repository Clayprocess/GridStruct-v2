# 合成 QScreen 对照：不读取桌面，验证区域采集的原像素与采集范围。
import os
import sys
from pathlib import Path
from time import perf_counter

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication
from capture import ScreenManager, capture_virtual_desktop


class FakeScreen:
    def __init__(self, name, geometry, physical, dpr):
        self.label, self.rect, self.physical, self.dpr = name, geometry, physical, dpr
        y, x = np.indices((physical.height(), physical.width()), dtype=np.uint32)
        data = np.empty((physical.height(), physical.width(), 4), dtype=np.uint8)
        data[:, :, 0] = x % 251
        data[:, :, 1] = y % 253
        data[:, :, 2] = (x // 7 + y // 11) % 255
        data[:, :, 3] = 255
        self.image = QPixmap.fromImage(QImage(data.data, physical.width(), physical.height(),
                                             data.strides[0], QImage.Format_RGBA8888).copy())
        self.calls = []
        self.pixel_calls = []
        self.failures = 0

    def name(self): return self.label
    def geometry(self): return self.rect
    def devicePixelRatio(self): return self.dpr

    def grabWindow(self, window, x=0, y=0, width=-1, height=-1):
        self.calls.append((x, y, width, height))
        # Qt 6 同时有有效宽高时缩放 QSize；否则保留负尺寸及另一维的值。
        px, py = int(x * self.dpr + .5), int(y * self.dpr + .5)
        if width >= 0 and height >= 0:
            width, height = int(width * self.dpr + .5), int(height * self.dpr + .5)
        if width < 0: width = self.image.width() - px
        if height < 0: height = self.image.height() - py
        assert px >= 0 and py >= 0
        assert px + width <= self.image.width() and py + height <= self.image.height()
        self.pixel_calls.append(QRect(px, py, width, height))
        if self.failures:
            self.failures -= 1
            return QPixmap()
        result = self.image.copy(px, py, width, height)
        result.setDevicePixelRatio(self.dpr)
        return result


class FakeManager:
    capture_region = ScreenManager.capture_region
    logical_to_physical = ScreenManager.logical_to_physical
    physical_rect_to_logical = ScreenManager.physical_rect_to_logical
    def __init__(self, screens):
        self.screens = screens
        self._records = {s: (s.rect, s.physical, s.dpr) for s in screens}
        self._signature = ('stable',)
        self.refresh_count = 0
        self.change_at = 0
    def refresh(self):
        self.refresh_count += 1
        if self.change_at and self.refresh_count >= self.change_at:
            self._signature = (self.refresh_count,)


def check(manager, rect, baseline=False):
    ScreenManager._instance = manager
    expected = capture_virtual_desktop().crop(rect)
    for s in manager.screens:
        s.calls.clear()
        s.pixel_calls.clear()
    start = perf_counter()
    actual = capture_virtual_desktop().crop(rect) if baseline else manager.capture_region(rect)
    elapsed = (perf_counter() - start) * 1000
    assert actual.size() == expected.size(), (rect, actual.size(), expected.size())
    assert actual.devicePixelRatio() == expected.devicePixelRatio()
    assert actual.toImage() == expected.toImage(), rect
    for s in manager.screens:
        if not s.rect.intersects(rect):
            assert not s.calls, f'Captured unrelated screen {s.label}: {s.calls}'
        else:
            assert len(s.calls) == 1
            local = rect.intersected(s.rect).translated(-s.rect.topLeft())
            # 允许仅为亚像素取整扩展的边沿，不允许退回整屏抓取。
            request = s.pixel_calls[0]
            assert request.width() <= local.width() * s.dpr + 2 * s.dpr + 2
            assert request.height() <= local.height() * s.dpr + 2 * s.dpr + 2
    return elapsed


def check_physical(manager, physical):
    logical = manager.physical_rect_to_logical(physical)
    shot = capture_virtual_desktop()
    for screen in manager.screens:
        screen.pixel_calls.clear()
    first = shot.crop(logical, physical)
    live = manager.capture_region(logical, physical)
    assert first.toImage() == live.toImage(), physical
    bounds = QRect()
    for screen in manager.screens:
        bounds = bounds.united(physical.intersected(screen.physical))
    assert first.size() == shot.pixel_size(logical, physical) == bounds.size()
    for screen in manager.screens:
        overlap = physical.intersected(screen.physical)
        if overlap.isEmpty():
            assert not screen.pixel_calls
            continue
        local = overlap.translated(-screen.physical.topLeft())
        expected = screen.image.copy(local).toImage()
        actual = first.copy(overlap.translated(-bounds.topLeft())).toImage()
        actual.setDevicePixelRatio(1)
        assert actual == expected, (physical, screen.name())
        request, = screen.pixel_calls
        assert request.contains(local)
        assert request.width() <= local.width() + 2 * screen.dpr
        assert request.height() <= local.height() + 2 * screen.dpr


def main():
    _app = QApplication([])
    baseline = '--baseline' in sys.argv
    samples = 0
    for dpr in (1, 1.25, 1.5, 2, 2.25):
        logical = QSize(round(1920 / dpr), round(1080 / dpr))
        screen = FakeScreen('left', QRect(QPoint(-1920, -90), logical), QRect(-1920, -90, 1920, 1080), dpr)
        other = FakeScreen('right', QRect(0, 0, 1280, 720), QRect(0, 0, 1280, 720), 1)
        manager = FakeManager((screen, other))
        for x, y, w, h in ((0, 0, 100, 101), (3, 7, 101, 77),
                            (logical.width() - 51, 9, 51, 61),
                            (11, logical.height() - 43, 73, 43),
                            (0, 0, logical.width(), logical.height())):
            check(manager, QRect(screen.rect.topLeft() + QPoint(x, y), QSize(w, h)), baseline)
            samples += 1
        # 负坐标、多屏间隙与混合 DPR 均按原生坐标拼接。
        check(manager, QRect(-1930, -100, 2070, 380), baseline)
        samples += 1
        # #66 的 C3、合并格 B5:D5，加上屏幕边缘、仅一列和跨屏原生像素。
        for rect in (QRect(216, 365, 91, 25), QRect(126, 413, 271, 25),
                     QRect(-1, -1, 91, 25), QRect(1919, 1079, 1, 1), QRect(1917, 174, 12, 9)):
            check_physical(manager, rect.translated(screen.physical.topLeft()))
            samples += 1
    for rect in (QRect(4000, 4000, 20, 20), QRect()):
        check(manager, rect, baseline)
        samples += 1
    other.calls.clear()
    other.failures = 1
    recovered = manager.capture_region(QRect(0, 0, 100, 100))
    assert recovered.size() == QSize(100, 100) and len(other.calls) == 2
    other.failures = 3
    try:
        manager.capture_region(QRect(0, 0, 100, 100))
    except RuntimeError:
        pass
    else:
        raise AssertionError('Persistent capture failure was accepted')
    manager.change_at = manager.refresh_count + 2
    try:
        manager.capture_region(QRect(0, 0, 100, 100))
    except RuntimeError:
        pass
    else:
        raise AssertionError('Unstable layout was accepted')
    print(f'PASS: {samples} pixel/DPR/region cases; bounded capture retries; unstable layout rejected; no real desktop captured')


if __name__ == '__main__':
    main()
