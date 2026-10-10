# OCR 引擎：PP-OCRv6 small + onnxruntime，经 RapidOCR 调用。
# 模型随安装包发布（assets/models/，约 31MB），离线开箱即用。

import os
from dataclasses import dataclass
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import QRect

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "models")
_MODEL_DIR = os.path.abspath(_MODEL_DIR)

_MODELS = {
    "det": "PP-OCRv6_det_small.onnx",
    "rec": "PP-OCRv6_rec_small.onnx",
    "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
}


@dataclass
class OCRResult:
    box: QRect      # 逻辑坐标
    text: str
    score: float


class OCREngine:
    """懒加载单例。recognize(QPixmap) -> list[OCRResult]。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._engine = None
        return cls._instance

    def _ensure(self):
        if self._engine is not None:
            return
        from rapidocr import RapidOCR
        kw = {}
        for k, name in _MODELS.items():
            p = os.path.join(_MODEL_DIR, name)
            if os.path.exists(p):
                kw[f"{k}_model_path"] = p
        # RapidOCR 参数名随版本变，传不进去就用默认（会自动下载）
        try:
            self._engine = RapidOCR(**kw)
        except TypeError:
            self._engine = RapidOCR()

    def recognize(self, pixmap: QPixmap) -> list[OCRResult]:
        self._ensure()
        dpr = pixmap.devicePixelRatio() or 1.0
        img = pixmap.toImage().convertToFormat(QImage.Format_RGB888)
        w, h = img.width(), img.height()
        import numpy as np
        arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(h, w, 3)
        res = self._engine(arr)
        out = []
        if res.boxes is not None:
            for box, txt, score in zip(res.boxes, res.txts or [], res.scores or []):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                r = QRect(int(min(xs) / dpr), int(min(ys) / dpr),
                          int((max(xs) - min(xs)) / dpr),
                          int((max(ys) - min(ys)) / dpr))
                out.append(OCRResult(r, txt or "", float(score or 0)))
        return out
