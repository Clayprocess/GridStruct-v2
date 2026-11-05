"""截图里的识别卡片：全文可改、与截图双向定位、按键归属、改过再标注、复制文字，及工具条圆角与明暗截图。

不跑 OCR 模型、不用用户截图：合成图 + 手写识别结果，经 adopt_document 交给截图窗口。
"""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from styles import apply_theme, tokens as T

OUTPUT = Path(__file__).resolve().parents[1] / ".scratch" / "ocr-capture-card"


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def sample():
    pixmap = QPixmap(560, 220)
    pixmap.fill(QColor("#ffffff"))
    with QPainter(pixmap) as painter:
        painter.setPen(QColor("#18181b"))
        painter.drawText(20, 40, "Invoice 2026")
        painter.drawText(20, 80, "Amount: 120.00")
        painter.drawText(20, 120, "Tax: 6.00")
    return pixmap


def records():
    from ocr import OCRResult
    return [OCRResult(QRect(16, 24, 200, 22), "Invoice 2026", .98),
            OCRResult(QRect(16, 64, 200, 22), "Amount: 12O.OO", .62),
            OCRResult(QRect(16, 104, 120, 22), "Tax: 6.00", .99)]


def open_window(app, pixmap, at=QPoint(200, 120), mode="light"):
    from annotate import AnnotationWindow
    from ocr import OCRDocument
    window = AnnotationWindow(pixmap, at, mode)
    window.show()
    flush(app)
    document = OCRDocument(records(), window._render())
    assert window._ocr.adopt_document(document, window._render())
    flush(app)
    return window, document


def check_card(app):
    window, document = open_window(app, sample())
    panel = window._toolbar
    editor = panel.ocr_editor
    # 识别出文字：卡片展开成全文，排版默认“段落”，低置信度 1 处，可点截图定位。
    assert panel.ocr_document is document and editor.isVisible()
    assert editor.toPlainText() == document.text() and panel._ocr_layout.currentData() == "paragraph"
    assert panel._ocr_status.text() == "已识别 3 处文字", panel._ocr_status.text()
    assert panel._ocr_pending.text() == "低置信度 1 处" and panel._ocr_pending.isVisible()
    assert "定位" in panel._ocr_hint.text()
    # 卡片在主条下方，整条不压住选区。
    assert panel._ocr_feedback.mapTo(panel, QPoint()).y() > panel._button_row.mapTo(panel, QPoint()).y()
    assert panel.geometry().top() > window.geometry().bottom()
    # 点截图上的字（文字层定位）→ 全文光标跳过去；全文里挪光标 → 截图上那行高亮。
    amount = document.results[1].item_id
    document.focus((document.results[2].item_id,))
    start = next(span[0] for span in editor.spans if span[2].item_id == document.results[2].item_id)
    assert editor.textCursor().position() == start
    cursor = editor.textCursor()
    cursor.setPosition(next(span[0] for span in editor.spans if span[2].item_id == amount) + 1)
    editor.setTextCursor(cursor)
    assert document.focused_ids == (amount,)
    assert [item._focused for item in window._text_layer.childItems()] == [False, True, False]
    # F8 跳到低置信度那处。
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key_F8)
    assert editor.textCursor().position() == next(span[0] for span in editor.spans if span[2].item_id == amount)

    # 在全文里打字：Shift+1 打出“!”不切工具，Enter 换行不结束截图，Ctrl+Z 撤销的是文字。
    cursor.movePosition(cursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key_Exclam, Qt.ShiftModifier)
    assert panel.selected_tool() is None and window._canvas.tool_id in ("", None), window._canvas.tool_id
    QTest.keyClick(editor, Qt.Key_Return)
    QTest.keyClicks(editor, "ok")
    assert window.isVisible() and document.draft is not None and document.draft.endswith("\nok"), document.draft
    assert "已修改" in panel._ocr_hint.text()
    QTest.keyClick(editor, Qt.Key_Z, Qt.ControlModifier)
    assert not editor.toPlainText().endswith("ok")
    QTest.keyClicks(editor, "ok")

    # 改过再换排版要确认：取消则不变。
    with patch("ocr.editor.ask_confirmation", return_value=False) as asked:
        panel._ocr_layout.setCurrentIndex(panel._ocr_layout.findData("lines"))
        panel._choose_ocr_layout(panel._ocr_layout.currentIndex())
        assert asked.called and document.layout == "paragraph" and panel._ocr_layout.currentData() == "paragraph"
        assert document.draft.endswith("ok")

    # 改过字时 Esc 要确认，提示里说清楚丢的是文字；取消就留在截图里。
    with patch("annotate.window._confirm_exit", return_value=False) as confirm:
        QTest.keyClick(editor, Qt.Key_Escape)
        assert confirm.called and "改过的识别文字" in confirm.call_args.args[1] and window.isVisible()

    # 重新识别会丢掉改过的字，先确认。
    with patch("annotate.window.ask_confirmation", return_value=False) as asked, \
            patch.object(window._ocr, "start") as start_ocr:
        window.trigger_action("ocr")
        assert asked.called and not start_ocr.called

    # 改过字再标注：文字保留、可复制，不再和截图定位。
    window.select_tool("rect")
    flush(app)
    assert window._text_layer is None and panel.ocr_document is document and editor.isVisible()
    assert "定位" not in panel._ocr_hint.text() and panel._ocr_status.text().startswith("已切换到标注")
    window._image_changed()
    assert window._ocr.document is document and panel.ocr_document is document
    assert "不再对照截图定位" in panel._ocr_status.text()

    # “复制文字”：复制改过的全文并结束截图，不发图片输出。
    finished = []
    window.finished.connect(finished.append)
    window.trigger_action("ocr_copy")
    assert QApplication.clipboard().text() == document.draft and not finished
    flush(app)

    # 没改过就标注：照旧作废，卡片收成一行状态。
    window, document = open_window(app, sample())
    window.select_tool("rect")
    window._image_changed()
    assert window._ocr.document is None and window._toolbar.ocr_document is None
    assert not window._toolbar.ocr_editor.isVisible() and "已失效" in window._toolbar._ocr_status.text()
    window.close()
    flush(app)


def check_flip(app):
    # 选区靠屏幕底边：下方连卡片放不下，整条翻到选区上方，卡片在主条外侧。
    screen = app.primaryScreen().availableGeometry()
    pixmap = QPixmap(500, 140)
    pixmap.fill(QColor("#ffffff"))
    window, _document = open_window(app, pixmap, QPoint(200, screen.bottom() - 220))
    panel = window._toolbar
    assert panel.geometry().bottom() < window.geometry().top(), (panel.geometry(), window.geometry())
    assert panel._ocr_feedback.mapTo(panel, QPoint()).y() < panel._button_row.mapTo(panel, QPoint()).y()
    window.close()
    flush(app)


def check_corners(app):
    # 主条四角之外不能有不透明的方块：行容器以前按全局 QWidget 规则铺了方形底色，直角从圆角里露出来。
    from annotate.toolbar import FloatPanel
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        panel = FloatPanel(mode=mode)
        panel.set_position_near(QRect(100, 100, 600, 300))
        panel.show()
        flush(app)
        image = panel.grab().toImage()
        row = panel._button_row.geometry().translated(panel._button_row.parentWidget().mapTo(panel, QPoint()))
        for corner in (row.topLeft(), row.topRight(), row.bottomLeft(), row.bottomRight()):
            assert image.pixelColor(corner).alpha() < 128, (mode, corner, image.pixelColor(corner).name())
        # 卡片正中是底色，阴影在卡片下方而不是一整块实心带。
        assert image.pixelColor(row.center()).name() == T.THEMES[mode]["popover"]
        below = image.pixelColor(row.center().x(), row.bottom() + 4)
        assert 0 < below.alpha() < 40, below.alpha()
        panel.close()
        panel.deleteLater()
        flush(app)


def screenshots(app):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for mode in ("light", "dark"):
        apply_theme(app, mode)
        app.setProperty("theme_mode", mode)
        window, document = open_window(app, sample(), mode=mode)
        document.focus((document.results[1].item_id,))
        flush(app)
        panel = window._toolbar
        top = window.geometry().united(panel.geometry())
        canvas = QPixmap(top.width() + 40, top.height() + 40)
        canvas.fill(QColor("#949495" if mode == "light" else "#19191b"))
        with QPainter(canvas) as painter:
            origin = QPoint(20, 20) - top.topLeft()
            painter.drawPixmap(window.geometry().topLeft() + origin, window.grab())
            painter.drawPixmap(panel.geometry().topLeft() + origin, panel.grab())
        assert canvas.save(str(OUTPUT / f"card-{mode}.png"))
        window.close()
        flush(app)


def main():
    app = QApplication.instance() or QApplication([])
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("arial.ttf", "msyh.ttc"):
        QFontDatabase.addApplicationFont(str(fonts / filename))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    apply_theme(app, "light")
    with TemporaryDirectory() as folder, patch("settings.storage.data_directory", return_value=Path(folder)):
        check_card(app)
        check_flip(app)
        check_corners(app)
        screenshots(app)
    print("PASS: card shows editable full text, layout/pending/F8, locate both ways with the screenshot")
    print("PASS: typing keys stay in the text box, Esc/re-OCR/re-layout confirm when edited, copy text ends capture")
    print("PASS: edited text survives annotating (no locate), unedited is invalidated; flips above when no room")
    print("PASS: toolbar corners have no square backgrounds, soft shadow")
    print(f"Screenshots: {OUTPUT}")


if __name__ == "__main__":
    main()
