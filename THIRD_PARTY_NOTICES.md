# 第三方许可与来源

GridStruct-V2 自有代码使用 [MIT 许可证](LICENSE)。下列组件、图标和模型保留各自的权利人和许可。便携 ZIP 的 `licenses/` 包含随包许可文本，`licenses/packages/` 保存构建时 Python 依赖提供的许可与归属文件；版本可查包内的 `build-manifest.json` 和对应目录名。

## 图标

`assets/icons/` 中的 Lucide 图标使用 ISC 许可。上游清单列出的 Feather 衍生图标还保留 Cole Bemis 的 MIT 声明。项目自绘图标见该目录的 README。[Lucide 原文与 Feather 清单](licenses/Lucide.txt) · [上游](https://github.com/lucide-icons/lucide/blob/main/LICENSE)

## 本地模型

| 随包文件 | 来源与归属 | SHA-256 |
| --- | --- | --- |
| `PP-OCRv6_det_small.onnx` | PaddleOCR 检测模型，RapidOCR 转为 ONNX 并分发 | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` |
| `PP-OCRv6_rec_small.onnx` | PaddleOCR 识别模型，RapidOCR 转为 ONNX 并分发 | `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` |
| `slanet-plus.onnx` | PaddleOCR 的 SLANet_plus 表格结构模型，与 rapid-table 1.0.3 发行文件一致 | `d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b` |

上述模型适用 [Apache License 2.0](licenses/Apache-2.0.txt)。PP-OCRv6 上游权重版权归 Baidu 及相应 PaddleOCR 权利人，转换和打包工作归 RapidOCR 作者。GridStruct-V2 原样分发上述 ONNX 文件。表格结构的预处理和解码参考了 RapidTable 1.0.3 的实现。[RapidOCR 3.10.0 模型声明](https://pypi.org/project/rapidocr/3.10.0/) · [检测模型卡](https://huggingface.co/PaddlePaddle/PP-OCRv6_small_det_onnx) · [识别模型卡](https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx) · [RapidTable 发行来源](https://pypi.org/project/rapid-table/1.0.3/)

## Windows 运行库

- PySide6、Shiboken6 和本应用使用的 Qt 模块使用 Qt 的开源许可选项。便携包使用动态 Qt 库。LGPLv3 允许接收者为自己使用而修改这些库，并为调试这些修改进行逆向工程。Qt PDF 包含 PDFium，另有 PDFium 及其依赖的许可与归属。[LGPLv3](licenses/LGPL-3.0.txt) · [GPLv3](licenses/GPL-3.0.txt) · [Qt 源码发行档案](https://download.qt.io/archive/qt/) · [PySide 源码](https://code.qt.io/cgit/pyside/pyside-setup.git/) · [PDFium 文本](licenses/PDFium.txt) · [Qt PDF 许可说明](https://doc.qt.io/qt-6/qtpdf-licensing.html)
- RapidOCR 引擎按 Apache-2.0 分发，ONNX Runtime 按 MIT 分发并附有第三方声明，XlsxWriter 按 BSD-2-Clause 分发。OpenCV、NumPy 等随依赖安装的组件各自保留发行包中的条款。[RapidOCR](https://github.com/RapidAI/RapidOCR) · [ONNX Runtime](https://github.com/microsoft/onnxruntime) · [XlsxWriter](https://github.com/jmcnamara/XlsxWriter)
- 程序包含 CPython 解释器和 PyInstaller 引导程序。便携包附有该版本 Python 发行版的许可和 PyInstaller 包含引导程序例外条款的许可文本。[Python 许可](https://docs.python.org/3/license.html) · [PyInstaller 许可](https://pyinstaller.org/en/stable/license.html)
