# OCR 引擎：PP-OCRv6 small + onnxruntime，经 RapidOCR 调用。
# 模型随安装包发布（assets/models/，约 31MB），离线开箱即用。

import os
from threading import Lock
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import QRectF
from .model import OCRResult

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "models")
_MODEL_DIR = os.path.abspath(_MODEL_DIR)

_MODELS = {
    "det": "PP-OCRv6_det_small.onnx",
    "rec": "PP-OCRv6_rec_small.onnx",
    "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
}


class OCREngine:
    """懒加载单例。recognize(QPixmap) -> list[OCRResult]。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._engine = None
            cls._instance._lock = Lock()
        return cls._instance

    def _ensure(self):
        if self._engine is not None:
            return
        from rapidocr import RapidOCR
        params = {}
        for k, name in _MODELS.items():
            p = os.path.join(_MODEL_DIR, name)
            if not os.path.isfile(p):
                raise FileNotFoundError(f"缺少本地 OCR 模型：{p}")
            params[f"{k.title()}.model_path"] = p
        self._engine = RapidOCR(params=params)

    def recognize(self, pixmap: QPixmap) -> list[OCRResult]:
        return self.recognize_image(pixmap.toImage())

    def recognize_image(self, image: QImage, on_stage=None) -> list[OCRResult]:
        """后台任务使用独立 QImage，串行复用 ONNX 会话。"""
        if image.isNull():
            raise ValueError("没有可识别的图片")
        with self._lock:
            if on_stage is not None and self._engine is None:
                on_stage("loading")
            self._ensure()
            if on_stage is not None:
                on_stage("recognizing")
            return self._recognize_image(image)

    def _recognize_image(self, image: QImage) -> list[OCRResult]:
        from capture import ScreenManager
        img = image.convertToFormat(QImage.Format_BGR888)
        w, h = img.width(), img.height()
        import numpy as np
        # Qt 每行按 4 字节对齐；RapidOCR 的 ndarray 输入采用 BGR。
        rows = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(h, img.bytesPerLine())
        arr = rows[:, :w * 3].reshape(h, w, 3).copy()
        res = self._engine(arr)
        out = []
        if res.boxes is not None:
            for index, (box, txt, score) in enumerate(zip(res.boxes, res.txts, res.scores)):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                physical = QRectF(float(min(xs)), float(min(ys)),
                                  float(max(xs) - min(xs)), float(max(ys) - min(ys)))
                r = ScreenManager.image_rect_to_logical(physical, image).toAlignedRect()
                polygon = tuple((float(point[0]), float(point[1])) for point in box)
                out.append(OCRResult(r, txt or "", float(score or 0), polygon=polygon, raw_index=index))
        return out
