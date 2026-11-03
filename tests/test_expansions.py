"""Tests for new swarm presets, agent tools, and strategy export."""

from __future__ import annotations

from pathlib import Path

from strategy_research.apps.export.exporter import _parse_strategy, export_strategy
from strategy_research.kernel.swarm.preset_loader import list_presets, load_preset

# ── New Swarm Presets Tests ─────────────────────────────────


class TestNewSwarmPresets:
    def test_investment_committee(self, tmp_path):
        preset_file = Path(__file__).parent.parent / "src" / "strategy_research" / "kernel" / "swarm" / "presets" / "investment_committee.yaml"
        preset = load_preset(preset_file)
        assert preset is not None
        assert preset.name == "investment_committee"
        assert len(preset.agents) == 5
        assert "risk_controller" in preset.dag

    def test_crypto_lab(self, tmp_path):
        preset_file = Path(__file__).parent.parent / "src" / "strategy_research" / "kernel" / "swarm" / "presets" / "crypto_lab.yaml"
        preset = load_preset(preset_file)
        assert preset is not None
        assert preset.name == "crypto_lab"
        assert len(preset.agents) == 5

    def test_sector_rotation(self, tmp_path):
        preset_file = Path(__file__).parent.parent / "src" / "strategy_research" / "kernel" / "swarm" / "presets" / "sector_rotation.yaml"
        preset = load_preset(preset_file)
        assert preset is not None
        assert preset.name == "sector_rotation"
        assert len(preset.agents) == 6

    def test_all_presets_listed(self):
        presets_dir = Path(__file__).parent.parent / "src" / "strategy_research" / "kernel" / "swarm" / "presets"
        presets = list_presets(presets_dir)
        names = {p.name for p in presets}
        assert "quant_research_team" in names
        assert "risk_committee" in names
        assert "full_pipeline" in names
        assert "investment_committee" in names
        assert "crypto_lab" in names
        assert "sector_rotation" in names
        assert len(presets) >= 6


# ── Agent Tools Tests ───────────────────────────────────────


class TestNewAgentTools:
    def test_factor_analysis_tool_registered(self):
        from strategy_research.core.agent.builtin_tools import build_default_registry
        registry = build_default_registry()
        assert registry.get("factor_analysis") is not None

    def test_pattern_recognition_tool_registered(self):
        from strategy_research.core.agent.builtin_tools import build_default_registry
        registry = build_default_registry()
        assert registry.get("pattern_recognition") is not None

    def test_options_pricing_tool_registered(self):
        from strategy_research.core.agent.builtin_tools import build_default_registry
        registry = build_default_registry()
        assert registry.get("options_pricing") is not None

    def test_total_tools_count(self):
        from strategy_research.core.agent.builtin_tools import build_default_registry
        registry = build_default_registry()
        # 6 original + 3 new = 9
        assert len(registry._tools) >= 9

    def test_options_pricing_call(self):
        from strategy_research.core.agent.builtin_tools import OptionsPricingTool
        from strategy_research.core.agent.tools import ToolContext
        tool = OptionsPricingTool()
        result = tool.execute(
            ctx=ToolContext(),
            spot=100, strike=100, rate=0.05, volatility=0.2,
            time_to_expiry=0.5, option_type="call",
        )
        assert '"status": "ok"' in result
        assert '"price"' in result
        assert '"delta"' in result

    def test_options_pricing_put(self):
        from strategy_research.core.agent.builtin_tools import OptionsPricingTool
        from strategy_research.core.agent.tools import ToolContext
        tool = OptionsPricingTool()
        result = tool.execute(
            ctx=ToolContext(),
            spot=100, strike=110, rate=0.05, volatility=0.3,
            time_to_expiry=1.0, option_type="put",
        )
        assert '"status": "ok"' in result

    def test_options_pricing_invalid_type(self):
        from strategy_research.core.agent.builtin_tools import OptionsPricingTool
        from strategy_research.core.agent.tools import ToolContext
        tool = OptionsPricingTool()
        result = tool.execute(
            ctx=ToolContext(),
            spot=100, strike=100, rate=0.05, volatility=0.2,
            time_to_expiry=0.5, option_type="straddle",
        )
        assert '"error"' in result


# ── Strategy Export Tests ───────────────────────────────────


class TestStrategyExport:
    def test_parse_strategy(self):
        code = '''
PARAMS = {
    "fast_period": 10,
    "slow_period": 30,
    "threshold": 0.02,
}
FACTOR_EXPRS = [
    "ts_mean(close, fast_period) / ts_mean(close, slow_period) - 1",
]
FACTOR_WEIGHT_METHOD = "equal"
'''
        info = _parse_strategy(code)
        assert info["params"]["fast_period"] == 10
        assert info["params"]["slow_period"] == 30
        assert len(info["factor_exprs"]) == 1

    def test_export_pine(self, tmp_path):
        strategy_file = tmp_path / "strategy.py"
        strategy_file.write_text('''
PARAMS = {"fast": 10, "slow": 30}
FACTOR_EXPRS = ["ts_mean(close, 20) / ts_mean(close, 60) - 1"]
''')
        output_dir = tmp_path / "exports"
        results = export_strategy(strategy_file, output_dir, ["pine"])

        assert results["pine"]["status"] == "ok"
        assert (output_dir / "strategy.pine").exists()
        content = (output_dir / "strategy.pine").read_text()
        assert "@version=5" in content
        assert "ta.sma" in content

    def test_export_tdx(self, tmp_path):
        strategy_file = tmp_path / "strategy.py"
        strategy_file.write_text('''
PARAMS = {"fast": 10, "slow": 30}
FACTOR_EXPRS = ["ts_mean(close, 20) / ts_mean(close, 60) - 1"]
''')
        output_dir = tmp_path / "exports"
        results = export_strategy(strategy_file, output_dir, ["tdx"])

        assert results["tdx"]["status"] == "ok"
        assert (output_dir / "strategy.tdx").exists()
        content = (output_dir / "strategy.tdx").read_text()
        assert "通达信公式" in content
        assert "MA(" in content

    def test_export_vnpy(self, tmp_path):
        strategy_file = tmp_path / "strategy.py"
        strategy_file.write_text('''
PARAMS = {"fast": 10, "slow": 30}
FACTOR_EXPRS = ["ts_mean(close, 20) / ts_mean(close, 60) - 1"]
''')
        output_dir = tmp_path / "exports"
        results = export_strategy(strategy_file, output_dir, ["vnpy"])

        assert results["vnpy"]["status"] == "ok"
        assert (output_dir / "strategy.py").exists()
        content = (output_dir / "strategy.py").read_text()
        assert "CtaTemplate" in content
        assert "on_bar" in content

    def test_export_all_formats(self, tmp_path):
        strategy_file = tmp_path / "strategy.py"
        strategy_file.write_text('''
PARAMS = {"period": 20}
FACTOR_EXPRS = ["ts_mean(close, 20)"]
''')
        output_dir = tmp_path / "exports"
        results = export_strategy(strategy_file, output_dir)

        assert len(results) == 3
        assert all(r["status"] == "ok" for r in results.values())

    def test_export_nonexistent_strategy(self, tmp_path):
        results = export_strategy(tmp_path / "nonexistent.py", tmp_path / "out")
        # Should not crash, but may have errors
        assert isinstance(results, dict)
