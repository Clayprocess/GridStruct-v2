# GridStruct-V2

Windows 本地截图、标注、贴图与 OCR 工具，对标 PixPin 的常用操作。程序、托盘和新构建产物统一命名为 **GridStruct-V2**。

范围以 [P0 范围文档](docs/p0-scope.md) 为准，交互实现参考同级目录中的 [docs-clean](../docs-clean/README.md)。下列功能已接入源码；实机验收状态见文末，不代表 P0 已全部验收通过。

## 当前功能

- **截图**：虚拟桌面框选、窗口/元素吸附（滚轮切换父/子元素）、选区移动与八方向缩放、尺寸提示（点击可输入宽高并锁定比例）；`R` 复用上次选区，`Ctrl+A` 选中当前屏幕、再按选中全部屏幕；`C` 复制鼠标处颜色，单按 `Shift` 切换颜色格式；方向键移动 1px，Shift+方向键收边、Ctrl+方向键扩边，拖拽时按住 Shift 锁定 1:1。选区建立后显示浮动工具条，进入标注时保留选区外遮罩。
- **标注**：矩形、椭圆、箭头、画笔、直线、折线、文字、序号、马赛克、高亮、橡皮擦、放大镜，共 12 种工具。工具条同 PixPin 分主区和次区：主区依次是几何（矩形/椭圆，在二级面板切换）、折线、箭头、序号、画笔、高亮、马赛克、文字、橡皮擦，对应 `Shift+1`～`Shift+9`；直线、放大镜和取色放在悬停展开的次区，最近用过的次区工具会浮到主区；支持选择、移动、删除、撤销和重做。二级面板提供各工具的颜色、粗细或专属选项。
- **样式与编辑**：矩形填充/圆角、7 种箭头、5 种线型、文字字体/字号/背景/描边、序号样式（圆形/描边圆形/方形数字、圆形字母）、起点与编号调整、像素化/模糊马赛克、高亮透明度、橡皮大小、放大倍率。`Ctrl+滚轮`调当前颜色透明度；默认所有工具共用一个颜色（高亮单独记色）；复制、保存后在屏幕底部给出提示，保存的默认文件名带时间戳；画笔按住 Shift 画直线；文字和折线先用 Esc 结束当前编辑。
- **输出与取色**：截图复制、保存 PNG/JPG、贴图、OCR；保存成功后复制文件路径。取色支持 RGB/Hex/HSV/HSL，确认后复制色值并可应用到当前标注。
- **图片和文字贴图**：拖动、滚轮缩放、`Ctrl+滚轮`调不透明度（每格 5%，10%～100%）、方向键微移、中键切换原始大小、`Space` 标注（确认后替换贴图）、`T` 置顶、`L` 锁定、鼠标穿透（穿透时不透明度最高 50%）；`Esc`、`Ctrl+W` 或双击关闭。阴影颜色区分状态：蓝色为当前、灰色为非当前、橙色为锁定、绿色为穿透；托盘可退出所有贴图的鼠标穿透。文字支持滑选复制，右键编辑；图片支持 OCR。应用退出后恢复未关闭的贴图，主动关闭的贴图不会再次恢复。
- **长截图**：手动/自动滚动、纵向/横向拼接、开始/暂停/继续、预览和完成后标注。连续采集只抓选区，唯一重叠经原像素确认后追加。失配时控制条提示回滚，重新定位自动恢复且保留历史；普通反向滚动仍在唯一匹配时裁尾。目标窗口变化或达到上限时暂停。不同滚动应用的兼容性仍需实机确认，阶段 A 验收与性能边界见[验收记录](docs/stage-a-longshot-acceptance.md)。
- **OCR**：后台串行本地识别，区分排队、首次加载、识别、完成/空结果、取消和失败/超时。保留原始四点框、文字与置信度，用稳定条目标识整理版面。截图和图片贴图叠加可选文字层；右键打开逐条/逐段校正，原图、复制全文和贴图同步更新；全文自由编辑作为独立导出草稿。相同截图转贴图复用结果，校正和草稿随会话恢复；图像内容变化后结果失效。使用随项目提供的 PP-OCRv6 small 检测/识别模型和本地方向分类模型，不在识别时下载模型。验收范围见[B–D 记录](docs/stages-bcd-ocr-acceptance.md)。
- **设置与托盘**：明暗/系统主题、截图保存选项、工具颜色联动、部分工具偏好记忆、开机启动和关于页。当前截图热键固定为 `Ctrl+Alt+S`，设置页展示该快捷键。

## 从源码运行

需要 Windows、Python 3.10+。在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 main.py
```

应用常驻系统托盘，无常规主窗口。按 `Ctrl+Alt+S` 框选，使用浮动工具条开始标注或输出；双击选区或按 Enter 复制截图并结束。右键托盘可打开设置、从剪贴板创建贴图、开始长截图或退出。

运行依赖为 PySide6、RapidOCR 和 onnxruntime。安装依赖需要对应安装源；安装完成且本地模型齐全后，OCR 推理不需要联网。以下文件必须位于 `assets/models/`：

```text
PP-OCRv6_det_small.onnx
PP-OCRv6_rec_small.onnx
ch_ppocr_mobile_v2.0_cls_mobile.onnx
```

长截图控制条和遮罩的窗口捕获排除依赖 Windows 10 2004 或更新版本。多显示器和 DPI 适配已有实现，混合缩放组合需要在实际设备上确认。

## 便携数据与 Windows ZIP

配置保存为 `user-data/settings.ini`：源码运行时位于项目根目录，打包后位于 `GridStruct-V2.exe` 所在目录。首次创建 INI 时会迁移旧的 `GridStruct/GridStruct` 注册表配置，不删除原配置。

未关闭贴图的会话与图片保存在 `user-data/pins/`。将整个应用目录连同 `user-data/` 一起移动可保留配置和贴图；这是当前会话恢复，不是可浏览的贴图历史库。应用目录需要可写。 OCR 原始结果、布局、已应用校正和导出草稿保存在 session.json 的有版本字段中，与原始 PNG 内容及 DPR 绑定。旧会话仍可恢复；损坏 OCR 时保留图片并提示，暂停自动保存以保护原校正。

打包采用 **PyInstaller 6.x onedir + ZIP**。只有构建包时才需要另行安装 PyInstaller：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pip install "pyinstaller>=6,<7"
.\.venv\Scripts\python.exe -X utf8 tools/build_portable.py --check
.\.venv\Scripts\python.exe -X utf8 tools/build_portable.py
```

`--check` 只检查构建依赖和资源。构建默认输出到新的 `dist/portable-时间戳/`，其中包含 `GridStruct-V2/` 和 `GridStruct-V2-portable.zip`；也可用 `--output 新目录` 指定位置，已有目录不会覆盖。

ZIP 包含运行依赖、图标与本地模型，不带本机 `user-data/`。使用时完整解压到可写目录，启动 `GridStruct-V2.exe`，保留旁边的 `_internal/`；无需另外安装 Python。构建脚本校验资源哈希，并在 `build-manifest.json` 中记录依赖版本。资源校验不等于目标电脑运行验收，清单中的 `runtime_verified` 默认仍为 `false`。

构建还会在独立进程加载包内 Qt 原生 DLL，避免仅有文件但不能导入的情况；通过后记录 `native_dependency_load_verified=true`。已修复构建环境误收第三方 ICU 导致 QtWidgets 无法导入的问题，构建进程的依赖搜索路径与用户系统配置隔离。

发布走 GitHub Releases：先把 `settings/dialog.py` 的 `_VERSION` 改成新版本并合入 master，再推送同名附注标签（如 `git tag -a v0.1.0` 后 `git push origin v0.1.0`）。`.github/workflows/release.yml` 在 Windows runner 上执行上面的构建，把 `GridStruct-V2-<标签>-portable.zip` 挂到 Release，发布说明取自标签正文。标签与 `_VERSION` 不一致时构建直接失败。

## 模块边界

跨模块调用必须从各模块 `__init__.py` 导入公开接口，不直接导入内部实现文件。

| 模块 | 职责 | 主要公开接口与边界 |
|---|---|---|
| `capture/` | 屏幕管理、桌面捕获、选区、吸附、截图流程、长截图 | `ScreenManager`、`DesktopCapture`、`ScreenFrame`、`ScreenshotFlow`、`LongShotFlow`、`ScreenShotWindow`、`RegionDetector`；将选区/拼接结果交给标注流程 |
| `annotate/` | 标注画布、工具、样式和浮动工具条 | `AnnotationWindow`、`AnnotationCanvas`、`MarkStyle`、`FloatPanel`、`create_tool`；输出动作调用其他模块的公开接口 |
| `pin/` | 图片/文字贴图、窗口管理、会话保存恢复 | `show_pin`、`show_text_pin`、`show_pin_from_clipboard`、`restore_pins`、`save_pins`、`shutdown_pins`、`disable_click_through`；图片识别调用 OCR 接口 |
| `ocr/` | 原始证据和校正文档、本地推理、后台任务、复核对话框与文字层 | `OCREngine`、`OCRResult`、`OCRDocument`、`OCRController`、`OCRResultDialog`、`add_text_layer`、`format_text`；不包含表格/公式识别 |
| `export/` | 截图图片保存与保存路径复制 | `save_image`；当前支持 PNG/JPG，不包含 xlsx 导出 |
| `settings/` | 设置窗口、便携 INI 和数据目录 | `SettingsDialog`、`get_settings`、`data_directory` |
| `tray/` | 托盘入口和 Windows 全局热键 | `create_tray`、`GlobalHotkey` |
| `styles/` | 设计 tokens 与主题 | `tokens`、`apply_theme`；视觉值只定义在 `styles/tokens.py`，QSS 只写在 `styles/theme.py` |
| `ui/` | 通用图标与对话框基类 | `tinted_icon`、`BaseDialog`；图标来自 `assets/icons/` 中的 Lucide SVG |

UI 使用 PySide6 Qt Widgets，不使用 QML。业务模块通过动态属性选择按钮样式，不直接写 QSS。

## 范围外功能

表格/公式识别、动图/文件/LaTeX 贴图、单文件 EXE 属于 P1；本轮不实现 AI 自动打码、曲线等扩展标注功能。翻译以后另议。

不做会员、登录、订阅、云同步、兑换码、工业条码、TTS、摄像头画中画、录屏剪辑和多语种切换界面。当前也不提供可浏览的贴图历史库或完整的自定义热键管理。

## 验证范围

本轮使用真实 Windows 桌面窗口进行验收，未运行离屏渲染。已操作框选、浮动工具条、矩形标注、取色格式切换、文字贴图编辑及退出恢复、设置窗口层级、本地 OCR 和原图文字选择复制；OCR 原图文字重影、画布边距及置顶贴图遮挡设置的问题已修复并复测。验收使用合成数据窗口，辅助入口调用正式应用回调，不作为发布功能。

OCR 后续补齐也已实机验证：截图与图片贴图默认保留原图文字层，滑选复制得到实际文字，右键可主动打开全文复核；图片贴图放大到 110% 后选字区域仍与原图对齐。段落整理通过乱序行、同行片段、双列及跨栏标题等纯数据检查；该算法为几何启发式，尚未验证复杂业务版面的准确率。

自动长截图已在真实桌面合成页面完成从顶部到底部的连续采集：初始 1012 像素，加上实际滚动 2066 逻辑像素 × 1.5 DPR，导出尺寸精确为 1149×4111，包含最后 39 像素短步；到底后无变化一秒自动暂停。长图末尾可滚动浏览和标注，完整导出保留末尾标记。实测修复了反向滚轮、钩子线程超时、重复行误配和短步拼接缓慢。自动滚动采用一格滚轮加稳定帧确认，与参考文档的固定步长/间隔不同，以适配不同应用的滚轮设置。

这些结果不代表所有应用与屏幕环境都已通过。P0 关闭还要求按范围文档，在工作电脑日常使用 ZIP 包一周，确认截图输出、贴图与 OCR 三条链路稳定。

2026-10-11 已在打包 EXE 上完成可见桌面交互验收：`portable-20261011-130851` 跑通热键框选、矩形/箭头、撤销/重做、PNG 保存、图片复制贴图、110% 缩放与 JPG 保存。实际 PNG/JPG 均为 1066×549 像素。

`portable-20261011-131450` 修复了“箭头仍选中时抢占 OCR 文字右键菜单”的问题，并已重新实测热键截图、箭头标注、OCR 文字菜单、全文复核与复制、截图直接贴图、贴图 OCR 滑选复制、由文字剪贴板创建贴图，以及退出后恢复图片和文字贴图。复制结果通过读取系统剪贴板核对。验收证据保存在 `.local-run/packaged-ocr-review.png`、`.local-run/packaged-pin-ocr.png`，输出样本为 `.local-run/packaged-annotated.png` 和 `.local-run/packaged-pin.jpg`。

最近一次完成正式包桌面验收的是 `portable-20261011-135117`（当时名称为 PixShot）。针对 OCR 看起来没有启动，已修复识别完成没有明显反馈的问题：截图工具条持续显示状态并提供“查看全文”，贴图底部显示识别状态和原图选字说明；失败、取消、超时和无文字也有状态提示。实机正式包识别合成样本得到 13 处文字，全文入口与中文复制通过；这不等于已复现所有用户环境中的模型启动失败。证据为 `.local-run/ocr-feedback-fixed.png` 和 `.local-run/ocr-pin-feedback-fixed.png`。

控件吸附修复了 UIA 首个重叠容器提前返回、零面积结构父节点丢弃子树的问题。纯 provider 回归先失败后通过；可见桌面源码验证了独立 Qt 按钮和输入框，新正式包验证了资源管理器文件名控件的蓝色吸附框。未暴露可访问性元素的自绘界面仍可能只能吸附窗口。回归脚本为 `tools/check_snap_provider.py`（纯数据）和 `tools/check_ocr_feedback.py`（可见业务窗口、模拟模型结果）。

桌面自动化验收使用同一个正式 EXE 的显式参数 `GridStruct-V2.exe --desktop-check`：将浮动窗口暴露为可枚举窗口，并显示调用正式业务回调的入口。正常启动不带参数，保持原有浮动窗口行为。未进行离屏操作；该模式的通过不等于普通窗口模式、多屏混合 DPI 或一周日常稳定性均已验证。构建清单仍保留构建时的 `runtime_verified=false`，具体交互范围以本节为准。

仓库保留以下开发辅助脚本，需按验证目的显式运行：

| 脚本 | 用途与限制 |
|---|---|
| `tools/smoke_check.py` | 离屏合成样本回归，覆盖标注、OCR、拼接、设置和生命周期等；2026-10-18 在 master `d6f0d5b` 上离屏（Linux，`QT_QPA_PLATFORM=offscreen`）重跑 21/21 通过，未覆盖真实桌面交互 |
| `tools/windows_smoke.py` | 会显示合成窗口，检查 Windows 吸附、热键、滚轮钩子及捕获排除；不能代替真实应用滚动与交互验收 |
| `tools/build_portable.py --check` | 构建依赖与资源预检；不生成包、不启动应用 |

合成 OCR 样本只验证回归和接线，不代表真实业务文字准确率。混合 DPI、多屏交互、不同滚动应用和打包后的目标电脑运行需分别记录实测结果。

2026-10-11 合并远程更新后，应用与构建产物统一为 GridStruct-V2；内部自启项和单实例锁保留旧标识，避免升级后重复启动。整合了固定 11×11 @8× 跟随放大镜和窗口快照预览，并保留后台 UIA 控件精化及 OCR 反馈修复。合并后通过源码编译、吸附 provider 回归、可见业务窗口 OCR 反馈回归与模拟注册表兼容检查；`portable-20261011-141553` 构建、资源与原生依赖加载、ZIP CRC 校验通过。该合并包尚未重新完成真实桌面全流程验收，前述旧包实测结果不覆盖新增放大镜和快照行为。


## 多显示器与混合 DPI

进程通过 `app.manifest` 和启动时的 `SetProcessDpiAwarenessContext(-4)` 声明 PerMonitorV2；`SetProcessDpiAwareness(2)` 仅作为不支持 V2 系统的兼容回退。两者的区别见 [Microsoft DPI 初始化说明](https://learn.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process)。

`capture_virtual_desktop()` 返回 `DesktopCapture`，逐屏保留原生 `QPixmap` 和 DPR，不再创建统一最大 DPR 的整桌面位图。每屏有独立覆盖层，共享全局逻辑选区控制器。单屏裁切保持原生像素和 DPR；跨屏裁切按各屏物理位置拼接为 DPR=1 的图片，不重采样，屏幕排列或缩放差异形成的空白保留。跨屏结果在可滚动标注视口中编辑。

屏幕坐标通过 `ScreenManager` 的 `whole_rect`、`screen_at`、点/矩形双向换算进入；图像自身的局部坐标使用同一模块的 `image_*` 方法，避免把标注图片 DPR 误当所在显示器 DPR。Qt 在 Windows 上的逻辑屏幕排列可能有空洞，屏幕原点来自原生监视器信息，参见 [Qt High DPI](https://doc.qt.io/qt-6/highdpi.html)。

屏幕管理使用原生监视器枚举和 Qt 信息兜底、互斥刷新，监听添加/移除、几何/DPI 与 `TaskbarCreated`。捕获仍使用 Qt `grabWindow`，本次没有实现 PixPin DLL 的三套捕获后端。每屏尺寸异常最多重抓两次；原生尺寸用于校验，避免非整数缩放下逻辑尺寸反乘产生一像素误差。截图选区阶段遇到屏幕变化会隐藏旧覆盖层、清空旧选区、重新捕获并重建；已进入标注时结束旧截图会话。长截图会暂停并保留已有结果，防止把不同尺寸或 DPR 的帧混入拼接。

2026-10-11 本机只有一块 3840×2160、150% 显示器。已验证：源码进程 PerMonitorV2、原生整屏裁切逐像素一致、Windows 原生冒烟、BFS 四项、可见 Qt 业务窗口的截图/长截图生命周期与高 DPI 标注/OCR；内存检查覆盖负原点、100/125/150/200/225% 坐标和裁切、混合 DPR 原生拼接以及重试次数。向自身隐藏接收窗口发送 `TaskbarCreated` 后刷新通过，不等于实际重启 Explorer。脚本中的截图复制在边界记录输出，不等于真实系统剪贴板验收。

尚未验证：两块真实混合 DPI 显示器上的文字清晰度、鼠标跨屏连续框选、截图期间真实分辨率/缩放切换、物理插拔，以及任务管理器 DPI 列人工核对。桌面控制工具的应用授权等待超时，真实鼠标验收未完成；上述程序内检查不能替代这些硬件与交互验收。
