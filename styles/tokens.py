# 设计真值源：翻译自 shadcn/ui 默认 zinc 主题的 CSS 变量。
# 原始值为 HSL（如 --primary: 240 5.9% 10%），此处已换算为十六进制。
# 任何 UI 代码需要颜色/圆角/间距，必须从这里取，不许手写十六进制。

LIGHT = {
    # 底色与文字
    "background":        "#ffffff",  # --background: 0 0% 100%
    "foreground":        "#09090b",  # --foreground: 240 10% 3.9%
    # 卡片 / 浮层
    "card":              "#ffffff",  # --card
    "card_foreground":   "#09090b",  # --card-foreground
    "popover":           "#ffffff",  # --popover
    "popover_foreground":"#09090b",  # --popover-foreground
    # 主按钮（深底白字）
    "primary":           "#18181b",  # --primary: 240 5.9% 10%
    "primary_foreground":"#fafafa",  # --primary-foreground: 0 0% 98%
    "primary_hover":     "#27272a",  # 对应 shadcn primary/90：hover 只调亮度，不反转
    # 次按钮 / 柔和背景
    "secondary":         "#f4f4f5",  # --secondary: 240 4.8% 95.9%
    "secondary_foreground": "#18181b",
    "secondary_hover":   "#e4e4e7",
    "muted":             "#f4f4f5",  # --muted
    "muted_foreground":  "#71717a",  # --muted-foreground: 240 3.8% 46.1%
    "accent":            "#f4f4f5",  # --accent（hover 背景）
    "accent_foreground": "#18181b",
    # 危险操作
    "destructive":       "#ef4444",  # --destructive: 0 84.2% 60.2%
    "destructive_foreground": "#fafafa",
    "destructive_hover": "#dc2626",
    # 边框与输入框
    "border":            "#e4e4e7",  # --border: 240 5.9% 90%
    "input":             "#e4e4e7",  # --input
    "ring":              "#18181b",  # --ring（focus 环）
    # 设置窗口：右侧内容底色比导航和卡片暗一档，分组卡片浮在上面（同 PixPin 配置窗口的层次）。
    "settings_surface":  "#f4f4f5",
    "settings_card":     "#ffffff",
}

DARK = {
    "background":        "#09090b",  # --background: 240 10% 3.9%
    "foreground":        "#fafafa",  # --foreground: 0 0% 98%
    "card":              "#09090b",
    "card_foreground":   "#fafafa",
    "popover":           "#09090b",
    "popover_foreground":"#fafafa",
    "primary":           "#fafafa",  # 深色下主按钮反转为白底黑字
    "primary_foreground":"#18181b",
    "primary_hover":     "#e4e4e7",
    "secondary":         "#27272a",  # --secondary: 240 3.7% 15.9%
    "secondary_foreground": "#fafafa",
    "secondary_hover":   "#3f3f46",
    "muted":             "#27272a",
    "muted_foreground":  "#a1a1aa",  # --muted-foreground: 240 5% 64.9%
    "accent":            "#27272a",
    "accent_foreground": "#fafafa",
    # shadcn 深色 #7f1d1d 在 Windows 深色背景上像禁用态，提亮到 red-600。
    "destructive":       "#dc2626",
    "destructive_foreground": "#fafafa",
    "destructive_hover": "#b91c1c",
    "border":            "#27272a",
    "input":             "#27272a",
    "ring":              "#d4d4d8",  # --ring: 240 4.9% 83.9%
    # 深色下卡片要比底色亮一档才分得出层次。
    "settings_surface":  "#111113",
    "settings_card":     "#1c1c1f",
}

# 设置窗口：导航列宽、导航项高度（PixPin 实测约 154 / 35 逻辑像素），右侧控件列宽。
SETTINGS_NAV_WIDTH = 168
SETTINGS_NAV_ITEM = 36
SETTINGS_CONTROL_WIDTH = 220

# 圆角（--radius: 0.5rem = 8px）
RADIUS_SM = 4
RADIUS_MD = 6
RADIUS_LG = 8

# 间距基数 4px
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16

# 字号
FONT_SM = 12
FONT_MD = 13
FONT_LG = 15
FONT_XL = 20

THEMES = {"light": LIGHT, "dark": DARK}

ANNOTATION_COLORS = ["#ef4444", "#18181b", "#ffffff", "#3b82f6", "#22c55e", "#eab308"]
CAPTURE_MASK = (0, 0, 0, 110)
CAPTURE_GUIDE = (255, 255, 255, 140)
CAPTURE_TIP = (9, 9, 11, 230)
CAPTURE_BACKGROUND = (0, 0, 0)
PIXEL_GRID = (0, 0, 0, 60)
FLOAT_PANEL_SHADOW = (0, 0, 0, 42)
# 贴图阴影：由外向内逐层叠加的 alpha，层数即阴影宽度（px）。
PIN_SHADOW_ALPHAS = (3, 5, 8, 11, 15, 20)
# 贴图状态色（同 PixPin）：操作中蓝、锁定橙、鼠标穿透绿，其余时间是普通黑影。
PIN_STATE_COLORS = {"active": "#3b82f6", "locked": "#f97316", "click_through": "#22c55e"}
# 贴图缩放/不透明度提示停留时长。
PIN_HUD_MS = 1200
# 同 PixPin 默认：Ctrl+滚轮每格 5%；鼠标穿透时不透明度最高 50%。
PIN_OPACITY_STEP = 0.05
PIN_CLICK_THROUGH_OPACITY = 0.5
# OCR 原图选字：只叠半透明底色，不遮住原图字形；明暗主题共用（叠在截图像素上，与界面主题无关）。
OCR_SELECTION = (59, 130, 246, 96)
OCR_HOVER = (59, 130, 246, 28)
# 应用标识点阵在 EXE、窗口上的颜色：无底色，要在浅色和深色背景上都看得见，取中等明度的靛蓝。
APP_ICON_COLOR = "#6366f1"
# 复制、保存等结果提示的停留时长。
TOAST_MS = 1600
# 带操作按钮的提示要留出移动鼠标去点的时间。
TOAST_ACTION_MS = 4000
