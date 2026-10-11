# 设置窗口：QTabWidget 四个 tab，QSettings 持久化。
# organization/app 名按任务约定为 GridStruct/GridStruct。

import sys
from pathlib import Path
from subprocess import list2cmdline

from PySide6.QtWidgets import (
    QTabWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QComboBox, QCheckBox, QLineEdit, QPushButton, QFileDialog,
    QDialogButtonBox, QWidget, QApplication,
)
from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices

from ui import BaseDialog, ask_confirmation, show_message
from styles import apply_theme
from tray import GlobalHotkey
from .storage import get_settings

_ORG, _APP = "GridStruct", "GridStruct"
_VERSION = "0.1.0"
# 保留旧版内部标识，避免升级后自启项失联或重复。
_AUTOSTART_VALUE = "PixShot"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def check_updates(parent=None):
    """便携版由用户选择是否访问发布页，不在后台联网。"""
    if ask_confirmation(parent, "检查更新",
                        f"当前版本 {_VERSION}。是否打开项目发布页查看可用版本？",
                        confirm_text="打开发布页"):
        if not QDesktopServices.openUrl(QUrl("https://github.com/Clayprocess/GridStruct-v2/releases")):
            show_message(parent, "无法打开发布页", "系统未能打开浏览器，请检查默认浏览器设置后重试。")


def _read_autostart() -> bool:
    """读开机自启（Windows）。注册表无值视为关闭。"""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, _AUTOSTART_VALUE)
        return True
    except OSError:
        return False


def _write_autostart(enabled: bool):
    """写入可从任意工作目录启动的命令，失败交由设置窗口报告。"""
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                           0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, _AUTOSTART_VALUE, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(k, _AUTOSTART_VALUE)
            except FileNotFoundError:
                pass  # 已关闭时再次保存，无需创建值。


def _autostart_command() -> str:
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return list2cmdline([str(executable)])
    pythonw = executable.with_name("pythonw.exe")
    return list2cmdline([str(pythonw), "-X", "utf8", str(Path(__file__).resolve().parents[1] / "main.py")])


class SettingsDialog(BaseDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 模态设置会禁用贴图输入，必须位于已有置顶贴图之上。
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setWindowTitle("设置")
        self.resize(420, 360)
        self._s = get_settings()
        self._original_theme = self._s.value("theme", "system")
        self._original_language = self._s.value("language", "system")

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "通用")
        tabs.addTab(self._shot_tab(), "截图")
        tabs.addTab(self._hotkey_tab(), "热键")
        tabs.addTab(self._about_tab(), "关于")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Ok).setProperty("class", "primary")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # ---------- 通用 ----------

    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self._theme = QComboBox()
        self._theme.addItem("浅色", "light")
        self._theme.addItem("深色", "dark")
        self._theme.addItem("跟随系统", "system")
        cur = self._s.value("theme", "system")
        self._theme.setCurrentIndex(max(0, self._theme.findData(cur)))
        self._theme.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("主题", self._theme)

        self._language = QComboBox()
        self._language.addItem("跟随系统", "system")
        self._language.addItem("简体中文", "zh_CN")
        self._language.setCurrentIndex(max(0, self._language.findData(self._original_language)))
        form.addRow("语言", self._language)
        language_hint = QLabel("当前界面支持简体中文；其他系统语言使用中文。更改后需重启。")
        language_hint.setWordWrap(True)
        form.addRow("", language_hint)

        self._autostart = QCheckBox("开机自启")
        if sys.platform != "win32":
            self._autostart.setEnabled(False)
            self._autostart.setToolTip("仅 Windows 支持")
        else:
            self._autostart.setChecked(_read_autostart())
        form.addRow("", self._autostart)
        return w

    def _on_theme_changed(self, _idx: int):
        mode = self._theme.currentData()
        apply_theme(QApplication.instance(), mode)

    def reject(self):
        apply_theme(QApplication.instance(), self._original_theme)
        super().reject()

    # ---------- 截图 ----------

    def _shot_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        row = QHBoxLayout()
        self._shot_dir = QLineEdit(self._s.value("screenshot_dir", ""))
        self._shot_dir.setPlaceholderText("默认：图片文件夹")
        browse = QPushButton("浏览")
        browse.setProperty("class", "secondary")
        browse.clicked.connect(self._browse_dir)
        row.addWidget(self._shot_dir, 1)
        row.addWidget(browse)
        form.addRow("保存目录", row)

        self._img_fmt = QComboBox()
        self._img_fmt.addItems(["png", "jpg"])
        self._img_fmt.setCurrentText(self._s.value("image_format", "png"))
        form.addRow("图片格式", self._img_fmt)
        self._snap_only = QCheckBox("仅吸附窗口（关闭控件级吸附）")
        self._snap_only.setChecked(self._s.value("snap_only_window", False, type=bool))
        form.addRow("", self._snap_only)
        self._guides = QCheckBox("显示十字辅助线")
        self._guides.setChecked(self._s.value("capture_guides", True, type=bool))
        form.addRow("", self._guides)
        self._same_color = QCheckBox("所有标注工具使用同一颜色")
        self._same_color.setChecked(self._s.value("same_tool_color", False, type=bool))
        form.addRow("", self._same_color)
        self._ocr_copy = QCheckBox("OCR 完成后直接复制，不显示复核窗口")
        self._ocr_copy.setChecked(self._s.value("ocr/direct_copy", False, type=bool))
        form.addRow("", self._ocr_copy)
        return w

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if d:
            self._shot_dir.setText(d)

    # ---------- 热键 ----------

    def _hotkey_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        le = QLineEdit(GlobalHotkey.hotkey_name())
        le.setReadOnly(True)
        form.addRow("截图", le)
        form.addRow("", QLabel("P0 暂不支持修改热键"))
        return w

    # ---------- 关于 ----------

    def _about_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("GridStruct-V2"))
        layout.addWidget(QLabel(f"版本 {_VERSION} · Windows 便携版"))
        links = QLabel('<a href="https://github.com/Clayprocess/GridStruct-v2#readme">使用说明</a> · '
                       '<a href="https://github.com/Clayprocess/GridStruct-v2/issues">反馈问题</a>')
        links.setOpenExternalLinks(True)
        layout.addWidget(links)
        layout.addWidget(QLabel("截图、贴图与中英文 OCR 均在本机处理。"))
        updates = QPushButton("检查更新")
        updates.setProperty("class", "outline")
        updates.clicked.connect(lambda: check_updates(self))
        layout.addWidget(updates, alignment=Qt.AlignLeft)
        layout.addStretch()
        return w

    # ---------- 保存 ----------

    def _save(self):
        if sys.platform == "win32":
            try:
                _write_autostart(self._autostart.isChecked())
            except OSError as exc:
                show_message(self, "无法保存设置", f"开机自启设置失败：{exc}")
                return
        self._s.setValue("theme", self._theme.currentData())
        self._s.setValue("language", self._language.currentData())
        self._s.setValue("screenshot_dir", self._shot_dir.text())
        self._s.setValue("image_format", self._img_fmt.currentText())
        self._s.setValue("snap_only_window", self._snap_only.isChecked())
        self._s.setValue("capture_guides", self._guides.isChecked())
        self._s.setValue("same_tool_color", self._same_color.isChecked())
        self._s.setValue("ocr/direct_copy", self._ocr_copy.isChecked())
        if sys.platform == "win32":
            self._s.setValue("autostart", self._autostart.isChecked())
        self._s.sync()
        if self._s.status() != QSettings.NoError:
            show_message(self, "无法保存设置", "配置目录不可写，请将应用解压到有写入权限的目录。")
            return
        self.accept()
        if self._language.currentData() != self._original_language:
            show_message(self.parentWidget(), "语言设置已保存", "重启 GridStruct-V2 后应用新的语言设置。")
