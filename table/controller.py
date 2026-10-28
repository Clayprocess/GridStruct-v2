"""后台表格分析服务与窗口入口。"""

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from .analysis import analyze
from .dialog import TableDialog


@dataclass
class _Request:
    image: QImage
    results: object
    dialog: object = field(repr=False)
    cancelled: bool = False


class _Worker(QObject):
    stage = Signal(object, str)
    finished = Signal(object, object, str)

    @Slot(object)
    def run(self, request):
        if request.cancelled:
            return
        try:
            result = analyze(request.image, request.results, on_stage=lambda stage: self.stage.emit(request, stage))
        except Exception as exc:
            self.finished.emit(request, None, str(exc))
        else:
            self.finished.emit(request, result, "")


class _Service(QObject):
    """单线程串行：OCR 与 SLANet 会话都不并发调用。"""
    requested = Signal(object)

    def __init__(self, app):
        super().__init__(app)
        self._thread = QThread(self)
        self._worker = _Worker()
        self._worker.moveToThread(self._thread)
        self.requested.connect(self._worker.run)
        self._worker.stage.connect(self._stage)
        self._worker.finished.connect(self._finished)
        self._thread.finished.connect(self._worker.deleteLater)
        app.aboutToQuit.connect(self.shutdown)
        self._thread.start()

    @Slot(object, str)
    def _stage(self, request, stage):
        if not request.cancelled:
            request.dialog.set_stage(stage)

    @Slot(object, object, str)
    def _finished(self, request, result, error):
        if request.cancelled:
            return
        if error:
            request.dialog.show_error(error)
        else:
            request.dialog.apply_analysis(*result)

    def shutdown(self):
        # ONNX 同步调用不能强杀；退出时等它释放模型。
        self._thread.quit()
        self._thread.wait()


_service = None
_open = set()


def open_table(image, results=None):
    """image 为 QImage/QPixmap；results 为同图已有的 OCR 结果，可省一次识别。

    窗口独立于截图/贴图存在：截图会话结束后表格仍可继续校对。
    """
    global _service
    if _service is None:
        _service = _Service(QApplication.instance())
    image = image.toImage() if isinstance(image, QPixmap) else image
    image = image.copy()
    dialog = TableDialog(image)
    if results is not None:
        dialog.set_stage("structure")
    request = _Request(image, results, dialog)
    _open.add(dialog)

    def closed():
        request.cancelled = True
        _open.discard(dialog)

    dialog.destroyed.connect(closed)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    _service.requested.emit(request)
    return dialog
