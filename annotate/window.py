# 标注窗口：画布 + 样式条 + 浮动工具条。
# finished(QPixmap) 携带渲染结果；cancelled 取消。

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QSlider, QFileDialog,
    QLabel,
)
from PySide6.QtGui import QPixmap, QIcon, QColor
from PySide6.QtCore import Qt, QPoint, Signal

from .canvas import AnnotationCanvas
from .toolbar import FloatPanel
from ui.icons import tinted_icon
from styles import tokens as T

_SWATCHES = ["#ef4444", "#18181b", "#ffffff", "#3b82f6", "#22c55e", "#eab308"]


def _swatch_icon(color: str, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    from PySide6.QtGui import QPainter
    with QPainter(pm) as p:
        p.setBrush(QColor(color))
        p.setPen(QColor("#71717a") if color == "#ffffff" else Qt.NoPen)
        p.drawEllipse(1, 1, size - 2, size - 2)
    return QIcon(pm)


class AnnotationWindow(QWidget):
    finished = Signal(QPixmap)
    cancelled = Signal()

    def __init__(self, pixmap: QPixmap, pos: QPoint, theme_mode: str = "light"):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._mode = theme_mode

        dpr = pixmap.devicePixelRatio() or 1.0
        lw, lh = int(pixmap.width() / dpr), int(pixmap.height() / dpr)

        self._canvas = AnnotationCanvas(pixmap)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._canvas, 1)
        layout.addWidget(self._style_strip(), 0)
        self.resize(lw, lh + 44)
        self.move(pos)

        self._toolbar = FloatPanel(mode=theme_mode)
        self._toolbar.toolSelected.connect(self._on_tool)
        self._toolbar.actionTriggered.connect(self._on_action)
        self._toolbar.set_position_near(self.geometry())
        self._toolbar.show()
        self._canvas.pipettePicked.connect(self._on_pipette_picked)

    # ---------- 样式条 ----------

    def _style_strip(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StyleStrip")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("颜色"))
        self._swatches = []
        for c in _SWATCHES:
            b = QToolButton()
            b.setIcon(_swatch_icon(c))
            b.setCheckable(True)
            b.setToolTip(c)
            b.clicked.connect(lambda _=False, _c=c: self._set_color(_c))
            hl.addWidget(b)
            self._swatches.append((c, b))
        self._swatches[0][1].setChecked(True)
        hl.addWidget(QLabel("粗细"))
        slider = QSlider(Qt.Horizontal)
        slider.setRange(1, 10)
        slider.setValue(self._canvas.style.width)
        slider.setFixedWidth(120)
        slider.valueChanged.connect(self._set_width)
        hl.addWidget(slider)
        hl.addStretch()
        # 取色器
        pip = QToolButton()
        pip.setIcon(tinted_icon("pipette", self._mode))
        pip.setCheckable(True)
        pip.setToolTip("取色器")
        pip.toggled.connect(self._toggle_pipette)
        hl.addWidget(pip)
        self._pip_btn = pip
        return bar

    def _toggle_pipette(self, on: bool):
        self._canvas.set_pipette_mode(on)
        if on:
            print("[取色器] 点击画布取色")

    def _on_pipette_picked(self, hex_code: str):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(hex_code)
        self._pip_btn.setChecked(False)
        print(f"[取色器] {hex_code} 已复制")

    def _set_color(self, color: str):
        self._canvas.style.color = QColor(color)
        for c, b in self._swatches:
            b.setChecked(c == color)

    def _set_width(self, w: int):
        self._canvas.style.width = w

    # ---------- 工具条 ----------

    def _on_tool(self, tool_id: str):
        if not self._canvas.set_tool_by_id(tool_id):
            print(f"[标注] 工具 {tool_id}：P0-A 后续实现")

    def _on_action(self, action: str):
        if action == "undo":
            self._canvas.undo()
        elif action == "redo":
            self._canvas.redo()
        elif action == "confirm":
            self._finish()
        elif action == "copy":
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setPixmap(self._canvas.render_to_pixmap())
            self._finish()
        elif action == "save":
            self._save()
        elif action == "ocr":
            self._run_ocr()
        elif action == "cancel":
            self.cancelled.emit()
            self.close()
        else:
            print(f"[工具条] {action}：P0-A 后续实现")

    def _finish(self):
        self.finished.emit(self._canvas.render_to_pixmap())
        self.close()

    def _save(self):
        import os
        from PySide6.QtCore import QSettings, QStandardPaths
        from PySide6.QtWidgets import QApplication
        s = QSettings("GridStruct", "GridStruct")
        default_dir = s.value("screenshot_dir", "") or \
            QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        start = os.path.join(default_dir, "PixShot.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存截图", start, "PNG (*.png);;JPG (*.jpg);;BMP (*.bmp)")
        if path:
            self._canvas.render_to_pixmap().save(path)
            QApplication.clipboard().setText(path)
            print(f"[保存] {path}（路径已复制）")

    def _run_ocr(self):
        from PySide6.QtWidgets import QApplication
        from ocr.engine import OCREngine
        from ocr.dialog import OCRResultDialog
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            results = OCREngine().recognize(self._canvas.render_to_pixmap())
        finally:
            QApplication.restoreOverrideCursor()
        print(f"[OCR] 识别到 {len(results)} 行")
        OCRResultDialog(results, self).exec()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self._toolbar.close()
        super().closeEvent(e)
