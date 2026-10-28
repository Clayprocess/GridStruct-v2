# AGENTS.md — 给 Codex 的协作约定

## 硬约束

1. **视觉唯一真值源是 `styles/tokens.py`**。任何地方不许手写颜色十六进制，必须从 tokens 取。需要新颜色先加到 tokens 再用。
2. **图标优先用 `assets/icons/` 里的 Lucide SVG**，通过 `ui/icons.py` 加载。缺少合适图形时，允许按相同线宽、端点和尺寸规格自绘 SVG，并在 `assets/icons/README.md` 说明用途；不用 emoji 代替。色块、控件状态、数值标签和样式预览可用 QPainter 或真实图元绘制，位图图标通过 `painted_icon` 适配高 DPI，颜色仍从 tokens 取。
3. **QSS 只许出现在 `styles/theme.py`**（由 tokens 生成）。业务代码里不许 `setStyleSheet`。
4. **模块边界见 README 表格**。跨模块默认调用各模块 `__init__.py` 暴露的接口；README 明确列出的公开子模块也可直接导入，当前 `ui.motion` 是共享动效公开接口。
5. 中文注释，英文标识符。Python 3.10+。

## UI 规范（shadcn 翻译版）

- 按钮分 5 种，用 dynamic property `class` 区分：`primary` / `secondary` / `outline` / `ghost` / `destructive`
  ```python
  btn.setProperty("class", "primary")
  ```
- 主工具条按钮用 `QToolButton` + `ghost` 样式，图标 16px，hover 显示文字 tooltip。组切换小箭头用 `QToolButton#ToolCaret` 专用样式和 12px 图标；二级面板控件用各自的 QSS objectName 样式。
- 对话框继承 `ui/dialogs.py` 的 `BaseDialog`（统一样式、按钮排布）。
- 深色/浅色切换走 `styles/theme.py::apply_theme(app, mode)`，`mode` ∈ {"light", "dark", "system"}。

## 做事顺序

按 `../pixpin/pixpin-ui-spec.md` §9 的 P0→P2 阶段走。每阶段结束保证能跑、能点，再进下一阶段。

## 代码风格约束（必读）

写"刚好够用"的代码，禁止防御式编程：

1. **错误处理只在边界**：模块公开接口、文件 IO、网络请求处处理错误。
   内部函数不许 try/except 包裹，不许 `except Exception: pass`，
   不许写"以防万一"的空值检查——调用方保证前置条件。
2. **不许自发写测试**：不要建 `test_*.py` / `tests/` 目录。
   验收用 offscreen 冒烟脚本（参考 `tools/render_check.py` 的写法），
   跑通即算验证通过。除非任务明确要求测试，否则一个不写。
3. **禁止过度设计**：YAGNI。不许为"以后可能用"加的抽象层、钩子、插件机制、
   配置项。一个函数只做一件事，写完能删的就删。
4. **注释写"为什么"**：不许给显而易见的代码加注释，不许复述代码。
   注释只解释：非显而易见的决策、Qt 的坑、产品行为依据。
5. **违反以上任何一条，review 直接打回重写**，不逐行修。

## 验收标准

- 新窗口/对话框必须在明暗两套主题下离屏渲染截图自查（参考 `tools/render_check.py` 的写法）。
- 不引入新依赖先打招呼。PySide6 / onnxruntime 之外的新包要说明理由。
