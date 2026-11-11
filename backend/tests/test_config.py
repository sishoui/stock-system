"""配置收口测试：YAML 加载 + Pydantic v2 校验。

期望值独立来源：docs/spec.md §7.5 / §4.4 / §12。
"""
from pathlib import Path

import pytest
import yaml

from app.common.config import (
    AIConfig,
    DataConfig,
    RiskConfig,
    load_ai_config,
    load_data_config,
    load_risk_config,
)

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"


class TestRiskConfigDefaults:
    """§7.5 全部参数及默认值，默认对象（无 YAML 覆盖）即规格默认。"""

    def test_default_values_match_spec_7_5(self):
        rc = RiskConfig()
        assert rc.atr_multiplier == 2.0
        assert rc.atr_period == 14
        assert rc.trail_atr_mult == 3.0
        assert rc.trail_pct == 0.08
        assert rc.max_positions == 10
        assert rc.position_scale_strong == 1.0
        assert rc.position_scale_mid == 0.6
        assert rc.max_single_weight == 0.15
        assert rc.max_industry_weight == 0.30
        assert rc.dd_circuit_break == 0.10
        assert rc.dd_recover == 0.05


class TestRiskConfigValidation:
    def test_load_from_yaml_overrides_default(self, tmp_path):
        f = tmp_path / "risk.yaml"
        f.write_text(yaml.dump({"atr_multiplier": 2.5}), encoding="utf-8")
        rc = load_risk_config(f)
        assert rc.atr_multiplier == 2.5
        assert rc.atr_period == 14  # 未覆盖项仍为默认

    def test_rejects_value_out_of_search_range(self, tmp_path):
        # spec: atr_multiplier 区间 1.5–3.0
        f = tmp_path / "risk.yaml"
        f.write_text(yaml.dump({"atr_multiplier": 5.0}), encoding="utf-8")
        with pytest.raises(Exception):
            load_risk_config(f)

    def test_rejects_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_risk_config(Path("no/such/file.yaml"))

    def test_rejects_wrong_type(self, tmp_path):
        f = tmp_path / "risk.yaml"
        f.write_text(yaml.dump({"max_positions": "ten"}), encoding="utf-8")
        with pytest.raises(Exception):
            load_risk_config(f)


class TestAIConfigDefaults:
    """§4.4：日 ¥10、月 ¥200、退避 5/30/120s、复核置信度 0.7、连续 3 窗口告警。"""

    def test_default_values_match_spec_4_4(self):
        c = AIConfig()
        assert c.daily_cost_limit == 10.0
        assert c.monthly_budget == 200.0
        assert c.max_concurrency >= 1
        assert c.backoff_seconds == [5, 30, 120]
        assert c.review_confidence_threshold == 0.7
        assert c.consecutive_failure_alert == 3
        assert c.review_suspect is True
        assert c.force_review_on_buy is True

    def test_load_from_yaml(self, tmp_path):
        f = tmp_path / "ai.yaml"
        f.write_text(yaml.dump({"daily_cost_limit": 20.0}), encoding="utf-8")
        c = load_ai_config(f)
        assert c.daily_cost_limit == 20.0
        assert c.monthly_budget == 200.0

    def test_rejects_negative_cost_limit(self, tmp_path):
        f = tmp_path / "ai.yaml"
        f.write_text(yaml.dump({"daily_cost_limit": -1}), encoding="utf-8")
        with pytest.raises(Exception):
            load_ai_config(f)


class TestDataConfigDefaults:
    """§3.3/§3.4/§12：数据源档位、限速、对账抽样（随机 50 只 + 全部自选）。"""

    def test_default_values_match_spec(self):
        c = DataConfig()
        assert c.source_tier == "free"
        assert c.reconcile_sample_size == 50
        assert c.reconcile_price_tolerance >= 0
        assert c.rate_limit_qps >= 1

    def test_rejects_unknown_source_tier(self, tmp_path):
        f = tmp_path / "data.yaml"
        f.write_text(yaml.dump({"source_tier": "gold"}), encoding="utf-8")
        with pytest.raises(Exception):
            load_data_config(f)


class TestBundledConfigFiles:
    """仓库自带 configs/*.yaml 可加载，且与默认一致。"""

    @pytest.mark.parametrize(
        "filename,loader",
        [
            ("risk.yaml", load_risk_config),
            ("ai.yaml", load_ai_config),
            ("data.yaml", load_data_config),
        ],
    )
    def test_bundled_yaml_loads(self, filename, loader):
        cfg = loader(CONFIGS_DIR / filename)
        assert cfg == type(cfg)()
