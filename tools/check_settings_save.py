"""离屏验收：设置写不进磁盘时，已经生效的快捷键、开机自启和内存里的配置都恢复原样。

原来快捷键和开机自启先生效、配置文件最后写；写失败只弹提示，前两项留在新值，界面说失败、实际改了一半。

用法：python -X utf8 tools/check_settings_save.py。只用临时配置，假装是 Windows 以走到开机自启分支，注册表写入是假的。
"""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


class _UnwritableSettings(QSettings):
    """模拟应用目录变成只读：内存里能改，sync 写不进磁盘。"""

    def status(self):
        return QSettings.AccessError


def check_sync_failure(folder, original, enable):
    """original：打开设置时注册表里的自启项；enable：用户在设置里把自启改成开还是关。"""
    from settings import SettingsDialog
    from tray import HOTKEY_DEFAULTS
    s = _UnwritableSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
    s.setValue("theme", "light")
    applied, registry = [], []
    with patch("settings.dialog.get_settings", return_value=s), \
            patch("settings.dialog.sys", SimpleNamespace(platform="win32")), \
            patch("settings.dialog._read_autostart", return_value=original), \
            patch("settings.dialog._write_autostart", side_effect=registry.append), \
            patch("settings.dialog._restore_autostart", side_effect=registry.append), \
            patch("settings.dialog.apply_theme"), \
            patch("settings.dialog.show_message") as warning:
        dialog = SettingsDialog(apply_hotkeys=lambda texts: applied.append(dict(texts)))
        dialog._hotkeys["screenshot"].setKeySequence("Ctrl+Shift+B")
        dialog._autostart.setChecked(enable)
        dialog._theme.setCurrentIndex(dialog._theme.findData("dark"))
        dialog._save()
        assert warning.called and "不可写" in warning.call_args.args[2]
        assert dialog.result() == 0, "保存失败时设置窗口不能关"
        assert applied == [{"screenshot": "Ctrl+Shift+B", "document": ""}, HOTKEY_DEFAULTS], \
            f"写配置失败后快捷键必须换回原来的：{applied}"
        assert registry == [enable, original], \
            f"写配置失败后注册表里的自启项必须原样恢复：{registry}"
        assert s.value("theme") == "light" and not s.contains("hotkey/screenshot") and not s.contains("autostart"), \
            "内存里的配置也要回到打开设置前，别的地方读到的才不是没保存的新值"
        dialog.deleteLater()


def check_restore_writes_original():
    """回滚写注册表时用打开设置时读到的命令和类型，不是当前程序重新生成的命令。"""
    from settings.dialog import _restore_autostart
    calls = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    fake = SimpleNamespace(HKEY_CURRENT_USER=0, KEY_SET_VALUE=0, REG_SZ=1,
                           CreateKeyEx=lambda *_: Key(),
                           SetValueEx=lambda _k, name, _r, kind, value: calls.append((name, value, kind)),
                           DeleteValue=lambda _k, name: calls.append((name, None)))
    stable = r'"C:\Apps\GridStruct-stable\GridStruct.exe"'
    with patch.dict(sys.modules, {"winreg": fake}):
        _restore_autostart((stable, 2))
        _restore_autostart(None)
    assert calls == [("PixShot", stable, 2), ("PixShot", None)], calls


def main():
    app = QApplication.instance() or QApplication([])
    from styles import apply_theme
    apply_theme(app, "light")
    check_restore_writes_original()
    with TemporaryDirectory() as folder:
        check_sync_failure(folder, None, True)
        # 自启原本指向另一个目录的版本，在这里关掉：回滚要写回那条原命令，不能换成当前程序的路径。
        check_sync_failure(folder, (r'"C:\Apps\GridStruct-stable\GridStruct.exe"', 2), False)
    print("PASS: a failed settings write rolls back hotkeys, the exact autostart registry entry and in-memory values")


if __name__ == "__main__":
    main()
