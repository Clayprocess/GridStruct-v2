# GridStruct v2 — P0 范围文档

> grill-me 会话结论（2026-10-11）。目标：基本复刻 PixPin（已砍会员/服务端/小众功能）。

## 1. 项目定位

GridStruct v2：对标 PixPin 的截图/贴图/标注/OCR 工具，PySide6 自研实现。
参考资料用法：复用公开知识（UI 规格、架构分层、交互细节），不搬运 PixPin 的模型文件。

**设计原则**（Clay 2026-10-11 定）：有价值的细节就深挖。PixPin 是长期验证的成熟工具，
非显而易见的行为（交互细节、边界处理、久经打磨的默认参数）值得研究；
标准行为按行业惯例做，不浪费时间。

值得深挖的行为细节 backlog：
- 长截图：滚动步进、重叠区域处理、拼接失败回退（P0-B 前）
- 智能吸附：UiRegionDetector 优先级、超时、Win32→UIA 回退顺序
- 序号工具：编号规则、全局编号行为
- 取色器：颜色格式、复制行为
- 贴图：缩放/透明度/穿透的交互细节

## 2. P0 功能清单

### P0-A：截图主链路
- 全屏框选（尺寸提示、ESC 取消、双击确认）
- 智能吸附（窗口/元素矩形高亮）
- 12 个标注工具 + FloatPanel 工具条 + 二级配置面板
  - 矩形、椭圆、箭头、画笔、直线、折线、文字、序号、马赛克、高亮、橡皮擦、放大镜
- 保存 / 复制 / 确认 / 取消
- 取色器
- 托盘菜单 + 全局热键

### P0-B：贴图与长截图
- 贴图：图片 / 文字（拖拽缩放、右键菜单：复制/OCR/保存/置顶/透明度/关闭）
- 长截图（滚动拼接）

### P0-C：OCR 与设置
- OCR 文字识别（RapidOCR + PP-OCRv6 small，onnxruntime，本地）
- 设置窗口（简化版：通用 / 截图 / 热键 / 关于）

### 明确不做（P0）
- 翻译（整项砍掉，以后另议）
- 贴图扩展类型：动图 / 文件 / LaTeX → P1
- 表格识别、公式识别 → P1
- 会员/登录/订阅/云同步/兑换码 → 永不做
- 工业条码、TTS、摄像头画中画、录屏剪辑、多语种切换 → 永不做

## 3. 技术决策

| 项 | 决策 |
|---|------|
| UI 框架 | PySide6（Qt Widgets，不用 QML） |
| 设计语言 | shadcn zinc 翻译版（`styles/tokens.py` 唯一真值源） |
| 图标 | Lucide SVG（33 个已下载，`ui/icons.py` 加载） |
| OCR 模型 | **PP-OCRv6 small**（det small + rec small，onnxruntime；无 cls 模型，复用 v5 cls——RapidOCR ≥3.9 自动处理） |
| OCR 集成 | RapidOCR ≥3.9.0（Python，Apache 2.0），in-process 本地跑，离线可用 |
| 模型打包 | 打进 zip 包（~50MB 模型，开箱即用；v6 单模型覆盖 50 语种，无需按语言下载） |
| 打包 | PyInstaller `--onedir` → zip（单文件 exe 放 P1 验证） |
| 平台 | 只 Windows |
| 配置存储 | 便携目录（跟 zip 包走，换电脑配置跟着走） |

### 为什么选 PP-OCRv6 small
- 2026-06-11 随 PaddleOCR 3.7.0 发布；small 档 7.7M 参数，精度超过 v5 server 版
- 单模型 50 语种（中/英/日 + 46 拉丁语系），PixPin 那种"8 个语言包按需下载"的复杂度直接消失
- RapidOCR 3.9.0+ 已支持，det small + rec small 还是它的默认模型
- 有先例：hushsnap 项目就是 RapidOCR + PP-OCRv6 small in-process 离线跑

## 4. 验收标准

打出 Windows zip 包，在工作电脑日常用一周：
`截图 → 标注 → 复制/保存`、`贴图`、`框选 OCR 复制文字` 三条链路能替代 PixPin，不崩。
达到即 P0 关闭，进 P1（表格/公式/贴图扩展类型/单文件 exe）。

## 5. 执行分工

- 实现：Codex（GPT-6.1 Sol）+ Pax
- 架构评审 / 关键决策：GPT-6 Astra（调用通道待定）
- 验收：Clay（每阶段）

## 6. 输入资产

- UI 规格书：`~/workspace/pixpin/pixpin-ui-spec.md`
- 脚手架：`~/workspace/pixshot/`（主题/图标/AGENTS.md 已就绪）
- 产品知识库：`~/MEMORY.md` 中 PixPin 相关条目
