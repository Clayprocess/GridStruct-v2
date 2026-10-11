"""串行后台 OCR 服务与可取消的窗口任务。"""

from dataclasses import dataclass
from threading import Event
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout
from ui import BaseDialog
from .engine import OCREngine
from .dialog import OCRResultDialog
from .layout import format_text


@dataclass
class _Request:
    image: object
    cancelled: Event


class _Worker(QObject):
    finished = Signal(object, object, str)

    @Slot(object)
    def recognize(self, request):
        if request.cancelled.is_set():
            return
        try:
            results = OCREngine().recognize_image(request.image)
        except Exception as exc:
            self.finished.emit(request, [], str(exc))
        else:
            self.finished.emit(request, results, "")


class _Service(QObject):
    requested = Signal(object)
    finished = Signal(object, object, str)

    def __init__(self, app):
        super().__init__(app)
        self._thread = QThread(self)
        self._worker = _Worker()
        self._worker.moveToThread(self._thread)
        self.requested.connect(self._worker.recognize)
        self._worker.finished.connect(self.finished)
        self._thread.finished.connect(self._worker.deleteLater)
        app.aboutToQuit.connect(self.shutdown)
        self._thread.start()

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
        label = QLabel("正在本地识别文字，请稍候…\n首次使用需要加载模型。")
        layout.addWidget(label)
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

    def __init__(self, parent=None, *, layer_first=False):
        super().__init__(parent)
        self._owner = parent
        self._layer_first = layer_first
        self._results = []
        self._request = None
        self._pixmap = None
        self._progress = None
        self._dialogs = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self._service = _get_service()
        self._service.finished.connect(self._finished)

    @property
    def running(self):
        return self._request is not None

    def start(self, pixmap):
        if pixmap.isNull():
            self.statusChanged.emit("识别失败：没有可识别的图片", False)
            QMessageBox.warning(self._owner, "文字识别失败", "没有可识别的图片。")
            return
        if self.running:
            self.cancel()
        self._pixmap = pixmap.copy()
        self._results = []
        request = _Request(pixmap.toImage().copy(), Event())
        self._request = request
        self.statusChanged.emit("正在识别文字，首次使用需要加载模型…", False)
        # 窗口销毁后，排队任务与迟到结果均作废。
        self.destroyed.connect(lambda _=None, flag=request.cancelled: flag.set())
        self._progress = _ProgressDialog(self._owner)
        self._progress.rejected.connect(self.cancel)
        self._progress.show()
        self._timer.start(60000)
        self._service.requested.emit(request)

    def cancel(self):
        if self._request is None:
            return
        self._request.cancelled.set()
        self._end_wait()
        self.statusChanged.emit("已取消识别，可重新识别", False)
        self.cancelled.emit()

    def _end_wait(self):
        self._request = None
        self._timer.stop()
        progress, self._progress = self._progress, None
        if progress is not None:
            progress.accept()
            progress.deleteLater()

    def _timed_out(self):
        self._request.cancelled.set()
        self._end_wait()
        message = "识别超过 60 秒，本次结果已取消。后台推理结束后可重新识别，原图仍然保留。"
        self.statusChanged.emit("识别超时，可重新识别", False)
        self.failed.emit(message)
        QMessageBox.warning(self._owner, "识别超时", message)

    @Slot(object, object, str)
    def _finished(self, request, results, error):
        if request is not self._request or request.cancelled.is_set():
            return
        self._end_wait()
        if error:
            self.statusChanged.emit("识别失败，请查看错误提示后重试", False)
            self.failed.emit(error)
            QMessageBox.warning(self._owner, "文字识别失败", error)
            return
        self._results = results
        self.recognized.emit(results)
        if results:
            self.statusChanged.emit(f"已识别 {len(results)} 处文字：可在原图滑选，Ctrl+C 复制", True)
        else:
            self.statusChanged.emit("未检测到文字，可调整选区后重新识别", False)
        from settings import get_settings
        settings = get_settings()
        if not results:
            if not settings.value("ocr/hide_empty_notice", False, type=bool):
                notice = QMessageBox(QMessageBox.Information, "未检测到文字", "此图片中没有检测到文字区域。", parent=self._owner)
                checkbox = QCheckBox("以后不再提示")
                notice.setCheckBox(checkbox)
                notice.exec()
                settings.setValue("ocr/hide_empty_notice", checkbox.isChecked())
            return
        if settings.value("ocr/direct_copy", False, type=bool):
            QApplication.clipboard().setText(format_text(results))
            return
        if not self._layer_first:
            self.show_results()

    def show_results(self):
        """从原图文字层或菜单主动打开可编辑全文。"""
        if not self._results:
            return
        if self._dialogs and self._dialogs[-1]._results is self._results:
            self._dialogs[-1].show()
            self._dialogs[-1].raise_()
            self._dialogs[-1].activateWindow()
            return
        dialog = OCRResultDialog(self._results, self._owner, self._pixmap)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        self._dialogs.append(dialog)
        dialog.destroyed.connect(lambda: self._dialogs.remove(dialog))
        dialog.show()
