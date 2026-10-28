# 设置窗口：左侧纵向导航、右侧分组卡片（对齐 PixPin 配置窗口），QSettings 持久化。
# organization/app 名按任务约定为 GridStruct/GridStruct。

import sys
from pathlib import Path
from subprocess import list2cmdline

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox, QLineEdit, QPushButton,
    QFileDialog, QWidget, QApplication, QKeySequenceEdit, QListWidget, QListWidgetItem,
    QStackedWidget, QScrollArea, QFrame,
)
from PySide6.QtCore import QSettings, Qt, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QKeySequence, QIcon

from ui import BaseDialog, ask_confirmation, show_message, tinted_icon
from styles import apply_theme
from styles import tokens as T
from tray import HOTKEY_DEFAULTS, parse_hotkey
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


_HOTKEY_LABELS = {"screenshot": "截图", "longshot": "长截图", "document": "识别文档"}
# 导航顺序：页面名、Lucide 图标、构建方法名。
_PAGES = (("通用", "settings", "_general_page"), ("截图", "scan", "_shot_page"),
          ("保存", "save", "_save_page"), ("文字识别", "scan-text", "_ocr_page"),
          ("快捷键", "keyboard", "_hotkey_page"), ("关于", "info", "_about_page"))


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("SettingsDivider")
    return line


class _Card(QFrame):
    """一组设置：圆角卡片，行与行之间一条细线；名称在左、控件在右侧对齐列。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("SettingsCard")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(T.SPACE_4 + T.SPACE_1, T.SPACE_1, T.SPACE_4 + T.SPACE_1, T.SPACE_1)
        self._layout.setSpacing(0)

    def _add(self, layout, hint: str):
        # 卡片内不套 QWidget：全局 QWidget 底色会盖住卡片色，只用布局排版。
        if self._layout.count():
            self._layout.addWidget(_divider())
        box = QVBoxLayout()
        box.setContentsMargins(0, T.SPACE_3, 0, T.SPACE_3)
        box.setSpacing(T.SPACE_1)
        box.addLayout(layout)
        if hint:
            note = QLabel(hint)
            note.setProperty("class", "muted")
            note.setWordWrap(True)
            box.addWidget(note)
        self._layout.addLayout(box)

    def row(self, name: str, control, hint: str = ""):
        line = QHBoxLayout()
        line.setSpacing(T.SPACE_4)
        line.addWidget(QLabel(name), 1)
        if isinstance(control, QWidget):
            control.setFixedWidth(T.SETTINGS_CONTROL_WIDTH)
            line.addWidget(control)
        else:
            line.addLayout(control, 2)
        self._add(line, hint)

    def check(self, box: QCheckBox, hint: str = ""):
        line = QHBoxLayout()
        line.addWidget(box)
        self._add(line, hint)

    def widget(self, widget: QWidget, hint: str = ""):
        line = QHBoxLayout()
        line.addWidget(widget, 0, Qt.AlignLeft)
        self._add(line, hint)


class _Page(QScrollArea):
    """一个导航页：标题、组标题在卡片外上方；内容超长时只滚动右侧。"""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("SettingsScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("SettingsPage")
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(T.SPACE_4 + T.SPACE_2, T.SPACE_4, T.SPACE_4 + T.SPACE_2, T.SPACE_4)
        self._layout.setSpacing(T.SPACE_2)
        heading = QLabel(title)
        heading.setProperty("class", "pagetitle")
        self._layout.addWidget(heading)
        self._layout.addStretch()
        self.setWidget(inner)

    def group(self, title: str) -> _Card:
        stretch = self._layout.count() - 1
        label = QLabel(title)
        label.setProperty("class", "grouptitle")
        self._layout.insertSpacing(stretch, T.SPACE_2)
        self._layout.insertWidget(stretch + 1, label)
        card = _Card()
        self._layout.insertWidget(stretch + 2, card)
        return card


class SettingsDialog(BaseDialog):
    def __init__(self, parent=None, apply_hotkeys=None):
        """apply_hotkeys(texts) 在确认时注册新热键，失败返回原因；不传时只保存配置。"""
        super().__init__(parent)
        self._apply_hotkeys = apply_hotkeys
        # 模态设置会禁用贴图输入，必须位于已有置顶贴图之上。
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setWindowTitle("设置")
        self.resize(760, 540)
        self.setMinimumSize(640, 440)
        self._s = get_settings()
        self._original_theme = self._s.value("theme", "system")
        self._original_language = self._s.value("language", "system")

        self._nav = QListWidget()
        self._nav.setObjectName("SettingsNav")
        self._nav.setFixedWidth(T.SETTINGS_NAV_WIDTH)
        self._nav.setIconSize(QSize(18, 18))
        self._nav.setFocusPolicy(Qt.NoFocus)
        self._pages = QStackedWidget()
        for title, _icon, build in _PAGES:
            self._nav.addItem(QListWidgetItem(title))
            self._pages.addWidget(getattr(self, build)(title))
        self._nav.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._nav.setCurrentRow(0)
        self._tint_nav()

        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setProperty("class", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self._save)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(T.SPACE_4 + T.SPACE_2, T.SPACE_3, T.SPACE_4 + T.SPACE_2, T.SPACE_3)
        buttons.setSpacing(T.SPACE_2)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(ok)

        body = QWidget()
        body.setObjectName("SettingsBody")
        right = QVBoxLayout(body)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._pages, 1)
        right.addWidget(_divider())
        right.addLayout(buttons)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._nav)
        layout.addWidget(body, 1)

    def _tint_nav(self):
        """导航图标随主题和选中态换色：选中项是主色底，图标要反白。"""
        t = T.THEMES[QApplication.instance().property("theme_mode") or "light"]
        for row, (_title, name, _build) in enumerate(_PAGES):
            icon = tinted_icon(name, t["foreground"], 36, QIcon())
            self._nav.item(row).setIcon(tinted_icon(name, t["primary_foreground"], 36, icon, QIcon.Selected))

    # ---------- 通用 ----------

    def _general_page(self, title) -> QWidget:
        page = _Page(title)
        self._theme = QComboBox()
        self._theme.addItem("浅色", "light")
        self._theme.addItem("深色", "dark")
        self._theme.addItem("跟随系统", "system")
        cur = self._s.value("theme", "system")
        self._theme.setCurrentIndex(max(0, self._theme.findData(cur)))
        self._theme.currentIndexChanged.connect(self._on_theme_changed)
        page.group("外观").row("主题", self._theme)

        self._language = QComboBox()
        self._language.addItem("跟随系统", "system")
        self._language.addItem("简体中文", "zh_CN")
        self._language.setCurrentIndex(max(0, self._language.findData(self._original_language)))
        page.group("语言").row("界面语言", self._language,
                               "当前界面支持简体中文；其他系统语言使用中文。更改后需重启。")

        self._autostart = QCheckBox("开机后自动启动")
        if sys.platform != "win32":
            self._autostart.setEnabled(False)
            self._autostart.setToolTip("仅 Windows 支持")
        else:
            self._autostart.setChecked(_read_autostart())
        page.group("启动").check(self._autostart)
        return page

    def _on_theme_changed(self, _idx: int):
        mode = self._theme.currentData()
        apply_theme(QApplication.instance(), mode)
        self._tint_nav()

    def reject(self):
        apply_theme(QApplication.instance(), self._original_theme)
        super().reject()

    # ---------- 截图 ----------

    def _shot_page(self, title) -> QWidget:
        page = _Page(title)
        self._snap_only = QCheckBox("仅吸附窗口")
        self._snap_only.setChecked(self._s.value("snap_only_window", False, type=bool))
        self._guides = QCheckBox("显示十字辅助线")
        self._guides.setChecked(self._s.value("capture_guides", True, type=bool))
        detect = page.group("自动识别")
        detect.check(self._snap_only, "开启后只框选整个窗口，不再识别按钮、输入框等控件。")
        detect.check(self._guides)
        self._same_color = QCheckBox("所有标注工具使用同一颜色")
        self._same_color.setChecked(self._s.value("same_tool_color", True, type=bool))
        page.group("标注").check(self._same_color, "高亮笔保留自己的颜色。")
        self._confirm_exit = QCheckBox("退出截图时，如有未保存的标注先确认")
        self._confirm_exit.setChecked(self._s.value("capture/confirm_exit", True, type=bool))
        page.group("退出").check(self._confirm_exit, "按 Esc 或点关闭时生效；没有标注时直接退出。")
        return page

    # ---------- 保存 ----------

    def _save_page(self, title) -> QWidget:
        page = _Page(title)
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_2)
        self._shot_dir = QLineEdit(self._s.value("screenshot_dir", ""))
        self._shot_dir.setPlaceholderText("默认：图片文件夹")
        browse = QPushButton("浏览")
        browse.setProperty("class", "secondary")
        browse.clicked.connect(self._browse_dir)
        row.addWidget(self._shot_dir, 1)
        row.addWidget(browse)
        self._img_fmt = QComboBox()
        self._img_fmt.addItems(["png", "jpg"])
        self._img_fmt.setCurrentText(self._s.value("image_format", "png"))
        card = page.group("保存")
        card.row("保存目录", row)
        card.row("图片格式", self._img_fmt, "PNG 无损；JPG 文件更小，适合照片类截图。")
        return page

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if d:
            self._shot_dir.setText(d)

    # ---------- 文字识别 ----------

    def _ocr_page(self, title) -> QWidget:
        page = _Page(title)
        self._ocr_copy = QCheckBox("识别完成后直接复制文字")
        self._ocr_copy.setChecked(self._s.value("ocr/direct_copy", False, type=bool))
        page.group("识别结果").check(self._ocr_copy, "开启后不显示复核窗口。识别在本机完成，不上传图片。")
        return page

    # ---------- 快捷键 ----------

    def _hotkey_page(self, title) -> QWidget:
        page = _Page(title)
        card = page.group("全局快捷键")
        self._hotkeys = {}
        for name, label in _HOTKEY_LABELS.items():
            edit = QKeySequenceEdit(QKeySequence(self._s.value(f"hotkey/{name}", HOTKEY_DEFAULTS[name]),
                                                 QKeySequence.PortableText))
            edit.setMaximumSequenceLength(1)
            if not HOTKEY_DEFAULTS[name]:
                # 默认不设的可选热键可以清空，清空即只走托盘；Qt 自带占位文字是英文。
                edit.setClearButtonEnabled(True)
                edit.findChild(QLineEdit).setPlaceholderText("未设置")
            edit.keySequenceChanged.connect(self._check_hotkeys)
            self._hotkeys[name] = edit
            card.row(label, edit)
        defaults = QPushButton("恢复默认")
        defaults.clicked.connect(self._reset_hotkeys)
        card.widget(defaults, "点击输入框后按下新组合键。确定后立即生效；新组合被占用时保留原热键。识别文档默认不设快捷键，可从托盘打开。")
        self._hotkey_error = QLabel()
        self._hotkey_error.setProperty("class", "error")
        self._hotkey_error.setWordWrap(True)
        page._layout.insertWidget(page._layout.count() - 1, self._hotkey_error)
        self._check_hotkeys()
        return page

    def _reset_hotkeys(self):
        for name, edit in self._hotkeys.items():
            edit.setKeySequence(QKeySequence(HOTKEY_DEFAULTS[name], QKeySequence.PortableText))

    def _hotkey_texts(self):
        return {name: edit.keySequence().toString(QKeySequence.PortableText) for name, edit in self._hotkeys.items()}

    def _check_hotkeys(self) -> bool:
        """边录边校验，确定前就把非法组合和两键重复指出来。"""
        texts = self._hotkey_texts()
        problem = ""
        for name, text in texts.items():
            if not text and not HOTKEY_DEFAULTS[name]:
                continue
            try:
                parse_hotkey(text)
            except ValueError as error:
                problem = f"{_HOTKEY_LABELS[name]}：{error}"
                break
        used = [text for text in texts.values() if text]
        if not problem and len(set(used)) < len(used):
            problem = "不同功能不能用同一个组合"
        self._hotkey_error.setText(problem)
        self._hotkey_error.setVisible(bool(problem))
        return not problem

    # ---------- 关于 ----------

    def _about_page(self, title) -> QWidget:
        page = _Page(title)
        card = page.group("GridStruct-V2")
        card.row("当前版本", QLabel(f"{_VERSION} · Windows 便携版"))
        links = QLabel('<a href="https://github.com/Clayprocess/GridStruct-v2#readme">使用说明</a> · '
                       '<a href="https://github.com/Clayprocess/GridStruct-v2/issues">反馈问题</a>')
        links.setOpenExternalLinks(True)
        card.row("帮助", links)
        updates = QPushButton("检查更新")
        updates.clicked.connect(lambda: check_updates(self))
        card.widget(updates, "截图、贴图与中英文 OCR 均在本机处理。")
        return page

    # ---------- 保存 ----------

    def _save(self):
        if not self._check_hotkeys():
            self._nav.setCurrentRow(4)
            show_message(self, "热键无效", self._hotkey_error.text())
            return
        previous_hotkeys = {}
        for name, default in HOTKEY_DEFAULTS.items():
            text = self._s.value(f"hotkey/{name}", default)
            try:
                parse_hotkey(text)
            except ValueError:
                # 与启动时的配置回退一致，回滚必须恢复实际使用的组合。
                text = default
            previous_hotkeys[name] = text
        texts = self._hotkey_texts()
        problem = self._apply_hotkeys(texts) if self._apply_hotkeys is not None else None
        if problem:
            self._nav.setCurrentRow(4)
            show_message(self, "快捷键冲突", problem)
            return
        if sys.platform == "win32":
            try:
                _write_autostart(self._autostart.isChecked())
            except OSError as exc:
                # 自启写入失败不能留下尚未保存的新热键。
                problem = self._apply_hotkeys(previous_hotkeys) if self._apply_hotkeys is not None else None
                message = f"开机自启设置失败：{exc}"
                if problem:
                    message += f"\n原快捷键恢复失败：{problem}，请重新设置快捷键。"
                show_message(self, "无法保存设置", message)
                return
        for name, text in texts.items():
            self._s.setValue(f"hotkey/{name}", text)
        self._s.setValue("theme", self._theme.currentData())
        self._s.setValue("language", self._language.currentData())
        self._s.setValue("screenshot_dir", self._shot_dir.text())
        self._s.setValue("image_format", self._img_fmt.currentText())
        self._s.setValue("snap_only_window", self._snap_only.isChecked())
        self._s.setValue("capture_guides", self._guides.isChecked())
        self._s.setValue("same_tool_color", self._same_color.isChecked())
        self._s.setValue("ocr/direct_copy", self._ocr_copy.isChecked())
        self._s.setValue("capture/confirm_exit", self._confirm_exit.isChecked())
        if sys.platform == "win32":
            self._s.setValue("autostart", self._autostart.isChecked())
        self._s.sync()
        if self._s.status() != QSettings.NoError:
            show_message(self, "无法保存设置", "配置目录不可写，请将应用解压到有写入权限的目录。")
            return
        self.accept()
        if self._language.currentData() != self._original_language:
            show_message(self.parentWidget(), "语言设置已保存", "重启 GridStruct-V2 后应用新的语言设置。")
