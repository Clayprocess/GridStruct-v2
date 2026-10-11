"""离屏验证真实串行 OCR 服务的排队、阶段、取消和迟到结果。"""

import os
import atexit
from pathlib import Path
import sys
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QRect
from PySide6.QtGui import QFontDatabase, QPixmap
from PySide6.QtWidgets import QApplication, QWidget
from ocr import OCRResult
from ocr import controller as module
from styles import apply_theme, tokens as T


class _Plan:
    def __init__(self, text="结果", *, error="", cold=False, results=None):
        self.text, self.error, self.cold = text, error, cold
        self.load = Event()
        self.infer = Event()
        self.results = results if results is not None else (
            [OCRResult(QRect(20, 20, 120, 24), text, 0.99)] if text else [])


class _Engine:
    def __init__(self):
        self.plans = []
        self.calls = 0

    def recognize_image(self, image, on_stage):
        plan = self.plans[self.calls]
        self.calls += 1
        if plan.cold:
            on_stage("loading")
            if not plan.load.wait(5):
                raise RuntimeError("加载验收屏障未释放")
        on_stage("recognizing")
        if not plan.infer.wait(5):
            raise RuntimeError("推理验收屏障未释放")
        if plan.error:
            raise RuntimeError(plan.error)
        return plan.results


def main():
    app = QApplication(sys.argv)
    # Windows 的离屏插件不自动枚举系统字体。
    for filename in ("arial.ttf", "msyh.ttc"):
        QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    engine = _Engine()
    service = module._Service(app)
    def release_service():
        for plan in engine.plans:
            plan.load.set()
            plan.infer.set()
        service.shutdown()
    atexit.register(release_service)
    notices = []
    settings = type("Settings", (), {"direct": False, "value": lambda self, key, default, **kw:
                                    key == "ocr/hide_empty_notice" or
                                    (key == "ocr/direct_copy" and self.direct) or default})()
    output = Path(__file__).resolve().parents[1] / ".scratch" / "ocr-stages-bcd" / "tasks"
    output.mkdir(parents=True, exist_ok=True)

    def wait_for(predicate, description):
        deadline = monotonic() + 3
        while not predicate():
            app.processEvents()
            if monotonic() > deadline:
                raise AssertionError(description)
            sleep(0.002)
        app.processEvents()

    with (patch.object(module, "_get_service", return_value=service),
          patch.object(module, "OCREngine", return_value=engine),
          patch.object(module, "show_message", side_effect=lambda *args: notices.append(args)),
          patch("settings.get_settings", return_value=settings)):
        owners = [QWidget(), QWidget()]
        first, second = [module.OCRController(owner, layer_first=True) for owner in owners]
        first_states, second_states, recognized, messages = [], [], [], []
        first.stateChanged.connect(first_states.append)
        second.stateChanged.connect(second_states.append)
        first.recognized.connect(recognized.append)
        first.statusChanged.connect(lambda message, ready: messages.append(message))
        pixmap = QPixmap(240, 120)
        pixmap.fill(T.THEMES["light"]["background"])
        cold = _Plan("迟到结果", cold=True)
        current = _Plan("当前结果")
        engine.plans.extend([cold, current])
        first.start(pixmap)
        wait_for(lambda: first.state == "loading", "首次加载状态未到达")
        assert first._timer.isActive() and 59000 <= first._timer.interval() <= 60000
        second.start(pixmap)
        queued = second._request
        assert second.state == "queued" and not second._timer.isActive()
        second.cancel()
        assert queued.cancelled.is_set() and second.state == "cancelled"
        cold.load.set()
        wait_for(lambda: first.state == "recognizing", "加载后未进入识别")
        first.cancel()
        first.start(pixmap)
        assert first.state == "queued" and "仍在结束" in messages[-1]
        cold.infer.set()
        wait_for(lambda: engine.calls == 2 and first.state == "recognizing", "取消后的重试未启动")
        assert first.document is None
        assert all(not document for document in recognized)
        assert engine.calls == 2, "排队取消任务意外启动了模型"
        for mode in ("light", "dark"):
            apply_theme(app, mode)
            app.processEvents()
            first._progress.grab().save(str(output / f"progress-{mode}.png"))
        second.start(pixmap)
        superseded = second._request
        second.start(pixmap)
        assert superseded.cancelled.is_set()
        newest = _Plan("第二窗口最新结果")
        engine.plans.append(newest)
        current.infer.set()
        wait_for(lambda: first.state == "completed" and second.state == "recognizing",
                 "两个窗口未按串行顺序完成")
        assert first.document.results[0].text == "当前结果"
        newest.infer.set()
        wait_for(lambda: second.state == "completed", "第二窗口任务未完成")
        assert engine.calls == 3 and second.document.results[0].text == "第二窗口最新结果"
        assert first.adopt_document(second.document, pixmap)
        assert engine.calls == 3 and first.document is second.document
        first.show_results()
        assert first._dialogs[-1]._document is second.document
        first._dialogs[-1].close()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        first.invalidate()
        assert first.document is None and not recognized[-1]
        ordered = _Plan(results=[OCRResult(QRect(20, 70, 90, 20), "下行", 0.99),
                                 OCRResult(QRect(20, 20, 90, 20), "上行", 0.99)])
        engine.plans.append(ordered)
        settings.direct = True
        ordered.infer.set()
        first.start(pixmap)
        wait_for(lambda: first.state == "completed", "直接复制任务未完成")
        assert QApplication.clipboard().text() == first.document.text()
        assert QApplication.clipboard().text().startswith("上行"), "直接复制使用了引擎原始输入顺序"
        settings.direct = False

        for plan, expected in ((_Plan(""), "empty"), (_Plan(error="可控模型错误"), "failed")):
            engine.plans.append(plan)
            plan.infer.set()
            first.start(pixmap)
            wait_for(lambda: first.state == expected, f"{expected} 状态未到达")
        stalled = _Plan("超时后的迟到结果")
        engine.plans.append(stalled)
        first.start(pixmap)
        wait_for(lambda: first.state == "recognizing", "超时验收任务未开始")
        first._timer.timeout.emit()
        assert first.state == "timeout" and not first.running
        stalled.infer.set()
        wait_for(lambda: service.active is None, "超时任务后台未结束")
        assert first.document is None and first.state == "timeout"
        delayed = _Plan("预算外完成结果")
        engine.plans.append(delayed)
        first.start(pixmap)
        wait_for(lambda: first.state == "recognizing", "预算验收任务未开始")
        first._request.started_at -= 61
        delayed.infer.set()
        wait_for(lambda: first.state == "timeout" and service.active is None,
                 "完成信号越过执行预算后仍被接受")
        assert first.document is None
        closing = _Plan("关闭后的迟到结果")
        engine.plans.append(closing)
        second.start(pixmap)
        wait_for(lambda: second.state == "recognizing", "关闭验收任务未开始")
        request = second._request
        owners[1].deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert request.cancelled.is_set(), "窗口销毁未取消 OCR"
        closing.infer.set()
        wait_for(lambda: service.active is None, "已关闭窗口任务后台未结束")
        assert {"queued", "loading", "recognizing", "completed", "empty", "failed", "timeout", "cancelled"} <= set(first_states)
        assert len(notices) == 3
        owners[0].deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    service.shutdown()
    print(f"PASS OCR tasks: {engine.calls} 实际调用；排队不计时、取消跳过、串行、状态、共享文档、超时和销毁通过")
    print(f"视觉产物: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
