"""串行后台 OCR 服务与可取消的窗口任务。"""

from dataclasses import dataclass
from enum import Enum
from threading import Event
from time import monotonic
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication
from .engine import OCREngine
from .dialog import OCRResultDialog
from .layout import saved_layout
from .model import OCRDocument


class OCRState(str, Enum):
    """一次识别任务所处的状态，只由 OCRController 切换。

    值仍是原来的英文字符串：旧的字符串比较、stateChanged 收到的文本和验收脚本都照常可用。
    """
    IDLE = "idle"                # 没有任务，或结果已作废
    QUEUED = "queued"            # 排队等后台 OCR 服务
    LOADING = "loading"          # 首次加载本地模型
    RECOGNIZING = "recognizing"  # 正在识别
    COMPLETED = "completed"      # 有结果
    EMPTY = "empty"              # 识别完但没有文字
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    def __str__(self):
        # Python 3.12 起 f-string 会写成 "OCRState.QUEUED"；诊断日志等文本仍要原来的值。
        return self.value

    @property
    def waiting(self):
        """还在等结果：界面显示转圈和“取消”。"""
        return self in (OCRState.QUEUED, OCRState.LOADING, OCRState.RECOGNIZING)

    @property
    def retryable(self):
        """没拿到结果、可以点“重试”。"""
        return self in (OCRState.FAILED, OCRState.TIMEOUT, OCRState.CANCELLED)


@dataclass
class _Request:
    image: object
    cancelled: Event
    started_at: float = 0
    stage: OCRState = OCRState.QUEUED


class _Worker(QObject):
    finished = Signal(object, object, str)
    stage = Signal(object, str)

    def _stage(self, request, stage):
        if not request.started_at:
            request.started_at = monotonic()
        self.stage.emit(request, stage)

    @Slot(object)
    def recognize(self, request):
        if request.cancelled.is_set():
            return
        try:
            results = OCREngine().recognize_image(
                request.image, on_stage=lambda stage: self._stage(request, stage))
        except Exception as exc:
            self.finished.emit(request, [], str(exc))
        else:
            self.finished.emit(request, results, "")


class _Service(QObject):
    requested = Signal(object)
    finished = Signal(object, object, str)
    stage = Signal(object, str)

    def __init__(self, app):
        super().__init__(app)
        self._thread = QThread(self)
        self._worker = _Worker()
        self.active = None
        self._worker.moveToThread(self._thread)
        self.requested.connect(self._worker.recognize)
        self._worker.stage.connect(self._stage)
        self._worker.finished.connect(self._finished)
        self._thread.finished.connect(self._worker.deleteLater)
        app.aboutToQuit.connect(self.shutdown)
        self._thread.start()

    @Slot(object, str)
    def _stage(self, request, stage):
        self.active = request
        self.stage.emit(request, stage)

    @Slot(object, object, str)
    def _finished(self, request, results, error):
        self.active = None
        self.finished.emit(request, results, error)

    def shutdown(self):
        # ONNX 同步调用不能安全强杀；退出时等待它释放共享模型。
        self._thread.quit()
        self._thread.wait()


_service = None
_windows = set()


def _get_service():
    global _service
    if _service is None:
        _service = _Service(QApplication.instance())
    return _service


# 冷加载、推理、排队各给 60 秒：办公电脑首次加载模型常被杀毒扫描拖慢，分开计时不误判。
_STAGE_BUDGET_MS = 60000
_STAGE_TEXT = {OCRState.QUEUED: "排队中：等待本地 OCR 服务", OCRState.LOADING: "首次加载本地模型",
               OCRState.RECOGNIZING: "正在本地识别文字"}


def _write_diagnostic(reason, request):
    """超时时把所有线程的调用栈写进 user-data/logs，实机卡住时能看出停在哪一步。"""
    import faulthandler
    from datetime import datetime
    from settings import data_directory
    folder = data_directory() / "logs"
    try:
        folder.mkdir(exist_ok=True)
        path = folder / f"ocr-{datetime.now():%Y%m%d-%H%M%S}.txt"
        with path.open("w", encoding="utf-8") as stream:
            stream.write(f"{reason}\n阶段：{request.stage}\n")
            if request.started_at:
                stream.write(f"开始执行后 {monotonic() - request.started_at:.1f} 秒\n")
            stream.write("\n")
            stream.flush()
            faulthandler.dump_traceback(stream, all_threads=True)
    except OSError:
        return None
    return path


class OCRController(QObject):
    """start(pixmap)；recognized 在复核窗口出现前发出，供画布挂载文字层。

    等待、失败、超时都只发 statusChanged，由所在窗口在界面内展示，不弹窗打断截图。
    """
    recognized = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    statusChanged = Signal(str, bool)
    stateChanged = Signal(str)

    def __init__(self, parent=None, *, layer_first=False):
        super().__init__(parent)
        self._owner = parent
        self._layer_first = layer_first
        self._results = []
        self._document = None
        self._state = OCRState.IDLE
        self._request = None
        self._pixmap = None
        self._dialogs = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        # 不知道总耗时，只如实显示已等待秒数，不伪造进度。
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._show_wait)
        self._stage_started = 0.0
        self._behind_cancelled = False
        self._automatic = False
        self._service = _get_service()
        self._service.finished.connect(self._finished)
        self._service.stage.connect(self._stage)

    @property
    def document(self):
        return self._document

    @property
    def state(self):
        return self._state

    def _set_state(self, state, message, has_results=False):
        self._state = state
        self.stateChanged.emit(state)
        self.statusChanged.emit(message, has_results)

    @property
    def running(self):
        return self._request is not None

    def start(self, pixmap, *, automatic=False):
        """automatic：贴图后自动识别，不替用户改剪贴板、不弹复核窗口。"""
        if pixmap.isNull():
            self.invalidate("没有可识别的图片")
            self._set_state(OCRState.FAILED, "识别失败：没有可识别的图片")
            return
        if self.running:
            self.cancel()
        self._pixmap = pixmap.copy()
        self._results = []
        self._document = None
        self._automatic = automatic
        self.recognized.emit([])
        request = _Request(pixmap.toImage().copy(), Event())
        self._request = request
        # 窗口销毁后，排队任务与迟到结果均作废。
        self.destroyed.connect(lambda _=None, flag=request.cancelled: flag.set())
        active = self._service.active
        self._behind_cancelled = active is not None and active.cancelled.is_set()
        self._enter_stage(OCRState.QUEUED)
        self._service.requested.emit(request)

    def _enter_stage(self, stage):
        # 引擎在后台线程按字符串报阶段（"loading"/"recognizing"）。
        stage = OCRState(stage)
        self._request.stage = stage
        self._stage_started = monotonic()
        self._timer.start(_STAGE_BUDGET_MS)
        self._ticker.start()
        self._state = stage
        self.stateChanged.emit(stage)
        self._show_wait()

    def _show_wait(self):
        if self._request is None:
            return
        stage = self._request.stage
        text = _STAGE_TEXT[stage]
        if stage is OCRState.QUEUED and self._behind_cancelled:
            text = "排队中：前一次已取消的推理仍在结束"
        seconds = int(monotonic() - self._stage_started)
        self.statusChanged.emit(f"{text}…" + (f" 已等待 {seconds} 秒" if seconds >= 3 else ""), False)

    @Slot(object, str)
    def _stage(self, request, stage):
        if request is not self._request or request.cancelled.is_set():
            return
        self._enter_stage(stage)

    def cancel(self):
        if self._request is None:
            return
        started = bool(self._request.started_at)
        self._request.cancelled.set()
        self._end_wait()
        message = ("已取消识别，可重新识别；运行中的推理将在后台结束"
                   if started else "已取消排队，可重新识别")
        self._set_state(OCRState.CANCELLED, message)
        self.cancelled.emit()

    def _end_wait(self):
        self._request = None
        self._timer.stop()
        self._ticker.stop()

    def _timed_out(self):
        request = self._request
        request.cancelled.set()
        self._end_wait()
        if request.stage is OCRState.QUEUED:
            reason = "等待本地 OCR 服务超过 60 秒"
        elif request.stage is OCRState.LOADING:
            reason = "加载本地模型超过 60 秒"
        else:
            reason = "识别超过 60 秒"
        path = _write_diagnostic(reason, request)
        note = f"，诊断信息已存到 user-data/logs/{path.name}" if path is not None else ""
        message = f"{reason}，本次已停止等待。可点“重试”{note}；多次超时请重启程序。"
        self._set_state(OCRState.TIMEOUT, message)
        self.failed.emit(message)

    @Slot(object, object, str)
    def _finished(self, request, results, error):
        if request is not self._request or request.cancelled.is_set():
            return
        # GUI 繁忙时完成信号可能先于计时器送达，仍按实际执行预算判定。
        if monotonic() - self._stage_started >= _STAGE_BUDGET_MS / 1000:
            self._timed_out()
            return
        self._end_wait()
        if error:
            self._set_state(OCRState.FAILED, f"识别失败：{error}")
            self.failed.emit(error)
            return
        self._document = OCRDocument(results, request.image)
        self._results = self._document.results
        self.recognized.emit(self._document)
        # 接收窗口可能在挂层前检查图像版本并拒绝这次结果。
        if self._document is None:
            return
        if not self._document:
            self._set_state(OCRState.EMPTY, "未检测到文字，可调整选区后重新识别")
            return
        self._set_state(OCRState.COMPLETED, f"已识别 {len(self._document)} 处文字：可在原图滑选，Ctrl+C 复制", True)
        if self._automatic:
            return
        from settings import get_settings
        settings = get_settings()
        if settings.value("ocr/direct_copy", False, type=bool):
            self._document.set_layout(saved_layout(settings))
            QApplication.clipboard().setText(self._document.text())
            return
        if not self._layer_first:
            self.show_results()

    def show_results(self):
        """从原图文字层或菜单主动打开识别窗口。

        窗口是独立的标准窗口（任务栏有图标），不挂在截图或贴图下：截图会话打开它后随即结束，
        关掉贴图也不跟着关。
        """
        if self._document is None or not self._document:
            return
        if self._dialogs and self._dialogs[-1]._document is self._document:
            dialog = self._dialogs[-1]
            if dialog.isMinimized():
                dialog.setWindowState(dialog.windowState() & ~Qt.WindowMinimized)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return
        from PySide6.QtGui import QPixmap
        image = self._document.image
        dialog = OCRResultDialog(self._document, None,
                                 QPixmap.fromImage(image) if not image.isNull() else self._pixmap)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        # 没有父对象的窗口要有人持有 Python 引用，否则会被回收。
        _windows.add(dialog)
        self._dialogs.append(dialog)
        dialog.destroyed.connect(lambda: (_windows.discard(dialog), self._dialogs.remove(dialog)))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def adopt_document(self, document, pixmap):
        """同图转移结果，无需重新识别。"""
        if not document.matches(pixmap):
            self.cancel()
            self._set_state(OCRState.IDLE, "图像不匹配，未复用旧 OCR；已改的文字仍保留，请重新识别")
            return False
        self.cancel()
        self._pixmap = pixmap.copy()
        self._document = document
        self._results = document.results
        self.recognized.emit(document)
        if self._document is None:
            return False
        if document:
            self._set_state(OCRState.COMPLETED, "已恢复识别文字：可在原图滑选、Ctrl+C 复制", True)
        else:
            self._set_state(OCRState.EMPTY, "未检测到文字，可调整选区后重新识别")
        return True

    def invalidate(self, message="图像已改变，请重新识别文字"):
        self.cancel()
        self._document = None
        self._results = []
        self.recognized.emit([])
        self._set_state(OCRState.IDLE, message)
