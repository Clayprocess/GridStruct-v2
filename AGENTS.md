# AGENTS.md — 给 Codex 的协作约定

## 硬约束

1. **视觉唯一真值源是 `styles/tokens.py`**。任何地方不许手写颜色十六进制，必须从 tokens 取。需要新颜色先加到 tokens 再用。
2. **图标只用 `assets/icons/` 里的 Lucide SVG**，通过 `ui/icons.py` 加载。缺图标先说，不要自己画、不要用 emoji 代替。
3. **QSS 只许出现在 `styles/theme.py`**（由 tokens 生成）。业务代码里不许 `setStyleSheet`。
4. **模块边界见 README 表格**。跨模块调用只走各模块 `__init__.py` 暴露的公开接口。
5. 中文注释，英文标识符。Python 3.10+。

## UI 规范（shadcn 翻译版）

- 按钮分 5 种，用 dynamic property `class` 区分：`primary` / `secondary` / `outline` / `ghost` / `destructive`
  ```python
  btn.setProperty("class", "primary")
  ```
- 工具条按钮用 `QToolButton` + `ghost` 样式，图标 16px，hover 显示文字 tooltip。
- 对话框继承 `ui/dialogs.py` 的 `BaseDialog`（统一样式、按钮排布）。
- 深色/浅色切换走 `styles/theme.py::apply_theme(app, mode)`，`mode` ∈ {"light", "dark", "system"}。

## 做事顺序

按 `../pixpin/pixpin-ui-spec.md` §9 的 P0→P2 阶段走。每阶段结束保证能跑、能点，再进下一阶段。

## 验收标准

- 新窗口/对话框必须在明暗两套主题下离屏渲染截图自查（参考 `tools/render_check.py` 的写法）。
- 不引入新依赖先打招呼。PySide6 / onnxruntime 之外的新包要说明理由。
