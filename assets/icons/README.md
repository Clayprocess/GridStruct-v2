# 图标资产

Lucide 图标（MIT 协议），SVG 格式，`stroke="currentColor"`。

- 来源：https://lucide.dev / https://github.com/lucide-icons/lucide
- 加载：`from ui.icons import icon, tinted_icon`
- 缺图标时从 lucide.dev 按 kebab-case 名下载，放到本目录即可。
- 注意：新版 Lucide 把 `trash-2` 改名为 `trash`，本目录已同步。

## 自绘图标

Lucide 没有合适图形的几个按钮，按 Lucide 同样的规格自绘（24×24、`stroke-width="2"`、圆头圆角、`currentColor`），
保证和其余图标同一套线宽与观感：

- `circle-number.svg`：序号，圈 1（同 PixPin）
- `checkerboard.svg`：马赛克，细棋盘格；粗棋盘格在 16px 下比其他图标重一截
- `polyline.svg`：折线，带两端端点的折线（Lucide `spline` 是曲线）
- `longshot.svg`：长截图，上下断开的截图框 + 向下箭头，表示往下继续截
