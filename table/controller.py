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
    tokens: object = None   # 改框后重新分析：整页文字框已有，不再 OCR
    regions: object = None  # 用户框选的区域；None 时自动找表
    cancelled: bool = False


class _Worker(QObject):
    stage = Signal(object, str)
    recognized = Signal(object, object)
    finished = Signal(object, object, str)

    @Slot(object)
    def run(self, request):
        if request.cancelled:
            return
        try:
            result = analyze(request.image, request.results, on_stage=lambda stage: self.stage.emit(request, stage),
                             tokens=request.tokens, regions=request.regions,
                             on_results=lambda results: self.recognized.emit(request, results))
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
        self._worker.recognized.connect(self._recognized)
        self._worker.finished.connect(self._finished)
        self._thread.finished.connect(self._worker.deleteLater)
        app.aboutToQuit.connect(self.shutdown)
        self._thread.start()

    @Slot(object, str)
    def _stage(self, request, stage):
        if not request.cancelled:
            request.dialog.set_stage(stage, request.page)

    @Slot(object, object)
    def _recognized(self, request, results):
        if request.cancelled:
            return
        # 结构失败也保留首次识别的真实结果，重试和模式切换都不必再跑 OCR。
        request.dialog.apply_text_results(results, request.page, request.image)

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
    global _service
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
    tokens, regions = dialog.page_request(index)
    if page.results is not None or tokens is not None:
        dialog.set_stage("structure", index)
    request = _Request(image, page.results, dialog, index, tokens=tokens, regions=regions)
    _active[dialog] = request
    if _service is None:
        _service = _Service(QApplication.instance())
    _service.requested.emit(request)


def _connect_analysis(dialog):
    def closed():
        request = _active.pop(dialog, None)
        if request is not None:
            request.cancelled = True

    dialog.destroyed.connect(closed)
    # 文字入口首次切到表格时才发这个信号；改框和多页仍沿用同一个串行队列。
    dialog.analysisRequested.connect(lambda: None if dialog in _active else _submit_next(dialog))


def _open(pages, title=None, *, text_document=None):
    dialog = TableDialog(pages, text_document=text_document, initial_mode="table")
    if title:
        dialog.setWindowTitle(title)
    _connect_analysis(dialog)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    _submit_next(dialog)
    return dialog


def open_text_workbench(document, pixmap=None, parent=None):
    """复用已有文字文档构造共享窗口；由调用方显示，首次切表格才开始结构分析。"""
    image = document.image if pixmap is None else pixmap.toImage()
    pages = [] if image.isNull() else [ImagePage(image.copy(), document.raw_results)]
    dialog = TableDialog(pages, parent, text_document=document)
    _connect_analysis(dialog)
    return dialog


def open_table(image, results=None, *, text_document=None):
    """image 为 QImage/QPixmap；results 为同图已有的 OCR 结果，可省一次识别。

    text_document 复用同图已有的全文草稿；结构分析始终使用 results 的原始证据。
    窗口独立于截图/贴图存在：截图会话结束后表格仍可继续校对。
    """
    image = image.toImage() if isinstance(image, QPixmap) else image
    if results is None and text_document is not None:
        results = text_document.raw_results
    return _open([ImagePage(image.copy(), results)], text_document=text_document)


_choosing = False


def open_document(parent=None, paths=None):
    """识别文档：选图片或 PDF，每页自动框出表格，逐张改网格，导出时一张表一个工作表。

    paths 给了就直接打开（独立 EXE 收到拖放或“打开方式”传入的文件），否则弹出选文件对话框。
    """
    global _choosing
    from settings import get_settings
    settings = get_settings()
    if not paths:
        # 托盘和快捷键都能触发；选文件对话框还开着时不再叠一个。
        if _choosing:
            return None
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
