"""关于页反馈入口离屏验收。使用临时配置和保留测试域名，不打开浏览器或发送消息。"""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from settings import SettingsDialog, VERSION
from styles import apply_theme


def main():
    app = QApplication([])
    recipient = "反馈+qa&dev@example.invalid"
    with TemporaryDirectory() as folder:
        settings = QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
        with patch("settings.dialog.get_settings", return_value=settings), \
                patch("settings.dialog.QDesktopServices.openUrl") as launch, \
                patch("settings.dialog.show_message") as warning, patch("settings.dialog.show_toast"):
            for address in ("", "invalid", recipient):
                settings.setValue("feedback/email", address)
                dialog = SettingsDialog()
                dialog._nav.setCurrentRow(dialog._nav.count() - 1)
                about = dialog._pages.currentWidget()
                buttons = {button.text(): button for button in about.findChildren(QPushButton)}
                buttons["复制反馈模板"].click()
                template = app.clipboard().text()
                assert template.splitlines() == [
                    "【GridStruct-V2 反馈】", f"版本：{VERSION}",
                    "问题类型：功能异常 / 界面 / 识别效果 / 建议（保留一项）",
                    "复现步骤：", "实际结果：", "期望结果：",
                ]
                teams = buttons["打开 Teams"]
                if address == recipient:
                    assert teams.isEnabled()
                    launch.return_value = True
                    teams.click()
                    url = urlsplit(bytes(launch.call_args.args[0].toEncoded()).decode("ascii"))
                    assert (url.scheme, url.netloc, url.path) == (
                        "https", "teams.microsoftonline.cn", "/l/chat/0/0")
                    assert parse_qs(url.query, strict_parsing=True) == {
                        "users": [recipient], "message": [template]}
                    warning.assert_not_called()
                    launch.return_value = False
                    teams.click()
                    assert "复制反馈模板" in warning.call_args.args[2]
                else:
                    assert not teams.isEnabled()
                    assert any("尚未配置" in label.text() for label in about.findChildren(QLabel))
                    teams.click()
                    launch.assert_not_called()
                for mode in ("light", "dark"):
                    apply_theme(app, mode)
                    dialog.show()
                    app.processEvents()
                    assert not dialog.grab().isNull()
                dialog.close()
    print("PASS: Teams 中国区聊天、特殊字符、预填版本、无收件人和打开失败时的复制入口")


if __name__ == "__main__":
    main()
