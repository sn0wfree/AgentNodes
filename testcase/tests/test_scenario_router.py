"""Scenario router tests — LLM-driven agent subset orchestration.

Covers docs/scenario-router-design.md:
- three-layer prompt construction (principles / archetypes / catalog)
- LLM path: parse + validate-and-repair pipeline
  (unknown ids dropped, mandatory trio forced, single-asset
  portfolio_construction removed)
- fallback chain: keyword -> default graph; never raises
- detect_max_iter_placeholders short-circuit probe
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.apps.study import scenario_router as sr


def _inp(objective="逐个调整参数找最优组合",
         strategy=None, ws: Path | None = None,
         targets=None) -> sr.RouteInput:
    return sr.RouteInput(
        objective=objective, strategy_name=strategy,
        workspace_path=ws, metric_targets=targets or [],
    )


class _FakeLLM:
    """Duck-typed OpenAICompatClient returning a canned JSON body."""

    def __init__(self, body: str):
        self.body = body
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        out = type("R", (), {})()
        out.content = self.body
        return out


# ── Prompt construction ────────────────────────────────────────

def test_prompt_has_principles_examples_and_catalog():
    p = sr.build_system_prompt()
    # principles layer
    assert "最小流水线" in p and "按需增选" in p and "节俭" in p
    # archetype examples framed as reference-not-menu
    assert "不是菜单" in p
    # full catalog present
    for a in ("researcher", "strategist", "risk_controller",
              "portfolio_construction"):
        assert a in p


# ── LLM path ───────────────────────────────────────────────────

def test_llm_clean_selection_passes_through():
    llm = _FakeLLM('{"selected_agents": ["researcher", "strategist", '
                   '"risk_controller", "anti_overfit_analyst"], '
                   '"reasoning": "调参闭环", "confidence": 0.9}')
    r = sr.route(_inp(), llm_client=llm)
    assert r.source == "llm"
    assert not r.repaired
    assert set(sr.MANDATORY_AGENTS) <= set(r.selected_agents)
    assert "portfolio_construction" not in r.selected_agents


def test_repair_forces_mandatory_and_drops_unknown():
    llm = _FakeLLM('{"selected_agents": ["researcher", "nonexistent_agent"], '
                   '"reasoning": "", "confidence": 0.7}')
    r = sr.route(_inp(), llm_client=llm)
    assert r.repaired
    assert "nonexistent_agent" not in r.selected_agents
    assert set(r.selected_agents) >= set(sr.MANDATORY_AGENTS)
    assert any("forced mandatory" in n for n in r.repair_notes)


def test_single_asset_removes_portfolio_construction(tmp_path: Path):
    # strategy dir exists but carries no multi-asset hints -> single
    strat = tmp_path / "strategies" / "solo"
    strat.mkdir(parents=True)
    (strat / "strategy.py").write_text("PARAMS = {'n': 5}\n")
    llm = _FakeLLM('{"selected_agents": ["researcher", "strategist", '
                   '"risk_controller", "portfolio_construction"], '
                   '"reasoning": "", "confidence": 0.8}')
    r = sr.route(_inp(strategy="solo", ws=tmp_path), llm_client=llm)
    assert r.repaired
    assert "portfolio_construction" not in r.selected_agents
    assert any("single asset" in n for n in r.repair_notes)


def test_multi_asset_keeps_portfolio_construction(tmp_path: Path):
    strat = tmp_path / "strategies" / "multi"
    strat.mkdir(parents=True)
    (strat / "strategy.py").write_text(
        "# risk_parity weighting across assets\nPARAMS={}\n")
    llm = _FakeLLM('{"selected_agents": ["researcher", "strategist", '
                   '"risk_controller", "portfolio_construction"], '
                   '"reasoning": "", "confidence": 0.8}')
    r = sr.route(_inp(strategy="multi", ws=tmp_path), llm_client=llm)
    assert "portfolio_construction" in r.selected_agents


# ── Fallback chain ─────────────────────────────────────────────

def test_keyword_fallback_on_llm_failure():
    class _Broken:
        def chat(self, *a, **k):
            raise RuntimeError("llm down")

    r = sr.route(_inp("对策略做参数优化"), llm_client=_Broken())
    assert r.source == "keyword_fallback"
    assert "anti_overfit_analyst" in r.selected_agents  # tune archetype
    assert set(sr.MANDATORY_AGENTS) <= set(r.selected_agents)


def test_default_graph_when_nothing_matches():
    class _Broken:
        def chat(self, *a, **k):
            raise RuntimeError("down")

    r = sr.route(_inp("随便看看_xyzq"), llm_client=_Broken())
    assert r.source == "default_graph"
    assert len(r.selected_agents) == len(sr.ALL_AGENT_IDS) - 1  # minus PC


def test_route_never_raises_on_garbage_llm_output():
    llm = _FakeLLM("这不是 JSON <<<>>>")
    r = sr.route(_inp("因子挖掘与 IC 验证"), llm_client=llm)
    assert r.source in ("keyword_fallback", "default_graph")


# ── build_graph_for_selection ──────────────────────────────────

def test_built_graph_is_valid_and_subset():
    g = sr.build_graph_for_selection(
        ["researcher", "strategist", "risk_controller"])
    assert g.validate() == []
    assert set(g.node_ids) <= sr.ALL_AGENT_IDS
    assert {"researcher", "strategist", "risk_controller"} <= set(g.node_ids)


def test_no_false_entries_after_pruning():
    """Regression: pruning portfolio_construction used to orphan
    risk_controller (and pruning dq/fa orphaned strategist), making the
    engine run evaluators in parallel with the researcher as START
    entries. Only researcher may be an entry."""
    cases = [
        ["researcher", "strategist", "risk_controller"],
        ["researcher", "strategist", "risk_controller",
         "anti_overfit_analyst"],
        [a for a in sr.ALL_AGENT_IDS if a != "portfolio_construction"],
        ["researcher", "data_quality", "strategist"],
    ]
    for sel in cases:
        g = sr.build_graph_for_selection(sel)
        entries = {n.id for n in g.nodes
                   if not any(e.target == n.id for e in g.edges)}
        # anti_overfit is a graph sink; with it selected but its chain
        # intact it still has upstream, so no extra entries either.
        assert entries <= {"researcher"}, f"false entries for {sel}: {entries}"


# ── max_iter placeholder probe (design §4b) ────────────────────

def test_detect_placeholders_string_payload():
    out = {"researcher": "Reached max_iterations=20 without a final answer.",
           "strategist": {"content": "fine"}}
    assert sr.detect_max_iter_placeholders(out) == ["researcher"]


def test_detect_placeholders_dict_payload_text_fields():
    out = {"risk_controller": {"error":
           "agent loop ended: Reached max_iterations=12"}}
    assert sr.detect_max_iter_placeholders(out) == ["risk_controller"]


def test_detect_placeholders_clean_outputs():
    out = {"researcher": {"action": "optimize_param"},
           "strategist": "结构化产出，正常"}
    assert sr.detect_max_iter_placeholders(out) == []


def test_detect_placeholders_normalizes_output_suffix_keys():
    """phase_engine's aggregated map keys carry the `_output` suffix;
    returned ids must match real agent ids."""
    out = {
        "researcher": "Reached max_iterations=20 without a final answer.",
        "risk_controller_output": {"error":
            "Reached max_iterations=12"},
    }
    assert sr.detect_max_iter_placeholders(out) == [
        "researcher", "risk_controller"]
