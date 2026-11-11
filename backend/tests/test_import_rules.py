"""跨层反向依赖拦截：data → analysis → strategy → backtest/api。

扫描 backend/app 全部 import，若低层模块 import 高层模块则测试失败。
"""
from pathlib import Path

from app.common.import_rules import LAYER_ORDER, find_illegal_imports

APP_DIR = Path(__file__).resolve().parents[1] / "app"


class TestLayerDependencyRules:
    def test_layer_order_matches_spec(self):
        # §2.2: data → analysis → strategy → backtest/api
        assert LAYER_ORDER == ("data", "analysis", "strategy", "backtest", "api")

    def test_no_illegal_imports_in_app(self):
        illegal = find_illegal_imports(APP_DIR)
        assert illegal == [], (
            "反向依赖（低层 import 高层）违规:\n"
            + "\n".join(f"{src}: {mod}" for src, mod in illegal)
        )

    def test_detector_catches_synthetic_violation(self, tmp_path):
        pkg = tmp_path / "app" / "data"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "bad.py").write_text(
            "from app.analysis import something\n", encoding="utf-8"
        )
        illegal = find_illegal_imports(tmp_path / "app")
        assert any(src.endswith("bad.py") and mod == "app.analysis" for src, mod in illegal)
