# 长截图：选区 + 滚轮滚动拼接。
#
# 流程：框选区域 -> 浮动控制条 -> 用户在目标窗口滚轮滚动，
# 全局鼠标钩子（Windows WH_MOUSE_LL）捕获滚轮 -> 截取选区 ->
# 与已拼接图做重叠匹配 -> 完成/取消。
#
# 拼接：取已拼接图底部 strip，在新截图上半部搜索最佳垂直偏移（SAD），
# 追加非重叠部分。

import sys
from time import monotonic
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QApplication,
    QMessageBox, QComboBox, QCheckBox, QProgressBar,
)
from PySide6.QtGui import QPixmap, QImage, QTransform, QPainter, QColor, QPen, QCursor, QRegion
from PySide6.QtCore import Qt, QRect, QPointF, Signal, QObject, QTimer
from styles import tokens as T
from ui import floating_window_type
from .screen import ScreenManager, capture_virtual_desktop

_IS_WIN = sys.platform == "win32"


def _exclude_window(window):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    return bool(user32.SetWindowDisplayAffinity(int(window.winId()), 0x11))


class _LongShotMask(QWidget):
    def __init__(self, screen, region):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.winId()
        self.windowHandle().setScreen(screen)
        self.setGeometry(screen.geometry())
        self._region = region.translated(-screen.geometry().topLeft())

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setClipRegion(QRegion(self.rect()) - QRegion(self._region))
            dim = QColor(T.DARK["background"])
            dim.setAlpha(90)
            painter.fillRect(self.rect(), dim)
            painter.setClipping(False)
            painter.setPen(QPen(QColor(T.ANNOTATION_COLORS[4]), 2))
            painter.drawRect(self._region.adjusted(1, 1, -1, -1))


class LongShotBar(QWidget):
    """顶部浮动控制条。"""
    finished = Signal()
    cancelled = Signal()
    toggled = Signal()

    def __init__(self):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | floating_window_type())
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("长截图")
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("手动滚动", "manual")
        self.mode.addItem("自动滚动", "auto")
        self.direction = QComboBox()
        self.direction.addItem("垂直", "vertical")
        self.direction.addItem("水平", "horizontal")
        self._toggle = QPushButton("开始")
        self._toggle.setProperty("class", "primary")
        self._toggle.clicked.connect(self.toggled.emit)
        controls.addWidget(self.mode)
        controls.addWidget(self.direction)
        controls.addWidget(self._toggle)
        layout.addLayout(controls)
        self._status = QLabel("选择模式和方向，点击开始。绿框表示当前采集位置。")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setFixedSize(320, 180)
        layout.addWidget(self._preview, alignment=Qt.AlignCenter)
        actions = QHBoxLayout()
        ok = QPushButton("停止并使用当前结果")
        ok.setProperty("class", "primary")
        ok.clicked.connect(self.finished.emit)
        no = QPushButton("取消")
        no.setProperty("class", "outline")
        no.clicked.connect(self.cancelled.emit)
        actions.addWidget(ok)
        actions.addWidget(no)
        layout.addLayout(actions)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.hide()
        layout.addWidget(self._progress)
        self.setFixedWidth(410)

    def exclude_from_capture(self) -> bool:
        """让控制条保持可点击，同时从桌面截图中排除（Windows 10 2004+）。"""
        return _exclude_window(self)

    def set_status(self, text: str):
        self._status.setText(text)
        self.adjustSize()

    def set_running(self, running, started=True):
        self._toggle.setText("暂停" if running else ("继续" if started else "开始"))
        self.mode.setEnabled(not running)
        self.direction.setEnabled(not started)

    def set_finalizing(self):
        self.set_status("正在整理当前结果…")
        self._progress.show()
        self.setEnabled(False)

    def set_preview(self, pixmap, viewport, horizontal=False):
        preview = pixmap.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        preview.setDevicePixelRatio(1)
        with QPainter(preview) as painter:
            painter.setPen(QPen(QColor(T.ANNOTATION_COLORS[4]), 2))
            if horizontal:
                span = viewport.width() / pixmap.width() * preview.width()
                painter.drawRect(QRect(round(preview.width() - span), 1, max(1, round(span) - 2), preview.height() - 2))
            else:
                span = viewport.height() / pixmap.height() * preview.height()
                painter.drawRect(QRect(1, round(preview.height() - span), preview.width() - 2, max(1, round(span) - 2)))
        self._preview.setPixmap(preview)

    def closeEvent(self, event):
        self.cancelled.emit()
        super().closeEvent(event)


def _to_gray_small(pm: QPixmap, width: int = 200):
    """转灰度 numpy 小图，用于匹配。返回 (arr, scale)。"""
    import numpy as np
    img = pm.toImage().convertToFormat(QImage.Format_Grayscale8)
    w, h = img.width(), img.height()
    width = min(width, w)
    scale = width / w
    sw, sh = width, max(1, int(h * scale))
    small = img.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    arr = np.frombuffer(small.constBits(), dtype=np.uint8).reshape(sh, small.bytesPerLine())[:, :sw]
    return arr.copy(), scale


def _gray_array(pm):
    import numpy as np
    image = pm.toImage().convertToFormat(QImage.Format_Grayscale8)
    return np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()].copy()


def find_overlap(stitched: QPixmap, new: QPixmap,
                 strip_h: int = 80) -> int:
    """返回新图顶部应裁掉的物理像素数。0 表示无有效重叠。"""
    import numpy as np
    strip_h = min(strip_h, max(4, new.height() // 4))
    if stitched.height() <= strip_h or new.height() <= strip_h:
        return 0
    height = min(stitched.height(), new.height())
    tail = stitched.copy(0, stitched.height() - height, stitched.width(), height)
    s_arr, scale = _to_gray_small(tail)
    n_arr, _ = _to_gray_small(new)
    s_arr, n_arr = s_arr.astype(np.int16), n_arr.astype(np.int16)
    sh = max(1, int(strip_h * scale))
    last = min(len(s_arr), len(n_arr))
    coarse = [_overlap_score(s_arr[-cut:], n_arr[:cut], 40) for cut in range(sh, last + 1)]
    candidates = [sh + off for off, score in enumerate(coarse)
                  if np.isfinite(score) and (off == 0 or score <= coarse[off - 1])
                  and (off == len(coarse) - 1 or score <= coarse[off + 1])]
    if not candidates:
        return 0
    source = _gray_array(tail).astype(np.int16)
    target = _gray_array(new).astype(np.int16)
    # 保留原始纵向像素，仅抽稀列来细化；空白底条带和静态阴影不能决定接缝。
    stride = max(1, new.width() // 200)
    sparse_source, sparse_target = source[:, ::stride], target[:, ::stride]
    radius = int(1 / scale) + 2
    refined = set()
    for off in candidates:
        estimate = round(off / scale)
        cuts = range(max(strip_h, estimate - radius), min(height, estimate + radius) + 1)
        scores = [(_overlap_score(sparse_source[-cut:], sparse_target[:cut]), cut) for cut in cuts]
        if scores:
            best = min(score for score, _ in scores)
            if np.isfinite(best):
                refined.update(cut for score, cut in scores if score == best)
    verified = sorted((_overlap_score(source[-cut:], target[:cut]), cut) for cut in refined)
    if (not verified or not np.isfinite(verified[0][0])
            or (len(verified) > 1 and np.isclose(verified[0][0], verified[1][0], rtol=.01, atol=.05))):
        return 0
    return verified[0][1]


def _overlap_score(source, target, limit=5):
    import numpy as np
    difference = np.abs(source - target)
    mean = difference.mean()
    if mean > limit:
        return float("inf")
    edges = np.zeros(source.shape, dtype=bool)
    edges[1:] |= (np.abs(np.diff(source, axis=0)) > 16) | (np.abs(np.diff(target, axis=0)) > 16)
    edges[:, 1:] |= (np.abs(np.diff(source, axis=1)) > 16) | (np.abs(np.diff(target, axis=1)) > 16)
    if not edges.any():
        return float("inf")
    edge_mean = difference[edges].mean()
    return mean + edge_mean if edge_mean <= limit else float("inf")



def _find_viewport_end(stitched, frame):
    """反向滚动只裁到已拼接图中能唯一匹配的完整视口，找不到就保留结果。"""
    import numpy as np
    source, scale = _to_gray_small(stitched)
    target, _ = _to_gray_small(frame)
    rows = target.shape[0]
    if rows > source.shape[0]:
        return 0
    band = min(rows, max(1, round(80 * scale)))
    scores = [np.abs(source[end - band:end].astype(int) - target[-band:].astype(int)).mean()
              for end in range(rows, source.shape[0] + 1)]
    best = min(scores)
    if best > 20 or scores.count(best) != 1:
        return 0
    end = rows + scores.index(best)
    if np.abs(source[end - rows:end].astype(int) - target.astype(int)).mean() > 20:
        return 0
    estimate = round(end / scale)
    radius = int(1 / scale) + 2
    target_full = _gray_array(frame).astype(np.int16)
    first = max(frame.height(), estimate - radius)
    last = min(stitched.height(), estimate + radius)
    origin = first - frame.height()
    source_full = _gray_array(stitched.copy(0, origin, stitched.width(), last - origin)).astype(np.int16)
    ends = range(first, last + 1)
    scores = [(np.abs(source_full[position - frame.height() - origin:position - origin] - target_full).mean(), position)
              for position in ends]
    best = min(score for score, _ in scores)
    matches = [position for score, position in scores if score == best]
    return matches[0] if best <= 20 and len(matches) == 1 else 0


class ImageStitcher:
    """增量拼接器。feed(QPixmap) -> 是否成功拼接。"""

    def __init__(self, horizontal=False):
        self._stitched: QPixmap | None = None
        self._last_input: QPixmap | None = None
        self._horizontal = horizontal

    def feed(self, pm: QPixmap, reverse=False) -> bool:
        if self._horizontal:
            pm = pm.transformed(QTransform().rotate(90))
        if self._stitched is None:
            self._stitched = QPixmap(pm)
            self._last_input = QPixmap(pm)
            return True
        if pm.toImage() == self._last_input.toImage():
            return False
        if pm.width() != self._stitched.width() or pm.devicePixelRatio() != self._stitched.devicePixelRatio():
            return False
        if reverse:
            end = _find_viewport_end(self._stitched, pm)
            if not end or end >= self._stitched.height():
                return False
            self._stitched = self._stitched.copy(0, 0, pm.width(), end)
            self._last_input = QPixmap(pm)
            return True
        cut = find_overlap(self._stitched, pm)
        if cut <= 0 or cut >= pm.height():
            return False
        dpr = pm.devicePixelRatio() or 1.0
        cut_px = cut
        bottom = pm.copy(0, cut_px, pm.width(), pm.height() - cut_px)
        w = self._stitched.width()
        h = self._stitched.height() + bottom.height()
        out = QPixmap(w, h)
        if out.isNull():
            return False
        out.setDevicePixelRatio(dpr)
        from PySide6.QtGui import QPainter
        with QPainter(out) as p:
            p.drawPixmap(0, 0, self._stitched)
            p.drawPixmap(QPointF(0, self._stitched.deviceIndependentSize().height()), bottom)
        self._stitched = out
        self._last_input = QPixmap(pm)
        return True

    @property
    def result(self) -> QPixmap | None:
        if self._stitched is not None and self._horizontal:
            return self._stitched.transformed(QTransform().rotate(-90))
        return self._stitched

    @property
    def height(self) -> int:
        return self._stitched.height() if self._stitched else 0


class LongShotFlow(QObject):
    """框选、人工/自动采集、暂停与结果交付由同一会话持有。"""

    def __init__(self, app: QApplication, on_done):
        super().__init__(app)
        self._app = app
        self._on_done = on_done
        self._win = None
        self._bar = None
        self._masks = []
        self._manager = ScreenManager.instance()
        self._manager.changed.connect(self._screens_changed)
        self._layout_changed = False
        self._hook = None
        self._stitcher = ImageStitcher()
        self._region = QRect()
        self._screen_region = QRect()
        self._shot = None
        self._last_frame = None
        self._running = False
        self._started = False
        self._horizontal = False
        self._reverse = False
        self._accum = 0
        self._samples = 0
        self._matched = False
        self._saw_change = False
        self._candidate = None
        self._attempted_frame = None
        self._sample_deadline = 0.0
        self._scroll_target = 0
        self._auto_multiplier = 1
        self._auto_pending = False
        self._auto_corrected = False
        self._preparing_base = False
        self._has_increment = False
        self._long_warning_shown = False
        self._sample_timer = QTimer(self)
        self._sample_timer.setInterval(50)
        self._sample_timer.timeout.connect(self._sample_frame)
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(650)
        self._auto_timer.timeout.connect(self._auto_tick)
        self._ack_timer = QTimer(self)
        self._ack_timer.setSingleShot(True)
        self._ack_timer.setInterval(1000)
        self._ack_timer.timeout.connect(self._injection_timeout)

    def start(self):
        if self._win is not None or self._bar is not None:
            return
        self._stitcher = ImageStitcher()
        self._accum = 0
        self._started = False
        self._running = False
        self._long_warning_shown = False
        self._scroll_target = 0
        self._auto_multiplier = 1
        self._auto_pending = False
        self._auto_corrected = False
        self._has_increment = False
        self._layout_changed = False
        from .overlay import ScreenShotWindow
        try:
            self._shot = capture_virtual_desktop()
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(None, "无法开始长截图", str(exc))
            return
        self._win = ScreenShotWindow(self._shot)
        self._win.regionSelected.connect(self._on_region)
        self._win.cancelled.connect(self._cleanup)
        self._win.show()

    def _on_region(self, rect):
        self._region = QRect(rect)
        self._screen_region = QRect(rect)
        window, self._win = self._win, None
        self._shot = window.capture
        window.close()
        # 首帧沿用框选前快照，避免桌面合成器尚未移除覆盖层。
        self._last_frame = self._shot.crop(self._region)
        self._shot = None
        self._stitcher.feed(self._last_frame)
        self._bar = LongShotBar()
        self._bar.finished.connect(self._finish)
        self._bar.cancelled.connect(self._cleanup)
        self._bar.toggled.connect(self._toggle_capture)
        self._masks = [_LongShotMask(screen, self._region) for screen in self._manager.screens]
        for mask in self._masks:
            mask.show()
        self._bar.show()
        if (_IS_WIN and QApplication.platformName() == "windows"
                and (not self._bar.exclude_from_capture()
                     or not all(_exclude_window(mask) for mask in self._masks))):
            QMessageBox.warning(self._bar, "无法开始长截图", "无法将操作条从截图中排除，请检查 Windows 截图支持。")
            self._cleanup()
            return
        screen = self._manager.screen_at(self._screen_region.center()) or self._app.primaryScreen()
        geo = screen.availableGeometry()
        self._bar.move(geo.right() - self._bar.width() - 12, geo.top() + 12)
        self._bar.set_preview(self._last_frame, self._last_frame)
        from .wheelhook import WheelHook
        self._hook = WheelHook()
        if not self._hook.start():
            QMessageBox.warning(self._bar, "无法开始长截图", "Windows 滚轮监听启动失败，未开始采集。")
            self._cleanup()
            return
        self._hook.wheel.connect(self._on_wheel, Qt.QueuedConnection)
        self._hook.horizontalWheel.connect(self._on_horizontal_wheel, Qt.QueuedConnection)
        self._hook.injectedWheel.connect(self._on_injected_wheel, Qt.QueuedConnection)

    def _toggle_capture(self):
        if self._layout_changed:
            return
        if self._running:
            self._pause("已暂停；可继续采集，或停止并使用当前结果。")
            return
        if not self._started:
            self._horizontal = self._bar.direction.currentData() == "horizontal"
            self._stitcher = ImageStitcher(self._horizontal)
            self._stitcher.feed(self._last_frame)
            self._started = True
        self._running = True
        self._bar.set_running(True)
        if self._bar.mode.currentData() == "auto":
            # 只把鼠标移入选区一次；用户移出选区即暂停，避免滚动其他窗口。
            candidates = [self._screen_region.center(), self._screen_region.bottomLeft() + QPointF(12, -12).toPoint(),
                          self._screen_region.topLeft() + QPointF(12, 12).toPoint()]
            target = next((point for point in candidates if self._screen_region.contains(point)
                           and not self._bar.geometry().contains(point)), None)
            if target is None:
                self._pause("操作条覆盖了选区，请重新选择更大的滚动区域。")
                return
            QCursor.setPos(target)
            for mask in self._masks:
                mask.hide()
            try:
                self._scroll_target = _focus_scroll_target(self._mask_hwnd(), self._scroll_target)
            except OSError as exc:
                self._pause("无法激活滚动窗口；可改用手动模式。")
                QMessageBox.warning(self._bar, "自动滚动失败", str(exc))
                return
            finally:
                for mask in self._masks:
                    mask.show()
                self._bar.raise_()
            if not self._has_increment:
                # 激活目标会改变窗口阴影；首次滚动前重取稳定基准，不能替换已有增量。
                self._preparing_base = True
                self._candidate = None
                self._samples = 0
                self._sample_deadline = monotonic() + 1.0
                self._bar.set_status("正在等待窗口画面稳定…")
                self._sample_timer.start()
            else:
                self._bar.set_status("自动滚动中；移出选区或点击暂停即可停止自动滚动。")
                self._auto_timer.start()
        else:
            direction = "向右滚动；向左回滚会裁掉尾部" if self._horizontal else "向下滚动；向上回滚会裁掉尾部"
            self._bar.set_status(f"采集中：{direction}。")

    def _pause(self, message):
        self._running = False
        self._auto_pending = False
        self._auto_corrected = False
        self._hook.cancel_scroll()
        self._ack_timer.stop()
        self._preparing_base = False
        self._sample_timer.stop()
        self._auto_timer.stop()
        self._bar.set_running(False, self._started)
        self._bar.set_status(message)

    def _grab(self):
        return capture_virtual_desktop().crop(self._region)

    def _mask_hwnd(self):
        point = self._manager.cursor_position()
        return next((int(mask.winId()) for mask in self._masks if mask.geometry().contains(point)), 0)

    def _screens_changed(self):
        if self._bar is None or self._hook is None:
            return
        # 已拼接帧依赖原来的物理尺寸；布局变化后只能交付旧结果或重新框选。
        self._layout_changed = True
        self._pause("显示器布局或缩放已变化；当前结果已保留，请停止并使用结果，或取消后重新框选。")
        self._bar._toggle.setEnabled(False)
        for mask in self._masks:
            mask.close()
        self._masks = []
        screen = self._manager.screen_at(self._bar.geometry().center()) or self._app.primaryScreen()
        geometry = screen.availableGeometry()
        self._bar.move(geometry.right() - self._bar.width() - 12, geometry.top() + 12)

    def _on_wheel(self, delta):
        if self._horizontal:
            if QApplication.keyboardModifiers() & Qt.ShiftModifier:
                self._queue_sample(delta)
        else:
            self._queue_sample(delta)

    def _on_horizontal_wheel(self, delta):
        if self._horizontal:
            self._queue_sample(-delta)

    def _queue_sample(self, delta):
        if self._bar is None or not self._running or self._preparing_base:
            return  # 已排队的钩子信号可能在暂停或清理后才到达。
        point = self._manager.cursor_position()
        if not self._screen_region.contains(point) or self._bar.geometry().contains(point):
            return
        self._accum += delta
        if abs(self._accum) < 120:
            return
        self._reverse = self._accum > 0
        self._accum = 0
        self._samples = 0
        self._matched = False
        self._saw_change = False
        self._sample_deadline = monotonic() + 1.0
        self._attempted_frame = None
        if not self._sample_timer.isActive():
            self._candidate = None
            self._sample_timer.start()

    def _auto_tick(self):
        if not self._running or self._sample_timer.isActive():
            return
        if self._auto_pending:
            return
        point = self._manager.cursor_position()
        if not self._screen_region.contains(point) or self._bar.geometry().contains(point):
            self._pause("鼠标已离开采集区域，自动滚动已暂停。")
            return
        try:
            _check_scroll_target(self._scroll_target, self._mask_hwnd())
            self._auto_pending = True
            tag = self._hook.expect_scroll(120 if self._horizontal else -120, self._horizontal)
            _send_scroll(self._horizontal, self._auto_multiplier, tag)
            self._ack_timer.start()
        except OSError as exc:
            self._pause("自动滚动失败；可改用手动模式。")
            QMessageBox.warning(self._bar, "自动滚动失败", str(exc))
            return

    def _on_injected_wheel(self, delta):
        if not self._running or not self._auto_pending:
            return
        self._auto_pending = False
        self._ack_timer.stop()
        desired = 120 if self._horizontal else -120
        if delta == desired:
            self._auto_corrected = False
            self._queue_sample(-120)
        elif delta == -desired and not self._auto_corrected:
            self._auto_multiplier *= -1
            self._auto_corrected = True
            self._auto_tick()
        else:
            self._pause("系统滚轮方向未能确认，请改用手动模式。")

    def _injection_timeout(self):
        if self._running and self._auto_pending:
            self._pause("未收到自动滚轮确认，请改用手动模式。")

    def _sample_frame(self):
        try:
            frame = self._grab()
        except (OSError, RuntimeError) as exc:
            self._pause("捕获失败，当前结果已保留；请重试或停止并使用当前结果。")
            QMessageBox.warning(self._bar, "长截图捕获失败", str(exc))
            return
        if self._layout_changed:
            return
        if frame.size() != self._last_frame.size() or frame.devicePixelRatio() != self._last_frame.devicePixelRatio():
            self._screens_changed()
            return
        self._samples += 1
        changed = frame.toImage() != self._last_frame.toImage()
        automatic = self._bar.mode.currentData() == "auto"
        stable = self._candidate is not None and frame.toImage() == self._candidate.toImage()
        self._candidate = frame
        if self._preparing_base:
            if stable:
                self._last_frame = frame
                self._stitcher = ImageStitcher(self._horizontal)
                self._stitcher.feed(frame)
                self._bar.set_preview(frame, frame, self._horizontal)
                self._preparing_base = False
                self._sample_timer.stop()
                self._bar.set_status("自动滚动中；移出选区或点击暂停即可停止自动滚动。")
                self._auto_timer.start()
            elif monotonic() >= self._sample_deadline:
                self._pause("窗口画面持续变化，无法建立基准；请等动画结束后继续。")
            return
        self._saw_change |= changed
        # 手动滚动连续采样；自动模式一格一格前进并等待相邻两帧稳定。
        attempted = self._attempted_frame is not None and frame.toImage() == self._attempted_frame.toImage()
        if changed and not attempted and (not automatic or stable):
            self._attempted_frame = frame
            self._matched = self._append_frame(frame)
            if not self._running:
                return
            if automatic and self._matched:
                self._sample_timer.stop()
                return
        if monotonic() >= self._sample_deadline:
            self._sample_timer.stop()
            if self._saw_change and not self._matched:
                self._pause("匹配超时：请回滚一小段到预览绿框位置，再点击继续。")
                QMessageBox.warning(self._bar, "图像匹配失败", "可能滚动过快、切换了窗口或页面仍在动画中。\n当前结果已保留；请回滚至已采集内容后继续。")
            elif automatic and not self._saw_change:
                self._pause("一秒内没有内容变化：可能已经到底，或目标窗口不接受自动滚动。")
            elif not self._saw_change:
                self._bar.set_status("没有检测到内容变化，可能已到滚动边界；可继续滚动或停止。")

    def _append_frame(self, frame):
        if not self._stitcher.feed(frame, reverse=self._reverse):
            self._bar.set_status("正在寻找唯一重叠…当前结果保留。")
            return False
        self._last_frame = frame
        self._has_increment = True
        result = self._stitcher.result
        self._bar.set_preview(result, frame, self._horizontal)
        self._bar.set_status(f"已采集 {self._stitcher.height} 像素；绿框为当前视口。")
        if self._stitcher.height > 29000 and not self._long_warning_shown:
            from settings import get_settings
            settings = get_settings()
            self._long_warning_shown = True
            if not settings.value("longshot/hide_length_warning", False, type=bool):
                self._pause("已超过 29000 像素，已暂停等待确认。")
                notice = QMessageBox(QMessageBox.Warning, "超长截图", "截图已超过 29000 像素，部分软件可能无法打开或处理。\n选择继续，或停止并使用当前结果。", parent=self._bar)
                proceed = notice.addButton("继续", QMessageBox.AcceptRole)
                notice.addButton("停止并使用当前结果", QMessageBox.RejectRole)
                checkbox = QCheckBox("以后不再提示")
                notice.setCheckBox(checkbox)
                notice.exec()
                settings.setValue("longshot/hide_length_warning", checkbox.isChecked())
                if notice.clickedButton() == proceed:
                    self._toggle_capture()
                else:
                    self._finish()
        return True

    def _finish(self):
        self._ack_timer.stop()
        self._sample_timer.stop()
        self._auto_timer.stop()
        self._running = False
        self._preparing_base = False
        self._bar.set_finalizing()
        # 先让停止采集状态交回事件循环，再交付已拼接图。
        QTimer.singleShot(0, self, lambda bar=self._bar: self._deliver_result(bar))

    def _deliver_result(self, bar):
        if self._bar is not bar:
            return
        result = self._stitcher.result
        self._cleanup()
        if result is not None:
            self._on_done(result)

    def cancel(self):
        self._cleanup()

    def _cleanup(self):
        self._ack_timer.stop()
        self._running = False
        self._auto_pending = False
        self._preparing_base = False
        self._sample_timer.stop()
        self._auto_timer.stop()
        self._last_frame = None
        self._candidate = None
        self._attempted_frame = None
        self._shot = None
        if self._hook is not None:
            self._hook.stop()
            self._hook = None
        if self._bar is not None:
            bar, self._bar = self._bar, None
            bar.close()
        for mask in self._masks:
            mask.close()
        self._masks = []
        if self._win is not None:
            window, self._win = self._win, None
            window.close()


def _scroll_window(ignored_hwnd):
    """复用吸附过滤，穿过 layered + transparent 辅助光标窗口。"""
    import ctypes
    from ctypes import wintypes
    from .snap import _win32_api, _window_at
    user32 = _win32_api()
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise OSError(ctypes.get_last_error(), "无法取得滚动目标位置")
    target, _ = _window_at(user32, point.x, point.y, ignored_hwnd)
    if not target:
        raise OSError("选区内没有可响应的滚动窗口")
    return user32, target


def _focus_scroll_target(ignored_hwnd, expected=0):
    user32, target = _scroll_window(ignored_hwnd)
    if expected and target != expected:
        raise OSError("选区中的窗口已经改变，请重新选择长截图区域")
    user32.SetForegroundWindow(target)
    foreground = user32.GetForegroundWindow()
    if foreground != target:
        raise OSError("无法激活选区内的滚动窗口")
    return target


def _check_scroll_target(expected, ignored_hwnd):
    user32, target = _scroll_window(ignored_hwnd)
    foreground = user32.GetForegroundWindow()
    if target != expected or foreground != expected:
        raise OSError("滚动目标或前台窗口已改变，自动滚动已停止；请返回原窗口后继续")


def _send_scroll(horizontal, multiplier=1, extra_info=0):
    """只发送一格滚轮，不猜测系统每格对应的像素或行数。"""
    import ctypes
    from ctypes import wintypes

    class MouseInput(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class InputUnion(ctypes.Union):
        _fields_ = [("mi", MouseInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    data = ((120 if horizontal else -120) * multiplier) & 0xffffffff
    event = Input(0, InputUnion(mi=MouseInput(0, 0, data, 0x1000 if horizontal else 0x0800, 0, extra_info)))
    accepted = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))
    if accepted != 1:
        raise OSError(ctypes.get_last_error(), "Windows 未接受滚轮输入；目标程序可能以管理员权限运行。")
