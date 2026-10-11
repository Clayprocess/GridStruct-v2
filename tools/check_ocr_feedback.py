"""可见业务窗口 OCR 反馈回归；用服务信号替代模型，不操作桌面输入。"""

from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QPoint, QRect, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QAbstractButton

from annotate import AnnotationWindow
from pin import PinWindow
from ocr import OCRResult
from styles import apply_theme, tokens as T


class _Service(QObject):
    requested = Signal(object)
    finished = Signal(object, object, str)
    stage = Signal(object, str)
    active = None


def _visible_text(window):
    return " ".join(child.text() for kind in (QLabel, QAbstractButton)
                    for child in window.findChildren(kind) if child.isVisible())


def main():
    app = QApplication(sys.argv)
    if app.platformName() == "offscreen":
        raise RuntimeError("此验收必须使用可见 Windows 窗口。")
    service = _Service()
    errors = []
    settings = type("Settings", (), {"value": lambda self, key, default, **kw:
                                    key == "ocr/hide_empty_notice" or default})()
    with (patch("ocr.controller._get_service", return_value=service),
          patch("settings.get_settings", return_value=settings),
          patch("ocr.controller.show_message") as warning):
        for mode in ("light", "dark"):
            apply_theme(app, mode)
            pixmap = QPixmap(640, 240)
            pixmap.fill(T.THEMES[mode]["background"])
            windows = [AnnotationWindow(pixmap, QPoint(120, 120), mode), PinWindow(pixmap)]
            for window in windows:
                window.show()
                app.processEvents()
                if isinstance(window, AnnotationWindow):
                    window.trigger_action("ocr")
                    controller = window._ocr
                else:
                    window._recognize()
                    controller = window._ocr_controller
                controller._request.started_at = monotonic()
                service.stage.emit(controller._request, "recognizing")
                app.processEvents()
                if "正在本地识别" not in _visible_text(window):
                    errors.append(f"{mode}/{type(window).__name__}: 无运行状态")
                controller.cancel()
                app.processEvents()
                if "已取消" not in _visible_text(window):
                    errors.append(f"{mode}/{type(window).__name__}: 无取消状态")
                controller.start(pixmap)
                service.finished.emit(controller._request, [], "验收用模型错误")
                app.processEvents()
                if "识别失败" not in _visible_text(window) or not warning.called:
                    errors.append(f"{mode}/{type(window).__name__}: 无失败状态")
                controller.start(pixmap)
                controller._timer.timeout.emit()
                app.processEvents()
                if "识别超时" not in _visible_text(window):
                    errors.append(f"{mode}/{type(window).__name__}: 无超时状态")
                controller.start(pixmap)
                service.finished.emit(controller._request, [], "")
                app.processEvents()
                if "未检测到文字" not in _visible_text(window):
                    errors.append(f"{mode}/{type(window).__name__}: 无空结果状态")
                controller.start(pixmap)
                results = [OCRResult(QRect(20, 20, 180, 30), "识别反馈验收", 0.99)]
                service.finished.emit(controller._request, results, "")
                app.processEvents()
                text = _visible_text(window)
                if "已识别" not in text or "原图" not in text:
                    errors.append(f"{mode}/{type(window).__name__}: 识别完成后无可见提示：{text}")
                if isinstance(window, AnnotationWindow):
                    buttons = [b for b in window.findChildren(QAbstractButton)
                               if b.isVisible() and b.text() == "查看全文"]
                    if not buttons:
                        errors.append(f"{mode}/AnnotationWindow: 没有可见全文入口")
                    else:
                        buttons[0].click()
                        app.processEvents()
                        if not controller._dialogs or not controller._dialogs[-1].isVisible():
                            errors.append("全文按钮未打开结果对话框")
                window._allow_close = True
                window.close()
                app.processEvents()
    for error in errors:
        print("FAIL", error)
    print(f"OCR feedback: {'FAIL' if errors else 'PASS'}")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
