"""枚举单一事实源守卫（docs/agents/domain.md 约定）。

机械拦截两类回归：
1. 代码里重新出现枚举字面量（绕过 common/enums.py）；
2. enums.py 定义的取值与各消费方的 Literal/取值集合漂移。
"""
import re
from pathlib import Path

from app.common import enums

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# 允许字面量出现的白名单：enums.py 本身、注释/docstring 由 AST 检查天然排除
ALLOWED_FILES = {"common/enums.py"}


def _py_files():
    return [p for p in sorted(APP_DIR.rglob("*.py"))]


class TestEnumSingleSource:
    def test_signal_levels_consistent_with_api_schema(self):
        # api/schemas.py 的 Literal 与 enums 对齐
        schema = (APP_DIR / "api" / "schemas.py").read_text(encoding="utf-8")
        for lit in re.findall(r'Literal\["([^"]+)"(?:, "([^"]+)")?(?:, "([^"]+)")?\]', schema):
            vals = [v for v in lit if v]
            if "strong" in vals or "medium" in vals:
                assert set(vals) == set(enums.SIGNAL_LEVELS), (
                    f"api/schemas.py 信号级别 Literal {vals} 与 enums.SIGNAL_LEVELS 漂移"
                )

    def test_no_signal_level_string_literals_outside_enums(self):
        # 字符串字面量 "strong"/"medium"/"weak" 只允许出现在 enums.py
        pattern = re.compile(r'["\'](strong|medium|weak)["\']')
        offenders = []
        for p in _py_files():
            rel = p.relative_to(APP_DIR).as_posix()
            if rel in ALLOWED_FILES or rel.startswith("api/schemas") or rel == "analysis/ai/schemas.py":
                continue
            tree_src = p.read_text(encoding="utf-8")
            for i, line in enumerate(tree_src.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if pattern.search(line) and "Literal" not in line:
                    offenders.append(f"{rel}:{i}: {stripped}")
        assert offenders == [], "信号级别字面量绕过 enums.py:\n" + "\n".join(offenders)

    def test_no_check_type_literals_outside_enums(self):
        pattern = re.compile(r'["\'](completeness|suspension|cross|abnormal)["\']')
        offenders = []
        for p in _py_files():
            rel = p.relative_to(APP_DIR).as_posix()
            if rel in ALLOWED_FILES:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if pattern.search(line):
                    offenders.append(f"{rel}:{i}: {stripped}")
        assert offenders == [], "校验类型字面量绕过 enums.py:\n" + "\n".join(offenders)

    def test_no_ai_status_literals_outside_enums(self):
        pattern = re.compile(r'["\']pending["\']')
        offenders = []
        for p in _py_files():
            rel = p.relative_to(APP_DIR).as_posix()
            if rel in ALLOWED_FILES:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if pattern.search(line):
                    offenders.append(f"{rel}:{i}: {stripped}")
        assert offenders == [], "AI 状态字面量绕过 enums.py:\n" + "\n".join(offenders)

    def test_normalize_signal_level(self):
        assert enums.normalize_signal_level("mid") == "medium"
        assert enums.normalize_signal_level("strong") == "strong"
