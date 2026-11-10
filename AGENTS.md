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

## 状态归属（改这些流程前先看）

各模块自己管自己的状态，不引入统一的状态机框架。下面这些状态只能经由列出的入口切换，不要另加布尔开关绕过：

| 状态 | 位置 | 唯一入口 |
| --- | --- | --- |
| 截图会话阶段 `Phase`（空闲/框选/标注/待开长截图/长截图） | `capture/flow.py` | `ScreenshotFlow` 内部赋值 `self._phase`；`start()` 只在 `IDLE` 时开始 |
| 结束一张截图前的确认 | `annotate/window.py` | `AnnotationWindow.confirm_discard()`：用户主动丢弃截图的入口（Esc、工具条关闭/取消、托盘退出、改做长截图）走它；有未保存标注或改过的识别文字才弹确认，设置里关掉“退出确认”则直接通过。程序内部直接 `close()` 不确认：复制、贴图、转表格、打开识别复核这类已把内容交出去的出口，以及 `ScreenshotFlow._cleanup()` / `cancel()` 收尾（托盘退出时已先确认过）。新增会丢内容的结束方式要先调用它，不要直接 `close()` |
| OCR 任务状态 `OCRState` | `ocr/controller.py` | `OCRController._set_state()` / `_enter_stage()`；界面用 `.waiting` / `.retryable`，不手写状态名清单。9 种状态的取值、`stateChanged(str)` 文本和工具条/贴图表现见 `tools/check_ocr_capture_card.py` 的 `STATE_CONTRACT` |
| 表格网格拖线预览 | `table/review.py` | `TableReview.set_grid(final=False)` 预览；被打断时由 `_settle()` 按松开提交 |
| 设置保存 | `settings/dialog.py` | `SettingsDialog._save()`：快捷键、开机自启、配置文件任何一步失败，都尽力把已生效的部分恢复到打开设置时（自启按注册表原值写回）。恢复本身也可能失败（例如原快捷键被别的程序占了），这时不会重试，只在“无法保存设置”的提示里写明哪一项没恢复、请用户重设 |

中途打断（按住鼠标时切工具、翻页、退出）和写入失败是最容易丢数据的地方：新增流程时先写能复现打断/失败的离屏脚本（`tools/check_*.py`）。
