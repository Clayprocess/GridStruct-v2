"""串行后台 OCR 服务与可取消的窗口任务。"""

from dataclasses import dataclass
from threading import Event
from time import monotonic
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton, QVBoxLayout
from ui import BaseDialog, MessageDialog, show_message
from .engine import OCREngine
from .dialog import OCRResultDialog
from .model import OCRDocument


@dataclass
class _Request:
    image: object
    cancelled: Event
    started_at: float = 0


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


def _get_service():
    global _service
    if _service is None:
        _service = _Service(QApplication.instance())
    return _service


class _ProgressDialog(BaseDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("文字识别")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        progress = QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(progress)
        cancel = QPushButton("取消识别")
        cancel.setProperty("class", "outline")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)


class OCRController(QObject):
    """start(pixmap)；recognized 在确认对话框出现前发出，供画布挂载文字层。"""
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
        self._state = "idle"
        self._request = None
        self._pixmap = None
        self._progress = None
        self._dialogs = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
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
        if self._progress is not None:
            self._progress.label.setText(message)
        self.stateChanged.emit(state)
        self.statusChanged.emit(message, has_results)

    @property
    def running(self):
        return self._request is not None

    def start(self, pixmap):
        if pixmap.isNull():
            self.invalidate("没有可识别的图片")
            self._set_state("failed", "识别失败：没有可识别的图片")
            show_message(self._owner, "文字识别失败", "没有可识别的图片。")
            return
        if self.running:
            self.cancel()
        self._pixmap = pixmap.copy()
        self._results = []
        self._document = None
        self.recognized.emit([])
        request = _Request(pixmap.toImage().copy(), Event())
        self._request = request
        # 窗口销毁后，排队任务与迟到结果均作废。
        self.destroyed.connect(lambda _=None, flag=request.cancelled: flag.set())
        self._progress = _ProgressDialog(self._owner)
        self._progress.rejected.connect(self.cancel)
        active = self._service.active
        message = ("排队中：前一次已取消的推理仍在结束，请稍候…"
                   if active is not None and active.cancelled.is_set()
                   else "排队中：等待本地 OCR 服务…")
        self._set_state("queued", message)
        self._progress.show()
        self._service.requested.emit(request)

    @Slot(object, str)
    def _stage(self, request, stage):
        if request is not self._request or request.cancelled.is_set():
            return
        # 冷加载与推理共用 60 秒预算；排队时间不占用它。
        if not self._timer.isActive():
            elapsed = (monotonic() - request.started_at) * 1000
            if elapsed >= 60000:
                self._timed_out()
                return
            self._timer.start(60000 - int(elapsed))
        message = "首次加载本地模型…" if stage == "loading" else "正在本地识别文字…"
        self._set_state(stage, message)

    def cancel(self):
        if self._request is None:
            return
        started = bool(self._request.started_at)
        self._request.cancelled.set()
        self._end_wait()
        message = ("已取消识别，可重新识别；运行中的推理将在后台结束"
                   if started else "已取消排队，可重新识别")
        self._set_state("cancelled", message)
        self.cancelled.emit()

    def _end_wait(self):
        self._request = None
        self._timer.stop()
        progress, self._progress = self._progress, None
        if progress is not None:
            progress.accept()
            progress.deleteLater()
            # Tool 进度窗关闭时 Windows 可能把焦点还给底层截图遮罩。
            if self._owner is not None and self._owner.isVisible():
                self._owner.raise_()
                self._owner.activateWindow()

    def _timed_out(self):
        self._request.cancelled.set()
        self._end_wait()
        message = "识别超过 60 秒，本次结果已取消。后台推理结束后可重新识别，原图仍然保留。"
        self._set_state("timeout", "识别超时，可重新识别；后台推理仍需结束")
        self.failed.emit(message)
        show_message(self._owner, "识别超时", message)

    @Slot(object, object, str)
    def _finished(self, request, results, error):
        if request is not self._request or request.cancelled.is_set():
            return
        # GUI 繁忙时完成信号可能先于计时器送达，仍按实际执行预算判定。
        if request.started_at and monotonic() - request.started_at >= 60:
            self._timed_out()
            return
        self._end_wait()
        if error:
            self._set_state("failed", "识别失败，请查看错误提示后重试")
            self.failed.emit(error)
            show_message(self._owner, "文字识别失败", error)
            return
        self._document = OCRDocument(results, request.image)
        self._results = self._document.results
        self.recognized.emit(self._document)
        # 接收窗口可能在挂层前检查图像版本并拒绝这次结果。
        if self._document is None:
            return
        if self._document:
            self._set_state("completed", f"已识别 {len(self._document)} 处文字：可在原图滑选，Ctrl+C 复制", True)
        else:
            self._set_state("empty", "未检测到文字，可调整选区后重新识别")
        from settings import get_settings
        settings = get_settings()
        if not self._document:
            if not settings.value("ocr/hide_empty_notice", False, type=bool):
                notice = MessageDialog(self._owner, "未检测到文字", "此图片中没有检测到文字区域。",
                                       checkbox_text="以后不再提示")
                notice.exec()
                settings.setValue("ocr/hide_empty_notice", notice.checkbox.isChecked())
            return
        if settings.value("ocr/direct_copy", False, type=bool):
            QApplication.clipboard().setText(self._document.text())
            return
        if not self._layer_first:
            self.show_results()

    def show_results(self):
        """从原图文字层或菜单主动打开可编辑全文。"""
        if self._document is None or not self._document:
            return
        if self._dialogs and self._dialogs[-1]._document is self._document:
            self._dialogs[-1].show()
            self._dialogs[-1].raise_()
            self._dialogs[-1].activateWindow()
            return
        from PySide6.QtGui import QPixmap
        image = self._document.image
        dialog = OCRResultDialog(self._document, self._owner,
                                 QPixmap.fromImage(image) if not image.isNull() else self._pixmap)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        self._dialogs.append(dialog)
        dialog.destroyed.connect(lambda: self._dialogs.remove(dialog))
        dialog.show()

    def adopt_document(self, document, pixmap):
        """同图转移结果，无需重新识别。"""
        if not document.matches(pixmap):
            self.cancel()
            self._set_state("idle", "图像不匹配，未复用旧 OCR；原校正仍保留，请重新识别")
            return False
        self.cancel()
        self._pixmap = pixmap.copy()
        self._document = document
        self._results = document.results
        self.recognized.emit(document)
        if self._document is None:
            return False
        if document:
            self._set_state("completed", "已恢复文字与人工校正：可在原图复核、复制", True)
        else:
            self._set_state("empty", "未检测到文字，可调整选区后重新识别")
        return True

    def invalidate(self, message="图像已改变，请重新识别文字"):
        self.cancel()
        self._document = None
        self._results = []
        self.recognized.emit([])
        self._set_state("idle", message)
