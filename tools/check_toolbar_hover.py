"""框选和标注时把焦点留在画布，验证第一次悬停就能显示工具提示。"""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from capture import DesktopCapture, ScreenFrame, ScreenshotFlow
from styles import apply_theme
from styles import tokens as T


def first_hover(editor, panel, group):
    QCursor.setPos(editor.screen().availableGeometry().bottomRight() - QPoint(2, 2))
    QTest.qWait(T.MOTION_BASE + 50)
    editor.activateWindow()
    assert QTest.qWaitForWindowActive(editor, 3000), "截图窗口未激活"
    assert QApplication.activeWindow() is editor
    assert not QToolTip.isVisible()
    button = panel._buttons[group]
    # 只移动鼠标，让 Qt 自己启动提示计时器。直接发 ToolTip 事件会绕过窗口激活检查。
    QTest.mouseMove(button, button.rect().center())
    deadline = monotonic() + 4
    while not QToolTip.isVisible() and monotonic() < deadline:
        QTest.qWait(40)
    assert QToolTip.isVisible() and QToolTip.text() == button.toolTip(), button.toolTip()
    assert QApplication.activeWindow() is editor, "工具提示抢走了画布焦点"


def main():
    app = QApplication([])
    apply_theme(app, "light")
    screen = app.primaryScreen()
    frame = QRect(screen.availableGeometry().topLeft() + QPoint(20, 20), QSize(520, 210))
    pixels = QPixmap(frame.size())
    pixels.fill()
    capture = DesktopCapture([ScreenFrame(screen, frame, pixels, frame.topLeft())])
    with TemporaryDirectory() as folder, patch("settings.storage.data_directory", return_value=Path(folder)):
        (Path(folder) / "settings.ini").touch()
        with patch("capture.flow.capture_virtual_desktop", return_value=capture):
            flow = ScreenshotFlow(app)
            try:
                flow.start()
                overlay = flow._win
                overlay._set_selection(QRect(frame.topLeft() + QPoint(90, 55), QSize(320, 150)))
                first_hover(overlay._windows[0], flow._panel, "geo")
                print(f"PASS first hover during selection ({app.platformName()})", flush=True)
                QToolTip.hideText()
                assert overlay.confirm()
                first_hover(flow._ann, flow._ann._toolbar, "lines")
                print(f"PASS first hover during annotation ({app.platformName()})", flush=True)
            finally:
                flow.cancel()
                app.aboutToQuit.emit()


if __name__ == "__main__":
    main()
