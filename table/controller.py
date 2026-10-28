"""后台表格分析服务与窗口入口。"""

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, QThread, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication, QFileDialog

from ui import show_message
from .analysis import analyze
from .dialog import TableDialog
from .pages import OPEN_FILTER, FilePage, ImagePage, collect_pages


@dataclass
class _Request:
    image: QImage
    results: object
    dialog: object = field(repr=False)
    page: int = 0
    crop: bool = False
    cancelled: bool = False


class _Worker(QObject):
    stage = Signal(object, str)
    finished = Signal(object, object, str)

    @Slot(object)
    def run(self, request):
        if request.cancelled:
            return
        try:
            result = analyze(request.image, request.results, on_stage=lambda stage: self.stage.emit(request, stage),
                             crop=request.crop)
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
            request.dialog.set_stage(stage, request.page)

    @Slot(object, object, str)
    def _finished(self, request, result, error):
        if request.cancelled:
            return
        if error:
            request.dialog.show_error(error, request.page)
        else:
            request.dialog.apply_analysis(*result, page=request.page)
        _submit_next(request.dialog)

    def shutdown(self):
        # ONNX 同步调用不能强杀；退出时等它释放模型。
        self._thread.quit()
        self._thread.wait()


_service = None
_active = {}  # 窗口 -> 正在分析的请求；关窗时作废


def _submit_next(dialog):
    """多页逐页送去分析，一次只占一页图像的内存。"""
    index = dialog.take_next_page()
    if index is None:
        _active.pop(dialog, None)
        return
    page = dialog.pages[index]
    try:
        image = page.render().copy()
    except ValueError as exc:
        dialog.show_error(str(exc), index)
        _submit_next(dialog)
        return
    if page.results is not None:
        dialog.set_stage("structure", index)
    request = _Request(image, page.results, dialog, index, crop=isinstance(page, FilePage))
    _active[dialog] = request
    _service.requested.emit(request)


def _open(pages, title=None):
    global _service
    if _service is None:
        _service = _Service(QApplication.instance())
    dialog = TableDialog(pages)
    if title:
        dialog.setWindowTitle(title)

    def closed():
        request = _active.pop(dialog, None)
        if request is not None:
            request.cancelled = True

    dialog.destroyed.connect(closed)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    _submit_next(dialog)
    return dialog


def open_table(image, results=None):
    """image 为 QImage/QPixmap；results 为同图已有的 OCR 结果，可省一次识别。

    窗口独立于截图/贴图存在：截图会话结束后表格仍可继续校对。
    """
    image = image.toImage() if isinstance(image, QPixmap) else image
    return _open([ImagePage(image.copy(), results)])


_choosing = False


def open_document(parent=None):
    """识别文档：选图片或 PDF，按表格识别；多页逐页改网格，输出时合成一张表。"""
    global _choosing
    # 托盘和快捷键都能触发；选文件对话框还开着时不再叠一个。
    if _choosing:
        return None
    from settings import get_settings
    settings = get_settings()
    directory = settings.value("document/open_dir", "") or QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    _choosing = True
    try:
        paths, _ = QFileDialog.getOpenFileNames(parent, "识别文档", directory, OPEN_FILTER)
    finally:
        _choosing = False
    if not paths:
        return None
    settings.setValue("document/open_dir", str(Path(paths[0]).parent))
    pages, problems = collect_pages(paths, QApplication.instance())
    if not pages:
        show_message(parent, "无法识别文档", "\n".join(problems) or "所选文件里没有可识别的页面。")
        return None
    title = f"{Path(paths[0]).name}{' 等' if len(paths) > 1 else ''} · 识别文档"
    dialog = _open(pages, title)
    for pdf in {page.source for page in pages if isinstance(page.source, QPdfDocument)}:
        # PDF 句柄跟着窗口走，关窗即释放文件。
        pdf.setParent(dialog)
    if problems:
        show_message(dialog, "部分文件已跳过", "\n".join(problems))
    return dialog
