# QSS 主题生成器：唯一的样式表出口。
# 用法：apply_theme(app, "light" | "dark" | "system")
# 业务代码不许手写 setStyleSheet，一律走这里。

import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from . import tokens as T

_ICON_DIR = Path(__file__).resolve().parents[1] / "assets" / "icons"
# QSS 的 image: url() 只认文件，Lucide 的 currentColor 又不被 Qt SVG 支持，
# 所以按主题色写出染色副本供样式表引用。
_TINTED_DIR = Path(tempfile.gettempdir()) / "gridstruct-v2-icons"


def _icon_url(name: str, color: str, stroke: int = 2) -> str:
    path = _TINTED_DIR / f"{name}-{color.lstrip('#')}-{stroke}.svg"
    if not path.exists():
        svg = (_ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
        svg = svg.replace("currentColor", color).replace('stroke-width="2"', f'stroke-width="{stroke}"')
        _TINTED_DIR.mkdir(exist_ok=True)
        path.write_text(svg, encoding="utf-8")
    # QSS url() 要正斜杠，Windows 路径需转换。
    return path.as_posix()


def build_qss(t: dict) -> str:
    r = T.RADIUS_MD
    return f"""
/* ===== 基础 ===== */
QWidget {{
    background-color: {t['background']};
    color: {t['foreground']};
    font-size: {T.FONT_MD}px;
}}
QLabel {{
    background: transparent;
}}
QLabel[class="title"] {{
    font-size: {T.FONT_LG}px;
    font-weight: 600;
}}
QLabel[class="muted"] {{
    color: {t['muted_foreground']};
    font-size: {T.FONT_SM}px;
}}
QLabel[class="error"] {{
    color: {t['destructive']};
    font-size: {T.FONT_SM}px;
}}

/* ===== 按钮 =====
   裸 QPushButton 默认是 outline：一个窗口只应有一个主按钮，必须显式声明 class=primary。
   所有按钮常驻 1px 边框（填充类为透明），hover 时尺寸不跳。 */
QPushButton {{
    background-color: transparent;
    color: {t['foreground']};
    border: 1px solid {t['border']};
    border-radius: {r}px;
    padding: 7px 15px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {t['accent']};
    color: {t['accent_foreground']};
}}
QPushButton:pressed {{
    background-color: {t['secondary_hover']};
}}
QPushButton:disabled {{
    background-color: transparent;
    color: {t['muted_foreground']};
    border-color: {t['border']};
}}
QPushButton[class="primary"] {{
    background-color: {t['primary']};
    color: {t['primary_foreground']};
    border-color: transparent;
}}
QPushButton[class="primary"]:hover, QPushButton[class="primary"]:pressed {{
    background-color: {t['primary_hover']};
    color: {t['primary_foreground']};
}}
QPushButton[class="secondary"] {{
    background-color: {t['secondary']};
    color: {t['secondary_foreground']};
    border-color: transparent;
}}
QPushButton[class="secondary"]:hover, QPushButton[class="secondary"]:pressed {{
    background-color: {t['secondary_hover']};
    color: {t['secondary_foreground']};
}}
QPushButton[class="ghost"] {{
    border-color: transparent;
}}
QPushButton[class="destructive"] {{
    background-color: {t['destructive']};
    color: {t['destructive_foreground']};
    border-color: transparent;
}}
QPushButton[class="destructive"]:hover, QPushButton[class="destructive"]:pressed {{
    background-color: {t['destructive_hover']};
    color: {t['destructive_foreground']};
}}
QPushButton[class="primary"]:disabled, QPushButton[class="secondary"]:disabled,
QPushButton[class="destructive"]:disabled {{
    background-color: {t['muted']};
    color: {t['muted_foreground']};
    border-color: transparent;
}}

/* ===== 工具条按钮（标注工具条用）===== */
QToolButton {{
    background-color: transparent;
    color: {t['foreground']};
    border: none;
    border-radius: {T.RADIUS_SM}px;
    padding: 6px;
}}
QToolButton:hover {{
    background-color: {t['accent']};
}}
QToolButton:checked {{
    background-color: {t['secondary']};
    border: 1px solid {t['border']};
    padding: 5px;
}}
QToolButton:disabled {{
    color: {t['muted_foreground']};
}}

/* ===== FloatPanel 浮动工具条 ===== */
/* 注：面板本体的圆角卡片背景由 FloatPanel.paintEvent 手绘
   （WA_TranslucentBackground 下 QSS 不绘制自身背景），颜色取自 tokens */
QFrame#FloatPanelSeparator {{
    background-color: {t['border']};
    border: none;
    min-width: 1px;
    max-width: 1px;
    margin: 8px 4px;
}}
/* 工具条确认 / 取消：primary / destructive 变体 */
QToolButton[class="primary"] {{
    background-color: {t['primary']};
    border-radius: {T.RADIUS_SM}px;
}}
QToolButton[class="primary"]:hover {{
    background-color: {t['primary_hover']};
}}
QToolButton[class="destructive"] {{
    background-color: {t['destructive']};
    border-radius: {T.RADIUS_SM}px;
}}
QToolButton[class="destructive"]:hover {{
    background-color: {t['destructive_hover']};
}}

/* ===== 输入框 ===== */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {t['background']};
    border: 1px solid {t['input']};
    border-radius: {r}px;
    padding: 7px 10px;
    selection-background-color: {t['primary']};
    selection-color: {t['primary_foreground']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {t['ring']};
}}
QComboBox:hover, QSpinBox:hover {{
    border-color: {t['muted_foreground']};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {t['muted_foreground']};
    background-color: {t['muted']};
}}
/* 数字框不显示上下按钮（Qt 默认箭头在自定义边框下会画残），改用滚轮或直接输入。
   Qt 坑：只写 width: 0 会把输入区一起压成 1px，必须同时指定 origin 和 height。 */
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 0px;
    height: 0px;
    border: none;
}}
QGraphicsView {{
    background-color: {t['background']};
    color: {t['foreground']};
    border: none;
    padding: 0;
}}
QComboBox {{
    padding-right: 28px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: url({_icon_url("chevron-down", t["muted_foreground"])});
    width: 14px;
    height: 14px;
}}
QComboBox::down-arrow:disabled {{
    image: url({_icon_url("chevron-down", t["border"])});
}}
QComboBox QAbstractItemView {{
    background-color: {t['popover']};
    color: {t['popover_foreground']};
    border: 1px solid {t['border']};
    border-radius: {r}px;
    padding: 4px;
    outline: none;
    selection-background-color: {t['accent']};
    selection-color: {t['accent_foreground']};
}}
QProgressBar {{
    border: 1px solid {t['border']};
    border-radius: {r}px;
    background: {t['secondary']};
    text-align: center;
    min-height: 16px;
}}
QProgressBar::chunk {{
    background: {t['primary']};
    border-radius: {r}px;
}}
/* 等待类细进度条：只表示“在进行”，不抢视线 */
QProgressBar[class="slim"] {{
    border: none;
    border-radius: 2px;
    background: {t['secondary']};
    min-height: 4px;
    max-height: 4px;
}}
QProgressBar[class="slim"]::chunk {{
    background: {t['foreground']};
    border-radius: 2px;
}}

/* ===== 菜单 / 浮层 ===== */
QMenu {{
    background-color: {t['popover']};
    color: {t['popover_foreground']};
    border: 1px solid {t['border']};
    border-radius: {T.RADIUS_LG}px;
    padding: 4px;
}}
QMenu::item {{
    border-radius: {T.RADIUS_SM}px;
    padding: 6px 24px 6px 12px;
}}
QMenu::item:selected {{
    background-color: {t['accent']};
    color: {t['accent_foreground']};
}}
QMenu::separator {{
    height: 1px;
    background: {t['border']};
    margin: 4px 8px;
}}

/* ===== Tooltip ===== */
QToolTip {{
    background-color: {t['primary']};
    color: {t['primary_foreground']};
    border: none;
    border-radius: {T.RADIUS_SM}px;
    padding: 4px 8px;
    font-size: {T.FONT_SM}px;
}}

/* ===== 对话框 ===== */
QDialog {{
    background-color: {t['card']};
}}

/* ===== 复选 / 单选 ===== */
QCheckBox, QRadioButton {{
    background: transparent;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {t['muted_foreground']};
    border-radius: {T.RADIUS_SM}px;
    background: {t['background']};
}}
QCheckBox::indicator:hover {{
    border-color: {t['foreground']};
}}
QCheckBox::indicator:checked {{
    background: {t['primary']};
    border-color: {t['primary']};
    image: url({_icon_url("check", t["primary_foreground"], stroke=3)});
}}
QCheckBox::indicator:disabled {{
    border-color: {t['border']};
    background: {t['muted']};
}}
QCheckBox:disabled {{
    color: {t['muted_foreground']};
}}

/* ===== 滑杆 ===== */
QSlider::groove:horizontal {{
    height: 4px;
    background: {t['secondary']};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px; height: 14px;
    margin: -5px 0;
    border-radius: 7px;
    background: {t['primary']};
}}

/* ===== 滚动条 ===== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {t['border']};
    border-radius: 5px;
    min-height: 30px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t['muted_foreground']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {t['border']};
    border-radius: 5px;
    min-width: 30px;
    margin: 2px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ===== Tab ===== */
QTabWidget::pane {{
    border: 1px solid {t['border']};
    border-radius: {r}px;
    background: {t['card']};
}}
QTabBar::tab {{
    background: transparent;
    color: {t['muted_foreground']};
    padding: 8px 14px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {t['foreground']};
    border-bottom: 2px solid {t['primary']};
    font-weight: 500;
}}

/* ===== 列表 ===== */
QListWidget {{
    background: {t['card']};
    border: 1px solid {t['border']};
    border-radius: {r}px;
    padding: 4px;
}}
QListWidget::item {{
    border-radius: {T.RADIUS_SM}px;
    padding: 6px 8px;
}}
QListWidget::item:selected {{
    background: {t['accent']};
    color: {t['accent_foreground']};
}}
QListWidget::item:hover {{
    background: {t['accent']};
}}

/* ===== 分组框 ===== */
QGroupBox {{
    border: 1px solid {t['border']};
    border-radius: {T.RADIUS_LG}px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 500;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {t['foreground']};
    background: transparent;
}}

/* ===== 设置窗口（settings/dialog.py）：左侧导航 + 右侧分组卡片 ===== */
QListWidget#SettingsNav {{
    background: {t['background']};
    border: none;
    border-right: 1px solid {t['border']};
    border-radius: 0;
    padding: {T.SPACE_3}px {T.SPACE_2}px;
    outline: none;
}}
QListWidget#SettingsNav::item {{
    min-height: {T.SETTINGS_NAV_ITEM}px;
    padding: 0 {T.SPACE_2}px;
    margin-bottom: {T.SPACE_1}px;
    border-radius: {r}px;
    color: {t['foreground']};
}}
QListWidget#SettingsNav::item:hover {{
    background: {t['accent']};
}}
QListWidget#SettingsNav::item:selected {{
    background: {t['primary']};
    color: {t['primary_foreground']};
}}
QWidget#SettingsBody, QScrollArea#SettingsScroll, QWidget#SettingsPage {{
    background: {t['settings_surface']};
    border: none;
}}
QFrame#SettingsCard {{
    background: {t['settings_card']};
    border: 1px solid {t['border']};
    border-radius: {T.RADIUS_LG}px;
}}
QFrame#SettingsDivider {{
    background: {t['border']};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}
QLabel[class="pagetitle"] {{
    font-size: {T.FONT_XL}px;
    font-weight: 600;
}}
QLabel[class="grouptitle"] {{
    font-size: {T.FONT_LG}px;
    font-weight: 600;
}}

/* ===== 标注二级面板（annotate/options.py）===== */
QWidget#ToolOptions {{
    background: transparent;
    border-top: 1px solid {t['border']};
}}
QWidget#ToolOptions[above="true"] {{
    border-top: none;
    border-bottom: 1px solid {t['border']};
}}
QWidget#ToolOptions QLabel {{
    color: {t['muted_foreground']};
    font-size: {T.FONT_SM}px;
    min-height: 29px;
}}
/* 输入类控件统一 26px 高，换工具时面板高度不跳。 */
QWidget#ToolOptions QSpinBox, QWidget#ToolOptions QComboBox {{
    min-height: 18px;
    max-height: 18px;
}}
QWidget#ToolOptions QSpinBox {{
    padding: 3px 4px;
    min-width: 36px;
    max-width: 48px;
}}
QWidget#ToolOptions QComboBox {{
    padding: 3px 26px 3px 8px;
}}
QWidget#ToolOptions QFontComboBox {{
    min-width: 100px;
}}
QFrame#OptionSeparator {{
    background-color: {t['border']};
    border: none;
    min-width: 1px;
    max-width: 1px;
    margin: 6px 2px;
}}
/* 色块与粗细圆点：选中态是一圈 ring，不用底色，避免把颜色本身染灰。 */
QToolButton#Swatch {{
    padding: 2px;
    border: 2px solid transparent;
    border-radius: 12px;
}}
QToolButton#Swatch:hover {{
    background-color: {t['accent']};
}}
QToolButton#Swatch:checked {{
    background-color: transparent;
    border: 2px solid {t['ring']};
    padding: 2px;
}}
QToolButton#Swatch:disabled, QToolButton#Segment:disabled {{
    background-color: transparent;
    color: {t['border']};
}}
QFrame#SegmentGroup {{
    background-color: {t['muted']};
    border-radius: {r}px;
}}
QToolButton#Segment {{
    color: {t['muted_foreground']};
    font-size: {T.FONT_SM}px;
    padding: 3px 8px;
    border: 1px solid transparent;
    border-radius: {T.RADIUS_SM}px;
}}
QToolButton#Segment:hover {{
    color: {t['foreground']};
    background-color: transparent;
}}
QToolButton#Segment:checked {{
    color: {t['foreground']};
    background-color: {t['background']};
    border: 1px solid {t['border']};
    padding: 3px 8px;
}}
QToolButton#MoreOptions {{
    padding: 5px;
}}
QToolButton#MoreOptions::menu-indicator, QToolButton#MoreTools::menu-indicator {{
    image: none;
    width: 0;
}}
QMenu::indicator {{
    width: 14px;
    height: 14px;
    left: 6px;
}}
QMenu::indicator:checked {{
    image: url({_icon_url("check", t["popover_foreground"])});
}}

/* 截图选区的宽高输入框（capture/size_editor.py） */
QFrame#SizeEditor {{
    background-color: {t['popover']};
    color: {t['popover_foreground']};
    border: 1px solid {t['border']};
    border-radius: {T.RADIUS_LG}px;
}}

/* ===== 贴图（pin/window.py）：卡片本体由 paintEvent 画，这里只管浮动提示 ===== */
/* 提示浮在任意截图内容上，明暗主题都用深底浅字，与截图尺寸提示同源。 */
QLabel#PinHud, QFrame#Toast {{
    background-color: rgba{T.CAPTURE_TIP};
    color: {T.DARK['foreground']};
    border-radius: 12px;
    padding: 4px 12px;
    font-size: {T.FONT_SM}px;
}}
QFrame#Toast QLabel {{
    color: {T.DARK['foreground']};
    font-size: {T.FONT_SM}px;
}}
QPushButton#ToastAction {{
    background: transparent;
    color: {T.DARK['foreground']};
    border: none;
    padding: 0;
    font-size: {T.FONT_SM}px;
    font-weight: 600;
    text-decoration: underline;
}}
QPushButton#ToastAction:hover {{
    color: {T.DARK['muted_foreground']};
}}
QTextEdit#PinText {{
    background: transparent;
    border: none;
    selection-background-color: {t['primary']};
    selection-color: {t['primary_foreground']};
}}
"""


def _system_is_dark() -> bool:
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def apply_theme(app: QApplication, mode: str = "system") -> str:
    """应用主题，返回实际生效的 'light' | 'dark'。"""
    if mode == "system":
        mode = "dark" if _system_is_dark() else "light"
    if mode not in T.THEMES:
        raise ValueError(f"unknown theme mode: {mode}")
    t = T.THEMES[mode]
    # QSS 管不到富文本链接色，只能走调色板；链接跟正文同色，靠下划线区分。
    palette = app.palette()
    palette.setColor(QPalette.Link, QColor(t["foreground"]))
    app.setPalette(palette)
    app.setStyleSheet(build_qss(t))
    app.setProperty("theme_mode", mode)
    return mode
