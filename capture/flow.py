# 截图流程编排：截图 -> 框选 -> 工具条 -> 复制/确认。
# P0-A 垂直切片拥有者。标注工具的具体实现后续挂到 FloatPanel.toolSelected 上。

from PySide6.QtCore import QObject, QRect
from PySide6.QtWidgets import QApplication

from .screen import capture_virtual_desktop, virtual_geometry
from .overlay import ScreenShotWindow
from annotate import AnnotationWindow, FloatPanel


class ScreenshotFlow(QObject):
    """一次截图会话：截图 -> 框选 -> 标注 -> 复制/保存。start() 后全程信号驱动。"""

    def __init__(self, app: QApplication, theme_mode: str = "light"):
        super().__init__(app)
        self._app = app
        self._mode = theme_mode
        self._win: ScreenShotWindow | None = None
        self._ann: AnnotationWindow | None = None
        self._shot = None
        self._panel = None

    def start(self):
        if self._win is not None or self._ann is not None:
            return  # 已有会话在跑
        self._shot = capture_virtual_desktop()
        vg = virtual_geometry()
        self._win = ScreenShotWindow(self._shot, vg, keep_open=True)
        self._win.regionSelected.connect(self._on_region)
        self._win.selectionChanged.connect(self._on_selection)
        self._win.actionRequested.connect(self._trigger_action)
        self._win.cancelled.connect(self._cleanup)
        self._win.show()

    def _on_selection(self, rect):
        if rect.width() < 4 or rect.height() < 4:
            if self._panel is not None:
                self._panel.hide()
            return
        if self._panel is None:
            self._panel = FloatPanel(self._app.property("theme_mode") or self._mode, self._win)
            self._panel.toolSelected.connect(self._select_tool)
            self._panel.actionTriggered.connect(self._trigger_action)
        self._panel.set_position_near(rect.translated(self._win.pos()))
        self._panel.show()

    def _select_tool(self, tool):
        self._win.confirm()
        self._ann.select_tool(tool)

    def _trigger_action(self, action):
        self._win.confirm()
        self._ann.trigger_action(action)

    # ---------- 内部 ----------

    def _on_region(self, rect: QRect):
        dpr = self._shot.devicePixelRatio() or 1.0
        pr = QRect(int(rect.x() * dpr), int(rect.y() * dpr),
                   int(rect.width() * dpr), int(rect.height() * dpr))
        cropped = self._shot.copy(pr)
        cropped.setDevicePixelRatio(dpr)

        if self._panel is not None:
            self._panel.close()
            self._panel = None
        self._shot = None

        # 标注窗口放在选区原位置
        position = rect.topLeft() + virtual_geometry().topLeft()
        mode = self._app.property("theme_mode") or self._mode
        self._ann = AnnotationWindow(cropped, position, mode)
        self._ann.finished.connect(self._on_finished)
        self._ann.cancelled.connect(self._cleanup)
        self._ann.show()

    def _on_finished(self, pixmap):
        QApplication.clipboard().setPixmap(pixmap)
        print(f"[截图] 已复制 {pixmap.width()}×{pixmap.height()} 到剪贴板")
        self._cleanup()

    def cancel(self):
        self._cleanup()

    def _cleanup(self):
        # closeEvent 也会发出取消信号，先释放会话引用避免重入。
        annotation, self._ann = self._ann, None
        window, self._win = self._win, None
        panel, self._panel = self._panel, None
        self._shot = None
        if annotation is not None:
            annotation.close()
        if window is not None:
            window.close()
        if panel is not None:
            panel.close()
