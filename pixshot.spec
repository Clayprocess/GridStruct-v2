# PyInstaller 打包规格（在 Windows 上运行）。
# 用法：pyinstaller pixshot.spec
# 产物：dist/PixShot/ 目录，打 zip 经 OneDrive 分发。
#
# 注意：
#   - 必须在 Windows 上执行（不支持交叉编译）
#   - OCR 模型（assets/models/，31MB）随包发布
#   - onnxruntime 需要其 DLL，打包时自动收集

import os

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[
        ("assets/icons", "assets/icons"),
        ("assets/models", "assets/models"),
    ],
    hiddenimports=[
        "rapidocr",
        "onnxruntime",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PixShot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 托盘应用：无控制台窗口
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.data,
    strip=False,
    upx=True,
    name="PixShot",
)
