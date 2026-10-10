# PixShot（暂定名）

对标 PixPin 的截图/贴图/标注工具，自研实现。

## 技术栈

- **UI**: PySide6（Qt for Python）—— Qt Widgets，**不用 QML**
- **设计语言**：翻译自 shadcn/ui（zinc 主题），见 `styles/tokens.py`（唯一的视觉真值源）
- **图标**：Lucide SVG（MIT），见 `assets/icons/`，用 `ui/icons.py` 加载
- **OCR**（二期）：RapidOCR + onnxruntime（PaddleOCR 系模型，本地跑）
- **打包**：PyInstaller / Nuitka → 单文件 exe

## 模块划分

| 模块 | 职责 | 边界 |
|------|------|------|
| `capture/` | 截图捕获、多显示器/DPI 适配、全局热键 | 只输出 QPixmap + 显示器信息 |
| `annotate/` | 标注画布（QGraphicsView/Scene）、12 种标注工具、FloatPanel 工具条 | 不碰文件 IO |
| `pin/` | 贴图窗口（6 种子类型）、贴图管理 | 不碰 OCR |
| `ocr/` | ONNX 推理封装（检测+识别）、表格/公式 | 纯函数式，输入图输出结构化结果 |
| `export/` | xlsx/图片导出、剪贴板 | 只做格式转换 |
| `styles/` | 设计 tokens + QSS 主题生成 | **视觉的唯一真值源** |
| `ui/` | 通用 UI 构件（图标加载、对话框基类） | 不写业务逻辑 |

## 不做的功能（对照 PixPin 砍掉）

会员/登录/订阅、云同步、工业条码、TTS、摄像头画中画、录屏剪辑、多语种 OCR（只留中/英）

## UI 规格

见 `../pixpin/pixpin-ui-spec.md`（PixPin 逆向规格书：窗口清单、工具条按钮、菜单项）

## 运行

```bash
pip install -r requirements.txt
python main.py   # 主题演示窗口
```
