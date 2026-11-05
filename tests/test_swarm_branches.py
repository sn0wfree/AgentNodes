"""SwarmRuntime 分支机制单元测试。

覆盖 docs/archive/2026-10/coverage-90-plan-20261030.md §3.2：`swarm/runtime.py` 原 64.8%，
其中 `_build_layer_results` / `_apply_branches` / `_apply_branch_action`
（约 100 行）零覆盖。这些是**条件分支控制流**——DAG 里「风控不过就跳过
组合构建」这类逻辑的实现，业务价值高且完全可纯测。
"""
from __future__ import annotations

import json

import pytest

from agentnodes.kernel.swarm.runtime import (
    AgentResult,
    AgentStatus,
    SwarmRuntime,
)


@pytest.fixture()
def runtime() -> SwarmRuntime:
    return SwarmRuntime()


def _ok(agent_id: str, output) -> AgentResult:
    return AgentResult(
        agent_id=agent_id, status=AgentStatus.SUCCESS,
        output=output if isinstance(output, str) else json.dumps(output),
    )


# ── _build_layer_results ────────────────────────────────────


def test_layer_results_parses_json_output(runtime):
    results = {"a": _ok("a", {"max_drawdown": -0.3, "sharpe": 1.2})}
    lr = runtime._build_layer_results(results)
    assert lr["a"]["output"]["max_drawdown"] == -0.3
    assert lr["a"]["output"]["sharpe"] == 1.2


def test_layer_results_merges_inner_answer_json(runtime):
    """answer 字段本身是 JSON 时，其键要上提（output.<field> 才能解析）。"""
    payload = {"answer": json.dumps({"calmar": 0.8}), "status": "ok"}
    lr = runtime._build_layer_results({"a": _ok("a", payload)})
    assert lr["a"]["output"]["calmar"] == 0.8
    assert lr["a"]["output"]["status"] == "ok"


def test_layer_results_non_dict_json_falls_back(runtime):
    """output 是 JSON 数组（非 dict）→ 包成 {"answer": 原文}。"""
    lr = runtime._build_layer_results({"a": _ok("a", "[1, 2, 3]")})
    assert lr["a"]["output"] == {"answer": "[1, 2, 3]"}


def test_layer_results_unparseable_output(runtime):
    lr = runtime._build_layer_results({"a": _ok("a", "not json")})
    assert lr["a"]["output"] == {"output": "not json"}


def test_layer_results_skips_failed_agents(runtime):
    failed = AgentResult(agent_id="b", status=AgentStatus.ERROR, output="x")
    assert runtime._build_layer_results({"b": failed}) == {}


def test_layer_results_skips_empty_output(runtime):
    empty = AgentResult(agent_id="c", status=AgentStatus.SUCCESS, output="")
    assert runtime._build_layer_results({"c": empty}) == {}


def test_layer_results_answer_not_dict_is_ignored(runtime):
    lr = runtime._build_layer_results({"a": _ok("a", {"answer": "[1,2]"})})
    assert lr["a"]["output"] == {"answer": "[1,2]"}


# ── _apply_branch_action ────────────────────────────────────


def test_branch_skip_removes_target_from_remaining_layers():
    remaining = [["portfolio"], ["report", "portfolio"]]
    skipped: list[str] = []
    retried: list[str] = []
    SwarmRuntime._apply_branch_action(
        "skip", "portfolio", remaining, {}, skipped, retried,
    )
    assert skipped == ["portfolio", "portfolio"]
    assert remaining == [[], ["report"]]
    assert retried == []


def test_branch_skip_noop_when_target_absent():
    remaining = [["report"]]
    skipped: list[str] = []
    SwarmRuntime._apply_branch_action(
        "skip", "portfolio", remaining, {}, skipped, []
    )
    assert skipped == []
    assert remaining == [["report"]]


def test_branch_retry_appends_to_next_layer():
    remaining = [["portfolio"], ["report"]]
    retried: list[str] = []
    SwarmRuntime._apply_branch_action(
        "retry", "risk_controller", remaining, {"risk_controller": {}},
        [], retried,
    )
    assert retried == ["risk_controller"]
    assert remaining[0] == ["portfolio", "risk_controller"]


def test_branch_retry_not_applied_when_already_scheduled():
    remaining = [["risk_controller"], ["report"]]
    retried: list[str] = []
    SwarmRuntime._apply_branch_action(
        "retry", "risk_controller", remaining, {"risk_controller": {}}, [], retried,
    )
    assert retried == []
    assert remaining[0] == ["risk_controller"]


def test_branch_retry_requires_layer_result():
    """没有该 agent 的结果 → 无法重试（不应凭空排入）。"""
    remaining = [["portfolio"]]
    retried: list[str] = []
    SwarmRuntime._apply_branch_action(
        "retry", "missing", remaining, {}, [], retried,
    )
    assert retried == []
    assert remaining[0] == ["portfolio"]


def test_branch_unknown_action_is_noop():
    remaining = [["a"]]
    skipped: list[str] = []
    retried: list[str] = []
    SwarmRuntime._apply_branch_action(
        "redirect", "a", remaining, {"a": {}}, skipped, retried,
    )
    assert skipped == [] and retried == []
    assert remaining == [["a"]]


# ── _apply_branches ─────────────────────────────────────────


def test_apply_branches_skips_on_true_condition():
    branches = [{
        "condition": "risk_controller.output.max_drawdown < -0.2",
        "action": "skip",
        "target": "portfolio",
    }]
    layer_results = {"risk_controller": {"output": {"max_drawdown": -0.35}}}
    layers = [["risk_controller"], ["portfolio"], ["report"]]
    skipped, retried = SwarmRuntime()._apply_branches(
        branches, layer_results, layers, 0,
    )
    assert skipped == ["portfolio"]
    assert layers[1] == []


def test_apply_branches_does_nothing_when_condition_false():
    branches = [{
        "condition": "risk_controller.output.max_drawdown < -0.2",
        "action": "skip",
        "target": "portfolio",
    }]
    layer_results = {"risk_controller": {"output": {"max_drawdown": -0.05}}}
    layers = [["risk_controller"], ["portfolio"]]
    rt = SwarmRuntime()
    skipped, retried = rt._apply_branches(branches, layer_results, layers, 0)
    assert (skipped, retried) == ([], [])
    assert layers[1] == ["portfolio"]


def test_apply_branches_retry_action():
    branches = [{
        "condition": "evaluator.output.needs_retry == 1",
        "action": "retry",
        "target": "strategist",
    }]
    layer_results = {
        "evaluator": {"output": {"needs_retry": 1}},
        "strategist": {"output": {}},
    }
    layers = [["evaluator"], ["portfolio"]]
    rt = SwarmRuntime()
    skipped, retried = rt._apply_branches(branches, layer_results, layers, 0)
    assert retried == ["strategist"]
    assert "strategist" in layers[1]


def test_apply_branches_no_remaining_layers():
    branches = [{"condition": "a.output.x == 1", "action": "skip", "target": "b"}]
    layers = [["a"]]
    rt = SwarmRuntime()
    assert rt._apply_branches(branches, {"a": {"output": {"x": 1}}}, layers, 0) == ([], [])


def test_apply_branches_empty_branches():
    rt = SwarmRuntime()
    layers = [["a"], ["b"]]
    assert rt._apply_branches([], {}, layers, 0) == ([], [])


def test_apply_branches_skips_malformed_config():
    """缺 condition 或 target 的分支配置被跳过（不抛）。"""
    rt = SwarmRuntime()
    layers = [["a"], ["b"]]
    assert rt._apply_branches(
        [{"action": "skip"}, {"condition": "a.output.x==1"}], {}, layers, 0,
    ) == ([], [])


def test_apply_branches_bad_expression_is_swallowed():
    """条件表达式非法 → 记日志跳过，不影响其他分支。"""
    rt = SwarmRuntime()
    layers = [["a"], ["b"]]
    branches = [{"condition": "this is not valid $$", "action": "skip", "target": "b"}]
    skipped, retried = rt._apply_branches(
        branches, {"a": {"output": {"x": 1}}}, layers, 0,
    )
    assert (skipped, retried) == ([], [])


# ── _evaluate_branches_after_layer ──────────────────────────


def test_evaluate_branches_after_layer_applies(caplog):
    rt = SwarmRuntime()
    agent_results = {
        "risk_controller": _ok("risk_controller", {"max_drawdown": -0.5}),
    }
    layers = [["risk_controller"], ["portfolio"]]
    branches = [{
        "condition": "risk_controller.output.max_drawdown < -0.2",
        "action": "skip",
        "target": "portfolio",
    }]
    rt._evaluate_branches_after_layer(branches, agent_results, layers, 0)
    assert layers[1] == []


def test_evaluate_branches_no_hit_is_quiet():
    rt = SwarmRuntime()
    layers = [["a"], ["b"]]
    rt._evaluate_branches_after_layer([], {}, layers, 0)
    assert layers == [["a"], ["b"]]
