"""构建 Windows onedir 与便携 ZIP：python -X utf8 tools/build_portable.py。

需在现有项目环境中安装 PyInstaller 6.x。--check 仅检查依赖和资源。
不包含 user-data、源码工作区或任何本机配置；失败产物保留以供排查。
"""

import argparse
from datetime import datetime
import hashlib
from importlib import metadata, util
import json
from pathlib import Path
import platform
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parents[1]
MODELS = ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx")


def check_requirements():
    if sys.platform != "win32":
        raise RuntimeError("Windows 便携包必须在 Windows 上构建。")
    missing = [name for name in ("PyInstaller", "PySide6", "onnxruntime", "rapidocr") if util.find_spec(name) is None]
    if missing:
        raise RuntimeError(f"缺少构建依赖：{', '.join(missing)}。PyInstaller 不会自动安装；请先在此虚拟环境中安装 PyInstaller 6.x。")
    if metadata.version("pyinstaller").split(".", 1)[0] != "6":
        raise RuntimeError("此 spec 使用 PyInstaller 6.x 的 _internal 目录布局，请使用 6.x 构建。")
    for name in MODELS:
        path = ROOT / "assets" / "models" / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"缺少本地 OCR 模型：{path}")
    if not any((ROOT / "assets" / "icons").glob("*.svg")):
        raise RuntimeError("assets/icons/ 中没有 SVG 图标。")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle(bundle):
    if not (bundle / "PixShot.exe").is_file():
        raise RuntimeError("构建未输出 PixShot.exe。")
    runtime = bundle / "_internal"
    if any(runtime.rglob("icuuc.dll")):
        raise RuntimeError("包内第三方 icuuc.dll 会遮蔽 Qt 所需的 Windows 系统 ICU。")
    resources = list((ROOT / "assets" / "icons").glob("*.svg"))
    resources += [ROOT / "assets" / "models" / name for name in MODELS]
    hashes = {}
    for source in resources:
        relative = source.relative_to(ROOT)
        packaged = runtime / relative
        expected = sha256(source)
        if not packaged.is_file() or sha256(packaged) != expected:
            raise RuntimeError(f"包内资源缺失或不一致：{relative}")
        hashes[str(relative).replace("\\", "/")] = expected
    platform_path = Path("PySide6/plugins/platforms/qwindows.dll")
    platform_source = Path(util.find_spec("PySide6").origin).parent / "plugins/platforms/qwindows.dll"
    packaged_platform = runtime / platform_path
    expected = sha256(platform_source)
    if not packaged_platform.is_file() or sha256(packaged_platform) != expected:
        raise RuntimeError("Qt Windows 平台插件缺失或哈希不一致。")
    hashes[platform_path.as_posix()] = expected
    # 独立进程加载包内原生 DLL，捕获缺失导出符号；不创建 QApplication 或窗口。
    subprocess.run(
        [sys.executable, "-c", "\n".join((
            "import ctypes, os, sys",
            "from pathlib import Path",
            "root = Path(sys.argv[1])",
            "handles = [os.add_dll_directory(str(root / part)) for part in ('', 'PySide6', 'shiboken6')]",
            "loaded = [ctypes.WinDLL(str(root / 'PySide6' / name)) for name in ('Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll')]",
        )), str(runtime)],
        check=True,
    )
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="仅检查构建依赖和本地资源")
    parser.add_argument("--output", type=Path, help="新建输出目录；已存在的目录不会覆盖")
    args = parser.parse_args()
    check_requirements()
    if args.check:
        print("构建依赖、三个 OCR 模型与图标已就绪；尚未执行打包。")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = (args.output or ROOT / "dist" / f"portable-{stamp}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    work = ROOT / "build" / f"portable-{stamp}"
    work.mkdir(parents=True, exist_ok=False)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(output),
         "--workpath", str(work), str(ROOT / "PixShot.spec")],
        cwd=ROOT,
        check=True,
    )
    bundle = output / "PixShot"
    hashes = verify_bundle(bundle)
    manifest = {
        "format": "Windows onedir portable",
        "built_at": datetime.now().astimezone().isoformat(),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "dependencies": {name: metadata.version(name) for name in ("pyinstaller", "PySide6", "onnxruntime", "rapidocr")},
        "resource_sha256": hashes,
        "native_dependency_load_verified": True,
        "runtime_verified": False,
    }
    (bundle / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (bundle / "使用说明.txt").write_text(
        "解压整个 PixShot 文件夹到可写目录，双击 PixShot.exe。\n"
        "应用常驻系统托盘；默认 Ctrl+Alt+S 截图。\n"
        "_internal 目录包含程序依赖、图标与本地 OCR 模型，请勿单独移动 EXE。\n"
        "配置和历史保存于同目录 user-data；移动整个文件夹可一并迁移。\n"
        "本包已检查资源完整性，尚需在目标 Windows 电脑验证截图、OCR 和贴图。\n",
        encoding="utf-8-sig",
    )
    archive = output / "PixShot-portable.zip"
    with ZipFile(archive, "x", compression=ZIP_DEFLATED, compresslevel=6) as zipped:
        for file in sorted(bundle.rglob("*")):
            if file.is_file():
                zipped.write(file, file.relative_to(output))
    with ZipFile(archive) as zipped:
        invalid = zipped.testzip()
        if invalid is not None:
            raise RuntimeError(f"ZIP CRC 校验失败：{invalid}")
    print(f"便携目录：{bundle}")
    print(f"ZIP：{archive}")
    print("资源校验通过；未启动程序或进行真实桌面验收。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
