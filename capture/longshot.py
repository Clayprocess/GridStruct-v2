# 长截图：选区 + 滚轮滚动拼接。
#
# 流程：框选区域 -> 浮动控制条 -> 用户在目标窗口滚轮滚动，
# 全局鼠标钩子（Windows WH_MOUSE_LL）捕获滚轮 -> 截取选区 ->
# 与已拼接图做重叠匹配 -> 完成/取消。
#
# 拼接：取已拼接图底部 strip，在新截图上半部搜索最佳垂直偏移（SAD），
# 追加非重叠部分。

import sys
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QApplication,
)
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt, QRect, QPoint, Signal, QObject

_IS_WIN = sys.platform == "win32"


class LongShotBar(QWidget):
    """顶部浮动控制条。"""
    finished = Signal()
    cancelled = Signal()

    def __init__(self):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        layout = QHBoxLayout(self)
        layout.addWidget(QLabel("滚动鼠标滚轮拼接，完成后点「完成」"))
        ok = QPushButton("完成")
        ok.clicked.connect(self.finished.emit)
        no = QPushButton("取消")
        no.clicked.connect(self.cancelled.emit)
        layout.addWidget(ok)
        layout.addWidget(no)
        self.setFixedHeight(44)


def _to_gray_small(pm: QPixmap, width: int = 200):
    """转灰度 numpy 小图，用于匹配。返回 (arr, scale)。"""
    import numpy as np
    img = pm.toImage().convertToFormat(QImage.Format_Grayscale8)
    w, h = img.width(), img.height()
    scale = width / w
    sw, sh = width, max(1, int(h * scale))
    small = img.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    arr = np.frombuffer(small.constBits(), dtype=np.uint8).reshape(sh, sw)
    return arr.copy(), scale


def find_overlap(stitched: QPixmap, new: QPixmap,
                 strip_h: int = 80) -> int:
    """返回新图顶部应裁掉的像素数（逻辑坐标）。0 表示无有效重叠。"""
    import numpy as np
    if stitched.height() < strip_h + 20 or new.height() < strip_h + 20:
        return 0
    s_arr, scale = _to_gray_small(stitched)
    n_arr, _ = _to_gray_small(new)
    sh = int(strip_h * scale)
    strip = s_arr[-sh:, :]                      # 已拼接图底部
    max_off = min(n_arr.shape[0] - sh, int(new.height() * 0.9 * scale))
    best, best_off = float("inf"), 0
    for off in range(0, max_off, 2):             # 步进 2 提速
        cand = n_arr[off:off + sh, :]
        sad = np.abs(strip.astype(int) - cand.astype(int)).mean()
        if sad < best:
            best, best_off = sad, off
    if best > 40:                               # 差异太大：无重叠
        return 0
    return int(best_off / scale)


class ImageStitcher:
    """增量拼接器。feed(QPixmap) -> 是否成功拼接。"""

    def __init__(self):
        self._stitched: QPixmap | None = None

    def feed(self, pm: QPixmap) -> bool:
        if self._stitched is None:
            self._stitched = QPixmap(pm)
            return True
        cut = find_overlap(self._stitched, pm)
        if cut <= 0:
            return False
        dpr = pm.devicePixelRatio() or 1.0
        cut_px = int(cut * dpr)
        bottom = pm.copy(0, cut_px, pm.width(), pm.height() - cut_px)
        w = self._stitched.width()
        h = self._stitched.height() + bottom.height()
        out = QPixmap(w, h)
        out.setDevicePixelRatio(dpr)
        from PySide6.QtGui import QPainter
        with QPainter(out) as p:
            p.drawPixmap(0, 0, self._stitched)
            p.drawPixmap(0, int(self._stitched.height() / dpr), bottom)
        self._stitched = out
        return True

    @property
    def result(self) -> QPixmap | None:
        return self._stitched

    @property
    def height(self) -> int:
        return self._stitched.height() if self._stitched else 0


class LongShotFlow(QObject):
    """长截图会话：框选 -> 控制条 -> 滚轮拼接 -> 标注窗口。"""

    def __init__(self, app: QApplication, on_done):
        super().__init__(app)
        self._app = app
        self._on_done = on_done  # 回调(QPixmap)，由外层打开标注窗口
        self._win = None
        self._bar: LongShotBar | None = None
        self._hook = None
        self._stitcher = ImageStitcher()
        self._region = QRect()
        self._accum = 0

    def start(self):
        from .screen import capture_virtual_desktop, virtual_geometry
        from .overlay import ScreenShotWindow
        self._win = ScreenShotWindow(capture_virtual_desktop(),
                                     virtual_geometry())
        self._win.regionSelected.connect(self._on_region)
        self._win.cancelled.connect(self._cleanup)
        self._win.show()

    def _on_region(self, rect: QRect):
        self._region = QRect(rect)
        self._win.close()
        self._win = None
        # 首帧
        self._stitcher.feed(self._grab())
        # 控制条放屏幕顶部居中
        self._bar = LongShotBar()
        self._bar.finished.connect(self._finish)
        self._bar.cancelled.connect(self._cleanup)
        self._bar.show()
        sg = self._app.primaryScreen().geometry()
        self._bar.move(sg.center().x() - self._bar.width() // 2, sg.top() + 20)
        # 滚轮钩子
        from .wheelhook import WheelHook
        self._hook = WheelHook()
        if not self._hook.start():
            print("[长截图] 滚轮钩子启动失败（仅 Windows）")
            self._cleanup()
            return
        self._hook.wheel.connect(self._on_wheel)

    def _grab(self) -> QPixmap:
        from .screen import capture_virtual_desktop
        shot = capture_virtual_desktop()
        dpr = shot.devicePixelRatio() or 1.0
        r = self._region
        pr = QRect(int(r.x() * dpr), int(r.y() * dpr),
                   int(r.width() * dpr), int(r.height() * dpr))
        cropped = shot.copy(pr)
        cropped.setDevicePixelRatio(dpr)
        return cropped

    def _on_wheel(self, delta: int):
        self._accum += delta
        if abs(self._accum) < 120:  # 攒够一格
            return
        self._accum = 0
        if self._stitcher.feed(self._grab()):
            print(f"[长截图] 已拼接高度 {self._stitcher.height}")
        else:
            print("[长截图] 未找到重叠，跳过该帧")

    def _finish(self):
        result = self._stitcher.result
        self._cleanup()
        if result is not None:
            self._on_done(result)

    def _cleanup(self):
        if self._hook is not None:
            self._hook.stop()
            self._hook = None
        if self._bar is not None:
            self._bar.close()
            self._bar = None
        if self._win is not None:
            self._win.close()
            self._win = None
