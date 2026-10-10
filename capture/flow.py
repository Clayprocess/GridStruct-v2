# 截图流程编排：截图 -> 框选 -> 工具条 -> 复制/确认。
# P0-A 垂直切片拥有者。标注工具的具体实现后续挂到 FloatPanel.toolSelected 上。

from PySide6.QtCore import QObject, QRect
from PySide6.QtWidgets import QApplication

from .screen import capture_virtual_desktop, virtual_geometry
from .overlay import ScreenShotWindow
from annotate.window import AnnotationWindow


class ScreenshotFlow(QObject):
    """一次截图会话：截图 -> 框选 -> 标注 -> 复制/保存。start() 后全程信号驱动。"""

    def __init__(self, app: QApplication, theme_mode: str = "light"):
        super().__init__(app)
        self._app = app
        self._mode = theme_mode
        self._win: ScreenShotWindow | None = None
        self._ann: AnnotationWindow | None = None
        self._shot = None

    def start(self):
        if self._win is not None or self._ann is not None:
            return  # 已有会话在跑
        self._shot = capture_virtual_desktop()
        vg = virtual_geometry()
        self._win = ScreenShotWindow(self._shot, vg)
        self._win.regionSelected.connect(self._on_region)
        self._win.cancelled.connect(self._cleanup)
        self._win.show()

    # ---------- 内部 ----------

    def _on_region(self, rect: QRect):
        dpr = self._shot.devicePixelRatio() or 1.0
        pr = QRect(int(rect.x() * dpr), int(rect.y() * dpr),
                   int(rect.width() * dpr), int(rect.height() * dpr))
        cropped = self._shot.copy(pr)
        cropped.setDevicePixelRatio(dpr)

        self._win.close()
        self._win = None

        # 标注窗口放在选区原位置
        self._ann = AnnotationWindow(cropped, rect.topLeft(), self._mode)
        self._ann.finished.connect(self._on_finished)
        self._ann.cancelled.connect(self._cleanup)
        self._ann.show()

    def _on_finished(self, pixmap):
        QApplication.clipboard().setPixmap(pixmap)
        print(f"[截图] 已复制 {pixmap.width()}×{pixmap.height()} 到剪贴板")
        self._cleanup()

    def _cleanup(self):
        if self._ann is not None:
            self._ann.close()
            self._ann = None
        if self._win is not None:
            self._win.close()
            self._win = None
