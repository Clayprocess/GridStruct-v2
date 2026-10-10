# QSS 主题生成器：唯一的样式表出口。
# 用法：apply_theme(app, "light" | "dark" | "system")
# 业务代码不许手写 setStyleSheet，一律走这里。

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from . import tokens as T


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

/* ===== 按钮：class=primary（默认主按钮）===== */
QPushButton {{
    background-color: {t['primary']};
    color: {t['primary_foreground']};
    border: none;
    border-radius: {r}px;
    padding: 8px 16px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {t['accent']};
    color: {t['accent_foreground']};
    border: 1px solid {t['border']};
    padding: 7px 15px;
}}
QPushButton:pressed {{
    background-color: {t['secondary']};
    color: {t['secondary_foreground']};
}}
QPushButton:disabled {{
    background-color: {t['muted']};
    color: {t['muted_foreground']};
}}

/* secondary：浅底深字 */
QPushButton[class="secondary"] {{
    background-color: {t['secondary']};
    color: {t['secondary_foreground']};
}}
/* outline：描边按钮 */
QPushButton[class="outline"] {{
    background-color: transparent;
    color: {t['foreground']};
    border: 1px solid {t['border']};
}}
QPushButton[class="outline"]:hover {{
    background-color: {t['accent']};
    border: 1px solid {t['border']};
    padding: 8px 16px;
}}
/* ghost：无框 */
QPushButton[class="ghost"] {{
    background-color: transparent;
    color: {t['foreground']};
}}
QPushButton[class="ghost"]:hover {{
    background-color: {t['accent']};
    color: {t['accent_foreground']};
}}
/* destructive：危险 */
QPushButton[class="destructive"] {{
    background-color: {t['destructive']};
    color: {t['destructive_foreground']};
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
    background-color: {t['primary']};
}}
QToolButton[class="destructive"] {{
    background-color: {t['destructive']};
    border-radius: {T.RADIUS_SM}px;
}}
QToolButton[class="destructive"]:hover {{
    background-color: {t['destructive']};
}}

/* ===== 输入框 ===== */
QLineEdit, QComboBox, QSpinBox {{
    background-color: {t['background']};
    border: 1px solid {t['input']};
    border-radius: {r}px;
    padding: 7px 10px;
    selection-background-color: {t['primary']};
    selection-color: {t['primary_foreground']};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {t['ring']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
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
    width: 14px; height: 14px;
    border: 1px solid {t['border']};
    border-radius: 3px;
    background: {t['background']};
}}
QCheckBox::indicator:checked {{
    background: {t['primary']};
    border-color: {t['primary']};
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
"""


def _system_is_dark() -> bool:
    # Qt 6.5+：按调色板亮度判断
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import Qt
        hint = QGuiApplication.styleHints().colorScheme()
        return hint == Qt.ColorScheme.Dark
    except Exception:
        return False


def apply_theme(app: QApplication, mode: str = "system") -> str:
    """应用主题，返回实际生效的 'light' | 'dark'。"""
    if mode == "system":
        mode = "dark" if _system_is_dark() else "light"
    if mode not in T.THEMES:
        raise ValueError(f"unknown theme mode: {mode}")
    t = T.THEMES[mode]
    app.setStyleSheet(build_qss(t))
    return mode
