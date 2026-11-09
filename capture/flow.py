# 截图流程编排：截图 -> 框选 -> 工具条 -> 复制/确认。
# P0-A 垂直切片拥有者。标注工具的具体实现后续挂到 FloatPanel.toolSelected 上。

from enum import Enum, auto

from PySide6.QtCore import QObject, QRect, QTimer
from PySide6.QtWidgets import QApplication

from .screen import capture_virtual_desktop
from .overlay import ScreenShotWindow
from annotate import AnnotationWindow, FloatPanel
from ui import show_message, show_toast


class Phase(Enum):
    """一次截图会话所处的阶段；只在 ScreenshotFlow 里切换，同一时刻只有一个。"""
    IDLE = auto()              # 没有会话，热键/托盘可以开始新截图
    SELECTING = auto()         # 遮罩上框选，工具条预建
    ANNOTATING = auto()        # 标注窗口打开（普通截图或长截图结果）
    LONGSHOT_PENDING = auto()  # 已决定做长截图：等选区确认，或等旧标注窗口从桌面消失
    LONGSHOT = auto()          # 长截图会话（框选或控制条）在跑


class ScreenshotFlow(QObject):
    """一次截图会话：截图 -> 框选 -> 标注 -> 复制/保存。start() 后全程信号驱动。"""

    def __init__(self, app: QApplication, theme_mode: str = "light", *, hotkeys=None):
        super().__init__(app)
        self._app = app
        self._mode = theme_mode
        self._win: ScreenShotWindow | None = None
        self._ann: AnnotationWindow | None = None
        self._shot = None
        self._panel = None
        self._longshot = None
        self._hotkeys = hotkeys
        self._longshot_escape = None
        self._phase = Phase.IDLE
        self._pending_longshot_region = QRect()
        self._longshot_timer = QTimer(self)
        self._longshot_timer.setSingleShot(True)
        self._longshot_timer.setInterval(100)
        self._longshot_timer.timeout.connect(self.start_pending_longshot)

    def start(self):
        if self._phase is not Phase.IDLE:
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
        self._win.cancelled.connect(self._cleanup)
        self._phase = Phase.SELECTING
        self._win.show()
        # 工具条有二十多个按钮和图标，现场创建要几十毫秒；以前在刚拖出选区时创建，拖动会卡一下。
        # 遮罩先画出来，再趁用户还没开始拖时建好；人的反应时间远大于这 100ms。
        QTimer.singleShot(100, self._prepare_panel)

    def _prepare_panel(self):
        if self._win is None or self._panel is not None:
            return
        self._panel = FloatPanel(self._app.property("theme_mode") or self._mode)
        self._win.route_keyboard(self._panel)
        self._panel.toolSelected.connect(self._select_tool)
        self._panel.actionTriggered.connect(self._trigger_action)
        # 提前建好原生窗口，第一次显示时不再付这笔开销。
        self._panel.winId()

    def _on_selection(self, rect):
        self._prepare_panel()
        if rect.width() < 4 or rect.height() < 4 or self._win.dragging():
            self._panel.hide()
            return
        self._panel.set_position_near(rect)
        self._panel.show()

    def _select_tool(self, tool):
        if self._win.confirm():
            self._ann.select_tool(tool)

    def _trigger_action(self, action):
        if action == "escape":
            if self._ann is not None:
                self._ann.trigger_action(action)
            else:
                # 标注创建失败时仍可退出已经冻结的遮罩。
                self._cleanup()
            return
        if action.startswith("tool:"):
            self._select_tool(action[5:])
            return
        if action == "cancel":
            # 与 Esc 一致：框选阶段直接退出，不先转成标注窗口再问。
            self._win.close()
            return
        if action == "longshot":
            # confirm() 同步发出 regionSelected，_on_region 按这个阶段改走长截图。
            self._phase = Phase.LONGSHOT_PENDING
            if not self._win.confirm():
                self._phase = Phase.SELECTING
            return
        if self._win.confirm():
            self._ann.trigger_action(action)

    def start_longshot(self, region=None):
        """托盘、工具条和热键共享同一个长截图会话。"""
        if region is not None:
            # 标注窗口里点“长截图”（已按 Esc 的规则确认过）。
            if self._phase is not Phase.ANNOTATING:
                return
            self._cleanup()
            self._phase = Phase.LONGSHOT_PENDING
            self._pending_longshot_region = QRect(region)
            # 等桌面合成器移除旧标注窗口后，长截图才能取得干净的首帧。
            self._longshot_timer.start()
            return
        if self._phase is Phase.SELECTING:
            self._trigger_action("longshot")
        elif self._phase is Phase.IDLE:
            self._phase = Phase.LONGSHOT
            self._get_longshot().start()
            self._settle_longshot()

    def start_pending_longshot(self):
        """延迟的用户启动入口；捕获异常在这一公开操作边界提示。"""
        from ui import show_message
        try:
            capture = capture_virtual_desktop()
        except (RuntimeError, OSError) as exc:
            self._phase = Phase.IDLE
            show_message(None, "无法开始长截图", str(exc))
            return
        self._phase = Phase.LONGSHOT
        self._get_longshot().start_region(capture, self._pending_longshot_region)
        self._settle_longshot()

    def _settle_longshot(self):
        # 长截图启动失败（截不到屏、控制条无法排除）时不会发 closed，按它的实际状态落定。
        if not self._longshot.is_active:
            self._phase = Phase.IDLE

    def _get_longshot(self):
        if self._longshot is None:
            from .longshot import LongShotFlow
            self._longshot = LongShotFlow(self._app, self._on_longshot_result)
            self._longshot.controlsShown.connect(self._register_longshot_escape)
            self._longshot.closed.connect(self._longshot_closed)
        return self._longshot

    def _register_longshot_escape(self):
        if self._hotkeys is None:
            return
        self._longshot_escape = self._hotkeys.register_escape(self._escape_longshot)
        if self._longshot_escape is None:
            show_toast("全局 Esc 不可用；请点击长截图操作条后按 Esc，或点“取消”。")

    def _escape_longshot(self):
        # 全局热键会先于 Qt 弹出层收到 Esc，保留先收菜单/对话框、再退出会话的顺序。
        transient = self._app.activePopupWidget() or self._app.activeModalWidget()
        if transient is not None:
            transient.close()
        else:
            self._longshot.cancel()

    def _longshot_closed(self):
        if self._longshot_escape is not None:
            self._hotkeys.release(self._longshot_escape)
            self._longshot_escape = None
        # 交付结果时先关长截图再开标注窗口，_on_longshot_result 随后把阶段改成 ANNOTATING。
        if self._phase is Phase.LONGSHOT:
            self._phase = Phase.IDLE

    def _on_longshot_result(self, pixmap):
        mode = self._app.property("theme_mode") or self._mode
        self._ann = AnnotationWindow(pixmap, self._longshot.region.topLeft(), mode, scrollable=True,
                                     capture_region=self._longshot.region)
        self._ann.finished.connect(self._on_finished)
        self._ann.cancelled.connect(self._cleanup)
        self._ann.longshotRequested.connect(self.start_longshot)
        self._phase = Phase.ANNOTATING
        self._ann.show()
        self._ann.raise_()
        self._ann.activateWindow()

    # ---------- 内部 ----------

    def _on_region(self, rect: QRect):
        capture = self._win.capture
        if self._phase is Phase.LONGSHOT_PENDING:
            self._cleanup()
            self._phase = Phase.LONGSHOT
            self._get_longshot().start_region(capture, rect)
            self._settle_longshot()
            return
        cropped = capture.crop(rect)
        crosses_screens = sum(frame.geometry.intersects(rect) for frame in capture.frames) > 1

        toolbar_pos = None
        if self._panel is not None:
            toolbar_pos = self._panel.manual_position()
            self._panel.close()
            self._panel = None
        self._shot = None

        # 标注窗口放在选区原位置
        position = rect.topLeft()
        mode = self._app.property("theme_mode") or self._mode
        # 跨屏导出保留各屏原生像素，单个标注视口不能再假定统一的屏幕 DPR。
        self._ann = AnnotationWindow(cropped, position, mode, scrollable=crosses_screens,
                                     capture_region=rect, toolbar_pos=toolbar_pos)
        self._ann.finished.connect(self._on_finished)
        self._ann.cancelled.connect(self._cleanup)
        self._ann.longshotRequested.connect(self.start_longshot)
        self._phase = Phase.ANNOTATING
        self._ann.show()
        self._ann.raise_()
        self._ann.activateWindow()

    def _on_finished(self, pixmap):
        QApplication.clipboard().setPixmap(pixmap)
        show_toast("已复制截图")
        self._cleanup()

    def cancel(self):
        self._longshot_timer.stop()
        self._cleanup()
        if self._longshot is not None:
            self._longshot.cancel()

    def _cleanup(self):
        # closeEvent 也会发出取消信号，先释放会话引用避免重入。
        annotation, self._ann = self._ann, None
        window, self._win = self._win, None
        panel, self._panel = self._panel, None
        self._shot = None
        if self._phase is not Phase.LONGSHOT:
            # 长截图在跑时这里只收掉截图会话残留的窗口，阶段等长截图 closed 再回到空闲。
            self._phase = Phase.IDLE
        if annotation is not None:
            annotation.close()
        if window is not None:
            window.close()
        if panel is not None:
            panel.close()
