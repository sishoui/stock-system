"""跨层依赖方向检查（docs/spec.md §2.2）。

依赖单向：data → analysis → strategy → backtest/api。低层 import 高层即违规。
common 为最底层，可被所有层引用；反向依赖 common 之外的任何 app 层均拦截。
"""
import ast
from pathlib import Path

# 依赖顺序（左可被右引用，右不可 import 左）
LAYER_ORDER: tuple[str, ...] = ("data", "analysis", "strategy", "backtest", "api")


def _layer_of(module: str) -> str | None:
    """app.<layer>... → layer；非 app 顶层模块（如 app.common）返回 None。"""
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "app" and parts[1] in LAYER_ORDER:
        return parts[1]
    return None


def _iter_python_files(app_dir: Path):
    for p in sorted(app_dir.rglob("*.py")):
        yield p


def find_illegal_imports(app_dir: Path) -> list[tuple[str, str]]:
    """返回 [(文件路径, 被违规 import 的模块)]，空列表即合规。"""
    illegal: list[tuple[str, str]] = []
    for file in _iter_python_files(Path(app_dir)):
        src_layer = _layer_of(_file_module(file, app_dir))
        if src_layer is None:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module] if node.module else []
            else:
                continue
            for mod in mods:
                dst_layer = _layer_of(mod)
                if dst_layer is not None and LAYER_ORDER.index(dst_layer) > LAYER_ORDER.index(src_layer):
                    illegal.append((str(file), mod))
    return illegal


def _file_module(file: Path, app_dir: Path) -> str:
    rel = Path(file).resolve().relative_to(Path(app_dir).resolve()).with_suffix("")
    parts = ["app", *rel.parts]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)
