# Windows 便携目录包；资源路径保持与源码中的 __file__/../assets 一致。
from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH)
# Qt 6.12 使用 Windows ICU；外部工具的 PATH 可使收集器误选同名 ICU 78。
# 仅修改构建子进程，不改用户 PATH；项目依赖仍由各包的 hook 收集。
windows = Path(os.environ["SystemRoot"])
os.environ["PATH"] = os.pathsep.join(str(path) for path in (
    Path(sys.executable).parent, Path(sys.base_prefix),
    Path(sys.base_prefix) / "DLLs", windows / "System32", windows,
))
optional_engines = (
    "rapidocr.inference_engine.mnn",
    "rapidocr.inference_engine.openvino",
    "rapidocr.inference_engine.paddle",
    "rapidocr.inference_engine.pytorch",
    "rapidocr.inference_engine.tensorrt",
)

datas = [
    (str(root / "assets" / "icons"), "assets/icons"),
    (str(root / "assets" / "models"), "assets/models"),
]
# RapidOCR 懒加载入口和 YAML 配置不在静态 import 链中；模型只打包仓库版本。
datas += collect_data_files("rapidocr", excludes=["models/**"])
datas += copy_metadata("rapidocr")
hiddenimports = collect_submodules(
    "rapidocr", filter=lambda name: not name.startswith(optional_engines)
)

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=collect_dynamic_libs("onnxruntime"),
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "torch", "tensorflow", "paddle", "openvino", "tensorrt", "MNN"],
    noarchive=False,
)
for destination, source, _ in a.binaries:
    if Path(destination).name.lower() == "icuuc.dll":
        raise RuntimeError(f"Qt 必须使用 Windows 系统 ICU，不得打包同名第三方 DLL：{source}")
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PixShot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    contents_directory="_internal",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PixShot")
