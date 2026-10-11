# 截图流程编排：截图 -> 框选 -> 工具条 -> 复制/确认。
# P0-A 垂直切片拥有者。标注工具的具体实现后续挂到 FloatPanel.toolSelected 上。

from PySide6.QtCore import QObject, QRect, QTimer
from PySide6.QtWidgets import QApplication

from .screen import capture_virtual_desktop
from .overlay import ScreenShotWindow
from annotate import AnnotationWindow, FloatPanel
from ui import show_message


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
        self._longshot = None
        self._longshot_requested = False
        self._pending_longshot_region = QRect()
        self._longshot_timer = QTimer(self)
        self._longshot_timer.setSingleShot(True)
        self._longshot_timer.setInterval(100)
        self._longshot_timer.timeout.connect(self.start_pending_longshot)

    def start(self):
        if self._win is not None or self._ann is not None or self._longshot_requested or (self._longshot is not None and self._longshot.is_active):
            return  # 已有会话在跑
        try:
            self._shot = capture_virtual_desktop()
        except (RuntimeError, OSError) as exc:
            show_message(None, "无法截图", str(exc))
            return
        self._win = ScreenShotWindow(self._shot, keep_open=True)
        self._win.regionSelected.connect(self._on_region)
        self._win.selectionChanged.connect(self._on_selection)
        self._win.actionRequested.connect(self._trigger_action)
        self._win.pipettePicked.connect(self._pick_color)
        self._win.cancelled.connect(self._cleanup)
        self._win.show()

    def _on_selection(self, rect):
        if rect.width() < 4 or rect.height() < 4:
            if self._panel is not None:
                self._panel.hide()
            return
        if self._panel is None:
            self._panel = FloatPanel(self._app.property("theme_mode") or self._mode)
            self._win.route_keyboard(self._panel)
            self._panel.toolSelected.connect(self._select_tool)
            self._panel.actionTriggered.connect(self._trigger_action)
        self._panel.set_position_near(rect)
        self._panel.show()

    def _select_tool(self, tool):
        if self._win.confirm():
            self._ann.select_tool(tool)

    def _trigger_action(self, action):
        if action.startswith("tool:"):
            self._select_tool(action[5:])
            return
        if action == "pipette":
            self._win.start_pipette()
            return
        if action == "longshot":
            self._longshot_requested = True
            if not self._win.confirm():
                self._longshot_requested = False
            return
        if self._win.confirm():
            self._ann.trigger_action(action)

    def _pick_color(self, color):
        from annotate import ColorDialog
        dialog = ColorDialog(color, QApplication.activeWindow())
        dialog.apply_color.setChecked(False)
        dialog.apply_color.hide()
        if dialog.exec() == ColorDialog.Accepted:
            QApplication.clipboard().setText(dialog.value.text())
        dialog.deleteLater()

    def start_longshot(self, region=None):
        """托盘、工具条和热键共享同一个长截图会话。"""
        if self._longshot_requested or (self._longshot is not None and self._longshot.is_active):
            return
        if region is not None:
            self._cleanup()
            self._longshot_requested = True
            self._pending_longshot_region = QRect(region)
            # 等桌面合成器移除旧标注窗口后，长截图才能取得干净的首帧。
            self._longshot_timer.start()
            return
        if self._ann is not None:
            return
        if self._win is not None:
            self._trigger_action("longshot")
            return
        self._get_longshot().start()

    def start_pending_longshot(self):
        """延迟的用户启动入口；捕获异常在这一公开操作边界提示。"""
        from ui import show_message
        self._longshot_requested = False
        try:
            capture = capture_virtual_desktop()
        except (RuntimeError, OSError) as exc:
            show_message(None, "无法开始长截图", str(exc))
            return
        self._get_longshot().start_region(capture, self._pending_longshot_region)

    def _get_longshot(self):
        if self._longshot is None:
            from .longshot import LongShotFlow
            self._longshot = LongShotFlow(self._app, self._on_longshot_result)
        return self._longshot

    def _on_longshot_result(self, pixmap):
        mode = self._app.property("theme_mode") or self._mode
        self._ann = AnnotationWindow(pixmap, self._longshot.region.topLeft(), mode, scrollable=True,
                                     capture_region=self._longshot.region)
        self._ann.finished.connect(self._on_finished)
        self._ann.cancelled.connect(self._cleanup)
        self._ann.longshotRequested.connect(self.start_longshot)
        self._ann.show()
        self._ann.raise_()
        self._ann.activateWindow()

    # ---------- 内部 ----------

    def _on_region(self, rect: QRect):
        capture = self._win.capture
        if self._longshot_requested:
            self._longshot_requested = False
            self._cleanup()
            self._get_longshot().start_region(capture, rect)
            return
        cropped = capture.crop(rect)
        crosses_screens = sum(frame.geometry.intersects(rect) for frame in capture.frames) > 1

        if self._panel is not None:
            self._panel.close()
            self._panel = None
        self._shot = None

        # 标注窗口放在选区原位置
        position = rect.topLeft()
        mode = self._app.property("theme_mode") or self._mode
        # 跨屏导出保留各屏原生像素，单个标注视口不能再假定统一的屏幕 DPR。
        self._ann = AnnotationWindow(cropped, position, mode, scrollable=crosses_screens,
                                     capture_region=rect)
        self._ann.finished.connect(self._on_finished)
        self._ann.cancelled.connect(self._cleanup)
        self._ann.longshotRequested.connect(self.start_longshot)
        self._ann.show()
        self._ann.raise_()
        self._ann.activateWindow()

    def _on_finished(self, pixmap):
        QApplication.clipboard().setPixmap(pixmap)
        print(f"[截图] 已复制 {pixmap.width()}×{pixmap.height()} 到剪贴板")
        self._cleanup()

    def cancel(self):
        self._longshot_timer.stop()
        self._longshot_requested = False
        self._cleanup()
        if self._longshot is not None:
            self._longshot.cancel()

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
