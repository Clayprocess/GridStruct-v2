# 三批功能与交互补齐验收

日期：2026-10-11。起点：`6f408d8`。本记录随三批功能与交互改动一同提交。

本次对齐指定的行为与交互。没有声称复现 PixPin 私有实现，也没有用合成样本证明普遍 OCR 准确率。

## 实现清单

| 编号 | 完成内容 | 主要位置 |
| --- | --- | --- |
| 1 | 长截图未开始时禁用停止按钮，结束入口拒绝未开始的会话 | `capture/longshot.py` |
| 2 | 新贴图保持截图选区的原始全局位置，与标注窗口之后移动无关 | `annotate/window.py`、`capture/flow.py` |
| 3 | 截图控制器统一处理各屏遮罩及 FloatPanel 的键盘事件，拦截 Tab 焦点遍历 | `capture/overlay.py` |
| 4 | 橡皮擦显示圆形尺寸预览，实际擦除区域与预览一致，预览不进入导出 | `annotate/canvas.py`、`annotate/tools.py` |
| 5 | 建选区、移动、缩放按遮罩变化区、边框和提示区刷新 | `capture/overlay.py` |
| 6 | 马赛克单击添加或移除，支持撤销重做 | `annotate/canvas.py` |
| 7 | 双击箭头添加或关闭注释，双击注释编辑，支持撤销重做 | `annotate/canvas.py`、`annotate/items.py` |
| 8 | 工具按钮提示包含对应快捷键 | `annotate/toolbar.py` |
| 9 | 截图及标注阶段支持 Shift+字母切换工具，文字编辑期间保留正常输入 | `capture/overlay.py`、`annotate/window.py` |
| 10 | 箭头起终点分别设置，支持曲线及控制点调整 | `annotate/items.py`、`annotate/options.py` |
| 11 | 折线支持圆头/方头及圆角/尖角连接 | `annotate/items.py`、`annotate/options.py` |
| 12 | 文字支持左/中/右对齐，多行导出保留对齐效果 | `annotate/items.py`、`annotate/options.py` |
| 13 | 框选阶段 FloatPanel 提供取色入口，取色不修改选区 | `capture/flow.py`、`capture/overlay.py` |
| 14 | 取色支持 Hex、RGB、HSV，放大镜中 Tab 切换格式 | `annotate/pipette.py`、`annotate/color_dialog.py` |
| 15 | OCR 结果提供从左到右（LTR）排版方向 | `ocr/dialog.py` |
| 16 | 设置提供跟随系统/简体中文语言选项，切换提示重启 | `settings/dialog.py` |
| 17 | 设置关于页与托盘复用检查更新入口 | `settings/__init__.py`、`settings/dialog.py`、`main.py` |
| 18 | 热键注册失败弹出统一冲突提示，说明问题、原因与可执行操作 | `tray/hotkey.py` |
| 19 | 托盘、截图/标注工具条、Ctrl+Alt+L 接入同一长截图流程 | `capture/flow.py`、`main.py` |
| 20 | 拼接轴硬上限为 29000 物理像素，达到后暂停并禁止继续，保留当前结果 | `capture/longshot.py` |
| 21 | BaseDialog 提供 120ms 淡入 | `ui/dialogs.py` |
| 22 | 贴图创建/关闭提供 150ms 淡入淡出，关闭完成后只保存/发信号一次 | `pin/window.py` |
| 23 | 穿透及透明度切换采用 150ms 渐变，穿透目标为基础透明度的一半 | `pin/window.py` |
| 24 | 截图进入标注提供 120ms 淡入 | `annotate/window.py` |
| 25 | FloatPanel 提供 150ms 淡入及 6px 上滑，未选工具时隐藏二级面板 | `annotate/toolbar.py` |
| 26 | 标注取消按钮改为 outline | `annotate/toolbar.py` |
| 27 | 生产模块 QMessageBox 全部迁移到 BaseDialog 系列，保留相关 checkbox 行为 | `ui/dialogs.py` 及调用模块 |
| 28 | 马赛克占位随明暗主题变化 | `annotate/items.py` |
| 29 | OCR 复制全部 primary 按钮位于最右 | `ocr/dialog.py` |
| 30 | FloatPanel 增加 token 定义的圆角偏移阴影 | `styles/tokens.py`、`annotate/toolbar.py` |
| 31 | OCR 文本悬停显示底色，不污染图像导出 | `ocr/layer.py` |
| 32 | 长截图预览使用 FastTransformation，原始拼接图保持不变 | `capture/longshot.py` |

工具快捷键使用本项目明确的映射：Shift+R 矩形、E 椭圆、A 箭头、P 画笔、L 直线、B 折线、T 文字、N 序号、M 马赛克、H 高亮、Z 橡皮擦、C 放大镜。Shift+O 保留给 OCR。参考材料未明确完整字母表，因此这些映射不标为 PixPin 原始映射。

界面语言目前仅支持简体中文；其他系统语言回退中文。OCR 自动识别中文和英文，与界面语言选项无关。排版方向本次提供 LTR，没有引入 RTL 或竖排。

## 验证结果

- `python -X utf8 tools/smoke_check.py`：21/21，通过真实本地模型识别和禁止下载校验。
- `python -m compileall -q ...`：通过；`git diff --check`：通过，仅存在仓库原有换行转换提示。
- 新增及变更窗口已生成明暗主题截图，检查统一提示对话框、取色、OCR、设置、贴图、长截图条及 FloatPanel。
- 内联 Qt 验收覆盖快捷键、Tab 格式切换、箭头注释/曲线撤销、文字对齐、折线样式、橡皮擦预览与擦除、硬上限、动画、提示对话框和原始采集视口保留。未新增测试文件。

本机可见验收使用 `main.py --desktop-check` 启动当前源码，以及独立合成业务界面；验收屏幕为 PG32UCDMR，物理 3840×2160、逻辑 2560×1440、DPR 1.5。

1. 实际热键启动截图，快速拖拽框选 `(231,188)` 至 `(1106,484)`，选区与鼠标对齐。
2. 实际 Shift+A 进入标注并绘制箭头，工具条和标注响应正常。
3. 实际保存 PNG 并复制图像：两者均为 1314×446，像素完全相同。保存证据：`.local-run/visible-result-20261011.png`。
4. 再次截图标注并贴图：窗口物理原点 `(347,282)`，对应原选区逻辑点 `(231,188)` 的 150% 映射。
5. 贴图实际启动离线 OCR，识别出 15 处文字；鼠标悬停显示底色，拖选并 Ctrl+C 可复制选中文本。
6. 实际打开全文对话框，LTR 选项可见，primary 复制全部按钮在右侧；复制出的 133 字符包含 `Local Capture Review`、`128`、`64`、`32`、`Sample 2026` 和 `本地保存`。

当前主流程可见操作中未出现卡死或原生消息框。保存使用 Windows 标准文件选择器；消息提示使用统一 BaseDialog。这次验证的是当前源码运行，不是重新打包的 EXE；也未作 PixPin 对照延迟测量或双屏混合 DPI 验收。

可见快速拖拽还发现并修正了排队事件读取最新光标而丢失按下点的问题：单屏采用事件坐标，释放时结算最终位置。复核同时修正 Shift+方向键被工具切换截获，以及长图再次进入采集时错误使用结果图尺寸的问题。

## 等待产品确认，保持不动

- 二级面板更改是否追溯更新已画图形。
- 橡皮擦“二次编辑”的具体交互。
- 单击吸附采用方式；继续保留目前的 snap 拖拽覆盖行为。
