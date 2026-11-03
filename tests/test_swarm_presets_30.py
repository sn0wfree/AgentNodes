"""Phase D — Swarm presets 6 → 31 (25 new) 测试.

覆盖:
- list_presets() 加载 31 个 preset
- 每个新 preset 有合法 YAML 结构
- 每个新 preset 的 dag 是合法 DAG (无环)
- 每个新 preset 的 prompt_file 引用都存在
"""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.kernel.swarm.preset_loader import list_presets
from strategy_research.core.workflow.dag import topological_layers, validate_dag

PRESETS_DIR = Path(__file__).resolve().parent.parent / "src" / "strategy_research" / "kernel" / "swarm" / "presets"


# ============================================================
# 25 个新 preset 的清单
# ============================================================

NEW_PRESETS = [
    # Equity
    "equity_research_team",
    "quant_research_committee",
    "value_investing_committee",
    "earnings_research_desk",
    "fundamental_research_team",
    # Quant Strategy
    "ml_quant_lab",
    "pairs_research_lab",
    "statistical_arbitrage_desk",
    # Technical
    "technical_analysis_panel",
    "market_microstructure_team",
    # Portfolio
    "portfolio_review_board",
    "asset_allocation_committee",
    "etf_allocation_desk",
    # Event/Macro
    "event_driven_task_force",
    "macro_strategy_forum",
    "macro_rates_fx_desk",
    # Sentiment/Flow
    "sentiment_intelligence_team",
    "social_alpha_team",
    "flow_analysis_team",
    # Cross-Market
    "global_allocation_committee",
    "global_equities_desk",
    "commodity_research_team",
    # Specialty
    "crypto_trading_desk",
    "derivatives_strategy_desk",
    "convertible_bond_team",  # bonus
]

EXISTING_PRESETS = [
    "quant_research_team", "full_pipeline", "investment_committee",
    "risk_committee", "sector_rotation", "crypto_lab",
]


@pytest.fixture
def all_presets():
    return list_presets(PRESETS_DIR)


# ============================================================
# 总数验证
# ============================================================

class TestPresetCount:
    """总数从 6 → 37 (6 existing + 25 new + 5 goal + 1 autoresearch presets)."""

    def test_total_count_is_37(self, all_presets):
        assert len(all_presets) == 37

    def test_all_25_new_presets_loaded(self, all_presets):
        names = {p.name for p in all_presets}
        for name in NEW_PRESETS:
            assert name in names, f"{name} 未加载"

    def test_all_6_existing_presets_loaded(self, all_presets):
        names = {p.name for p in all_presets}
        for name in EXISTING_PRESETS:
            assert name in names, f"{name} 缺失"


# ============================================================
# 每个新 preset 都有合法结构
# ============================================================

class TestNewPresetStructure:
    """每个新 preset: agents/dag 都存在 + prompt 文件存在."""

    @pytest.mark.parametrize("preset_name", NEW_PRESETS)
    def test_preset_has_at_least_3_agents(self, all_presets, preset_name):
        preset = next(p for p in all_presets if p.name == preset_name)
        assert len(preset.agents) >= 3, f"{preset_name} agents < 3"

    @pytest.mark.parametrize("preset_name", NEW_PRESETS)
    def test_preset_dag_no_cycle(self, all_presets, preset_name):
        """DAG 无环 (否则 topological_layers 会抛)."""
        preset = next(p for p in all_presets if p.name == preset_name)
        try:
            validate_dag(preset.dag)
        except Exception as exc:
            pytest.fail(f"{preset_name} DAG 有环: {exc}")

    @pytest.mark.parametrize("preset_name", NEW_PRESETS)
    def test_preset_topological_layers(self, all_presets, preset_name):
        """topological_layers 不报错 (用 SwarmRuntime 语义)."""
        preset = next(p for p in all_presets if p.name == preset_name)
        layers = topological_layers(preset.dag)
        all_in_layers = set()
        for layer in layers:
            all_in_layers.update(layer)
        agent_ids = {a.agent_name for a in preset.agents}
        assert all_in_layers == agent_ids, (
            f"{preset_name} layers {all_in_layers} != agents {agent_ids}"
        )

    @pytest.mark.parametrize("preset_name", NEW_PRESETS)
    def test_preset_agent_tools_nonempty(self, all_presets, preset_name):
        """每个 agent 至少有一个工具."""
        preset = next(p for p in all_presets if p.name == preset_name)
        for agent in preset.agents:
            tools = agent.context.get("tools", [])
            assert len(tools) >= 1, f"{preset_name}/{agent.agent_name} 无工具"


# ============================================================
# Prompt 文件存在
# ============================================================

class TestPromptFilesExist:
    """所有 prompt_file 引用都解析到真实文件."""

    @pytest.mark.parametrize("preset_name", NEW_PRESETS)
    def test_prompt_files_exist(self, all_presets, preset_name):
        from strategy_research import _TEMPLATES_DIR
        prompt_dir = _TEMPLATES_DIR / ".prompts"
        preset = next(p for p in all_presets if p.name == preset_name)
        for agent in preset.agents:
            prompt_file = agent.prompt
            assert prompt_file.endswith(".md"), f"{agent.agent_name} prompt 不是 .md"
            relative = prompt_file.replace(".prompts/", "")
            full_path = prompt_dir / relative
            assert full_path.exists(), f"{prompt_file} 不存在 ({full_path})"


# ============================================================
# DAG 拓扑性质 (用 SwarmRuntime 语义: in_degree[node] = len(deps[node]))
# ============================================================

class TestDagTopology:
    """DAG 拓扑性质 (parallel/sequential 分类)."""

    @pytest.mark.parametrize("preset_name", [
        "value_investing_committee",
        "macro_strategy_forum",
        "sentiment_intelligence_team",
    ])
    def test_parallel_preset_has_first_layer_with_multiple(self, all_presets, preset_name):
        """parallel 模式: 第一层应 ≥3 个 agent (无依赖节点)."""
        preset = next(p for p in all_presets if p.name == preset_name)
        layers = topological_layers(preset.dag)
        assert len(layers[0]) >= 3, (
            f"{preset_name} 第一层只 {len(layers[0])} 个, 应 ≥3 (parallel)"
        )

    @pytest.mark.parametrize("preset_name", [
        "event_driven_task_force",
        "market_microstructure_team",
        "derivatives_strategy_desk",
    ])
    def test_sequential_preset_each_layer_singleton(self, all_presets, preset_name):
        """sequential 模式: 每一层都是 1 个 agent."""
        preset = next(p for p in all_presets if p.name == preset_name)
        layers = topological_layers(preset.dag)
        for i, layer in enumerate(layers):
            assert len(layer) == 1, (
                f"{preset_name} 第 {i} 层有 {len(layer)} 个 agent, sequential 应为 1"
            )

    @pytest.mark.parametrize("preset_name", [
        "event_driven_task_force",
        "market_microstructure_team",
        "derivatives_strategy_desk",
    ])
    def test_sequential_preset_layer_count_matches_agent_count(self, all_presets, preset_name):
        """sequential 模式: 层数 == agent 数."""
        preset = next(p for p in all_presets if p.name == preset_name)
        layers = topological_layers(preset.dag)
        assert len(layers) == len(preset.agents)


# ============================================================
# 工具白名单 vs builtin_tools
# ============================================================

class TestToolWhitelistsValid:
    """preset 中引用的工具名都存在于 build_default_registry()."""

    @pytest.mark.parametrize("preset_name", NEW_PRESETS)
    def test_tools_exist_in_registry(self, all_presets, preset_name):
        from strategy_research.core.agent.builtin_tools import build_default_registry
        registry = build_default_registry()
        registered_tools = set(registry._tools.keys())

        preset = next(p for p in all_presets if p.name == preset_name)
        for agent in preset.agents:
            tools = agent.context.get("tools", [])
            for tool in tools:
                assert tool in registered_tools, (
                    f"{preset_name}/{agent.agent_name}: 工具 {tool!r} 未注册"
                )
