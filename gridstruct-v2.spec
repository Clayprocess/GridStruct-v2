# Windows 便携目录包；资源路径保持与源码中的 __file__/../assets 一致。
from pathlib import Path
import os
import re
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

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
    (str(root / "assets" / "app"), "assets/app"),
    (str(root / "assets" / "models"), "assets/models"),
]
# RapidOCR 懒加载入口和 YAML 配置不在静态 import 链中；模型只打包仓库版本。
datas += collect_data_files("rapidocr", excludes=["models/**"])
datas += copy_metadata("rapidocr")
hiddenimports = collect_submodules(
    "rapidocr", filter=lambda name: not name.startswith(optional_engines)
)

def analysis(script):
    return Analysis(
        [str(root / script)],
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


VERSION = re.search(r'_VERSION = "(.+)"', (root / "settings" / "dialog.py").read_text(encoding="utf-8")).group(1)


def version_info(name, description):
    # 给两个入口写入一致的产品与版本标识，便于识别构建来源。
    numbers = (*map(int, VERSION.split(".")), 0)
    strings = {
        "CompanyName": "Clayprocess",
        "FileDescription": description,
        "FileVersion": VERSION,
        "InternalName": name,
        "LegalCopyright": "Copyright © 2026 Clayprocess",
        "OriginalFilename": f"{name}.exe",
        "ProductName": "GridStruct V2",
        "ProductVersion": VERSION,
    }
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("080404B0", [StringStruct(key, value) for key, value in strings.items()])]),
            VarFileInfo([VarStruct("Translation", [0x0804, 1200])]),
        ],
    )


def executable(a, name, description):
    return EXE(
        PYZ(a.pure),
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        manifest=str(root / "app.manifest"),
        icon=str(root / "assets" / "app" / "gridstruct.ico"),
        version=version_info(name, description),
        contents_directory="_internal",
    )


# 托盘主程序与识别文档独立入口同包：两个 EXE 共用一份 _internal（Qt、onnxruntime、模型），包体积基本不变。
tray = analysis("main.py")
document = analysis("document_main.py")
for destination, source, _ in tray.binaries + document.binaries:
    if Path(destination).name.lower() == "icuuc.dll":
        raise RuntimeError(f"Qt 必须使用 Windows 系统 ICU，不得打包同名第三方 DLL：{source}")
coll = COLLECT(
    executable(tray, "GridStruct-V2", "GridStruct V2 截图与文字识别"),
    executable(document, "GridStruct-V2 识别文档", "GridStruct V2 识别文档"),
    tray.binaries, tray.datas, document.binaries, document.datas,
    strip=False, upx=False, name="GridStruct-V2",
)
