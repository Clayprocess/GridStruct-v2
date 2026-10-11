# pin 模块公开接口；会话仅记录未关闭贴图，不实现 P1 历史/销毁。

import json
import math
import re

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap
from PySide6.QtCore import QTimer, QSaveFile, QIODevice

from settings import data_directory
from ui import show_message
from .window import PinWindow, PinTextWindow, _ConfirmDialog

__all__ = [
    "PinWindow", "PinTextWindow", "show_pin", "show_text_pin",
    "show_pin_from_clipboard", "disable_click_through", "restore_pins",
    "save_pins", "shutdown_pins",
]

_open_pins: list[PinWindow | PinTextWindow] = []
_save_timer = None
_restoring = False
_shutting_down = False
_restore_failed = False
_save_error = None


def _schedule_save():
    global _save_timer
    if _restoring or _shutting_down or _restore_failed:
        return
    if _save_timer is None:
        _save_timer = QTimer(QApplication.instance())
        _save_timer.setSingleShot(True)
        # 拖动和输入会连续发出状态变化，合并落盘避免每个像素都写 JSON。
        _save_timer.setInterval(250)
        _save_timer.timeout.connect(save_pins)
    _save_timer.start()


def _closed(window):
    if not _shutting_down:
        _open_pins.remove(window)
        save_pins()


def _register(window, pos=None):
    if pos is not None:
        window.move(pos)
    _open_pins.append(window)
    window.stateChanged.connect(_schedule_save)
    window.closed.connect(_closed)
    window.show()
    _schedule_save()
    return window


def show_pin(pixmap, pos=None, document=None):
    """创建图片贴图；同图 OCR 文档随贴图共享，保持人工校正。"""
    if pixmap.isNull():
        show_message(None, "贴图", "无法创建空图片贴图")
        return None
    return _register(PinWindow(pixmap, document=document), pos)


def show_text_pin(text: str, pos=None):
    """创建可滑选、双击编辑的文字贴图。"""
    if not text:
        show_message(None, "贴图", "没有可贴出的文字")
        return None
    if len(text) > 10000:
        dialog = _ConfirmDialog("文字贴图", f"文字较长（{len(text)} 字），确认创建文字贴图？")
        if not dialog.exec():
            return None
    return _register(PinTextWindow(text), pos)


def show_pin_from_clipboard():
    """剪贴板优先取图片，其次取纯文字。"""
    clipboard = QApplication.clipboard()
    mime = clipboard.mimeData()
    if mime is not None and mime.hasImage():
        return show_pin(clipboard.pixmap())
    if mime is not None and mime.hasText() and clipboard.text():
        return show_text_pin(clipboard.text())
    show_message(None, "贴图", "剪贴板没有图片或文字")
    return None


def disable_click_through():
    """托盘入口：退出全部贴图的鼠标穿透并恢复各自原不透明度。"""
    for window in _open_pins:
        if window._click_through:
            window.set_click_through(False)


def save_pins() -> bool:
    """原子保存当前会话。失败会保留上次 JSON 并提示，不删除历史图片。"""
    global _save_error
    if _shutting_down:
        return True
    if _restore_failed:
        return False
    if _save_timer is not None:
        _save_timer.stop()
    try:
        directory = data_directory() / "pins"
        directory.mkdir(parents=True, exist_ok=True)
        states = []
        for window in _open_pins:
            state = window.state()
            if window.kind == "image":
                image_path = directory / f"{window.pin_id}.png"
                if not image_path.exists():
                    image_file = QSaveFile(str(image_path))
                    if not image_file.open(QIODevice.WriteOnly):
                        raise OSError(image_file.errorString())
                    if not window._base.save(image_file, "PNG"):
                        image_file.cancelWriting()
                        raise OSError(f"无法自动保存图片：{image_path}")
                    if not image_file.commit():
                        raise OSError(image_file.errorString())
            states.append(state)
        data = json.dumps({"version": 1, "pins": states}, ensure_ascii=False, indent=2).encode("utf-8")
        session = QSaveFile(str(directory / "session.json"))
        if not session.open(QIODevice.WriteOnly):
            raise OSError(session.errorString())
        if session.write(data) != len(data):
            session.cancelWriting()
            raise OSError(session.errorString())
        if not session.commit():
            raise OSError(session.errorString())
    except OSError as error:
        message = str(error)
        if message != _save_error:
            _save_error = message
            show_message(None, "贴图自动保存失败", message)
        return False
    _save_error = None
    return True


def _validate_state(state):
    if not isinstance(state, dict):
        raise ValueError("贴图记录应为 JSON 对象")
    if not isinstance(state["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", state["id"]):
        raise ValueError("贴图 ID 无效")
    if state["kind"] not in ("image", "text"):
        raise ValueError("不支持的贴图类型")
    for key in ("x", "y"):
        if type(state[key]) is not int or not -(2**31) <= state[key] < 2**31:
            raise ValueError(f"贴图 {key} 坐标无效")
    for key, low, high in (("scale", 0.2, 5.0), ("opacity", 0.1, 1.0)):
        value = state[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"贴图 {key} 超出范围")
    for key in ("topmost", "click_through"):
        if type(state[key]) is not bool:
            raise ValueError(f"贴图 {key} 状态无效")
    if state["kind"] == "text":
        if not isinstance(state["text"], str):
            raise ValueError("贴图文字无效")
    else:
        value = state["dpr"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 8:
            raise ValueError("贴图像素比例无效")


def restore_pins() -> bool:
    """启动时恢复未关闭贴图。损坏的会话保持原样，禁止后续自动覆盖。"""
    global _restoring, _restore_failed
    path = None
    try:
        directory = data_directory() / "pins"
        path = directory / "session.json"
        if not path.exists():
            return True
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("pins"), list):
            raise ValueError("不支持的贴图会话格式")
        prepared = []
        identifiers = set()
        for state in data["pins"]:
            _validate_state(state)
            if state["id"] in identifiers:
                raise ValueError("贴图 ID 重复")
            identifiers.add(state["id"])
            pixmap = None
            if state["kind"] == "image":
                pixmap = QPixmap(str(directory / f"{state['id']}.png"))
                if pixmap.isNull():
                    raise ValueError(f"贴图图片缺失或无法读取：{state['id']}.png")
                pixmap.setDevicePixelRatio(state["dpr"])
            prepared.append((state, pixmap))
    except (OSError, ValueError, KeyError, TypeError) as error:
        _restore_failed = True
        show_message(
            None, "贴图恢复失败",
            f"{error}\n原会话已保留，自动保存暂停。"
            + (f"请修复或移走该文件后重启：\n{path}" if path is not None else "请检查应用目录的写入权限后重启。"))
        return False
    _restoring = True
    ocr_errors = []
    for state, pixmap in prepared:
        document = None
        if state["kind"] == "image" and "ocr" in state:
            from ocr import OCRDocument
            try:
                document = OCRDocument.from_payload(state["ocr"], pixmap.toImage())
            except (ValueError, TypeError, KeyError) as error:
                ocr_errors.append(f"{state['id']}：{error}")
        window = PinWindow(pixmap, document=document) if state["kind"] == "image" else PinTextWindow(state["text"])
        window.restore_state(state)
        _register(window)
        if state["kind"] == "image" and "ocr" in state and document is None:
            window._show_ocr_status("OCR 数据未恢复，原会话已保留，自动保存暂停", False)
    _restoring = False
    # OCR 校正损坏时仍恢复图片，但不能用空结果自动覆盖原始人工编辑。
    _restore_failed = bool(ocr_errors)
    if ocr_errors:
        show_message(None, "贴图 OCR 未恢复", "\n".join(ocr_errors)
                     + f"\n贴图图片已恢复，原会话和 OCR 校正已保留，自动保存暂停。"
                     + f"\n请修复或移走该文件后重启：\n{path}")
    return not ocr_errors


def shutdown_pins() -> bool:
    """退出前保存会话；成功后关闭窗口不会把会话改成空列表。"""
    global _shutting_down
    if not save_pins():
        dialog = _ConfirmDialog(
            "退出前保存失败", "未能保存当前贴图。本次未保存的更改会丢失，仍要退出应用？")
        if not dialog.exec():
            return False
    _shutting_down = True
    for window in _open_pins:
        window._allow_close = True
    return True
