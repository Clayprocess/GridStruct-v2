# 参与开发

使用 Windows 和 Python 3.10 或更高版本。日常回归使用 Python 3.12。在仓库根目录打开 PowerShell，创建环境并安装开发依赖。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## 提交前自检

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -v -m "not windows_integration"
.\.venv\Scripts\python.exe -X utf8 tools/smoke_check.py
```

pytest 覆盖单实例、架构边界和表格校对。离屏冒烟使用合成图片，涵盖截图、标注、贴图、真实本地 OCR 和明暗渲染。仅检查跨列表格时，也可运行 `python tools/check_table.py --cross-column-only`，它复用 pytest 中的场景。

按改动选择已有专项。修改设置保存时运行 `tools/check_settings_save.py`，修改表格拖线或回退时运行 `tools/check_table_interrupt.py`，修改退出确认时运行 `tools/check_annotation_end.py`。CI 还会运行 OCR 窗口和截图卡片回归。运行可见桌面探测前，在 PowerShell 中执行 `Remove-Item Env:QT_QPA_PLATFORM` 以恢复系统显示平台。

## 提交说明

说明用户可观察的行为、改动原因和实际运行过的验证。窗口改动按项目约定检查明暗渲染；涉及全局热键、跨屏 DPI 或桌面捕获时，写明实机环境和覆盖范围。模块边界、样式与状态归属见[开发约定](AGENTS.md)，Windows 便携包构建和验收范围见[项目说明](README.md)。
