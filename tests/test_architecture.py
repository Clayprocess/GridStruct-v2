"""AGENTS.md 的业务包边界和样式约束，连同两个程序入口一起检查。"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "capture", "annotate", "pin", "ocr", "table", "export", "settings", "tray", "styles", "ui",
}
HEX_COLOR = re.compile(
    r"(?<![\w/])#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8,9}|[0-9a-f]{12})\b", re.I
)


def _imported_modules(node, submodules):
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
    elif not node.level:
        yield node.module
        # from pkg import child 也会加载未公开的子模块；styles 在 __all__ 中导出了 tokens。
        for alias in node.names:
            child = f"{node.module}.{alias.name}"
            if child in submodules and child != "styles.tokens":
                yield child


def test_architecture():
    files = list(ROOT.glob("*.py"))
    for package in PACKAGES:
        files.extend((ROOT / package).rglob("*.py"))
    paths = sorted(file.relative_to(ROOT) for file in files)
    submodules = {".".join(path.with_suffix("").parts[:2]) for path in paths if len(path.parts) > 1}

    violations = []
    for path in paths:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "setStyleSheet" and path != Path("styles/theme.py"):
                    violations.append(f"{path}:{node.lineno}  请在 styles/theme.py 中应用样式表")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if path != Path("styles/tokens.py") and HEX_COLOR.search(node.value):
                    violations.append(f"{path}:{node.lineno}  请从 styles/tokens.py 引用颜色")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in _imported_modules(node, submodules):
                    package, dot, _ = module.partition(".")
                    if (dot and package in PACKAGES and package != path.parts[0]
                            and module != "ui.motion"):
                        violations.append(f"{path}:{node.lineno}  请通过 {package} 的公开接口访问 {module}")
    assert not violations, "\n" + "\n".join(violations)
