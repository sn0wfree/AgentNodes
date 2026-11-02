"""research preset（B1-b）测试 —— 执行等价 / abort 语义 / 拓扑 / 灰度门。

方法论（replan v2 §5）：同一 BranchContext 分别喂旧 `_run_phases_branch`
与图形态 `run_phases_via_graph`，断言 RoundOutcome 逐字段相等、事件序列
相等、phase 函数收到的参数相等。三个 phase 函数两条路径都在函数体内
延迟 import autoresearch——monkeypatch 一处即同时命中两边。
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from strategy_research.core.study.graph_preset import (
    build_research_engine,
    run_phases_via_graph,
)
from strategy_research.core.study.phase_engine import (
    BranchContext,
    _resolve_branch_fn,
    _run_phases_branch,
)

RESEARCHER_RESULT = {
    "researcher_output": {
        "hypothesis": "h1",
        "predicted_affected": ["calmar"],
        "predictions": {"calmar": 0.6},
    }
}
EXEC_RESULT = {
    "metrics": {"calmar": 0.7, "sharpe": 1.1},
    "strategist_output": {"action": "discover_local", "changes": ["x"]},
    "backtest_result": {"equity": [1.0, 2.0]},
    "backtest_error": None,
    "data_quality_output": {"ok": True},
    "factor_analyst_output": {"n": 3},
    "portfolio_construction_output": {"w": [0.5, 0.5]},
}
EVAL_RESULT = {
    "verdict": "keep",
    "risk_controller_output": {"pass": True},
    "attribution_analyst_output": {"attr": "momentum"},
    "anti_overfit_analyst_output": {"aoa": "pass"},
    "backtest_diagnostics_output": {"diag": "ok"},
}
CLAIMS = {"calmar": 0.6}


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind: str, data: dict) -> None:
        self.events.append((kind, data))

    def started(self) -> list[str]:
        return [d["node"] for k, d in self.events if k == "node.started"]


class FakeRunner:
    def __init__(self, novel: bool = True) -> None:
        self._loop_strategy = None
        self._idle_rounds = 0
        self.novel = novel
        self.domain_events: list[tuple[str, str, dict]] = []
        self.archived: list[tuple] = []

    def _get_study(self):
        return SimpleNamespace(
            engine="phases", behavior=None, session_id="sess",
            study_id="sid", metric_targets=[{"name": "calmar"}],
            objective="obj", lazy_detection_interval=0, keep_recent=0,
        )

    def _emit(self, session: str, kind: str, data: dict) -> None:
        self.domain_events.append((session, kind, data))

    def _make_agent_on_event(self, session_id: str):
        # phase_engine 的新契约（agent 事件接线）；桩里不产事件。
        return None

    def _check_novelty(self, hypothesis: str, predicted_affected: list):
        return (self.novel, "" if self.novel else "duplicate hypothesis")

    def _archive_rejected(self, round_num, hypothesis, reason, detail) -> None:
        self.archived.append((round_num, hypothesis, reason, detail))


def make_ctx(tmp_path, runner: FakeRunner) -> BranchContext:
    return BranchContext(
        runner=runner, study=runner._get_study(), round_num=3,
        previous_summary=None, directive_text=None,
        path=tmp_path, strategy="strat", current_state={"k": 1},
        run_dir=tmp_path / "run", runs_dir=tmp_path / "runs",
        run_name="run_x", results_tsv=tmp_path / "r.tsv",
        graph=None, session="sess", sid="sid",
        metric_targets=[{"name": "calmar"}], guidance=None,
    )


def _install_phase_fakes(monkeypatch, calls: list) -> None:
    import strategy_research.core.autoresearch as ar

    def fake_researcher(path, strategy, current_state, run_dir, **kwargs):
        calls.append(("researcher", str(path), strategy, dict(current_state), kwargs))
        return dict(RESEARCHER_RESULT)

    def fake_execution(path, strategy, current_state, researcher_output, run_dir, **kwargs):
        calls.append(("execution", str(path), strategy, researcher_output, kwargs))
        return dict(EXEC_RESULT)

    def fake_evaluation(path, strategy, backtest_result, metrics, run_dir, **kwargs):
        calls.append(("evaluation", backtest_result, dict(metrics), kwargs))
        return dict(EVAL_RESULT)

    def fake_claims(runner, round_num, researcher_output, predicted_affected, metric_targets):
        calls.append(("claims", round_num, researcher_output, list(predicted_affected)))
        return dict(CLAIMS)

    monkeypatch.setattr(ar, "run_researcher_phase", fake_researcher)
    monkeypatch.setattr(ar, "run_execution_phase", fake_execution)
    monkeypatch.setattr(ar, "run_evaluation_phase", fake_evaluation)
    # 两条路径的 lookup 点都换成同一个 fake（patch where used）
    import strategy_research.core.study.graph_preset as gp
    import strategy_research.core.study.phase_engine as pe

    monkeypatch.setattr(pe, "_capture_round_claims", fake_claims)
    monkeypatch.setattr(gp, "_capture_round_claims", fake_claims)


# ── 执行等价 ─────────────────────────────────────────────


def test_round_outcome_identical_to_legacy(tmp_path, monkeypatch):
    calls: list = []
    _install_phase_fakes(monkeypatch, calls)

    legacy = _run_phases_branch(make_ctx(tmp_path, FakeRunner()))
    graph = run_phases_via_graph(make_ctx(tmp_path, FakeRunner()))

    assert dataclasses.asdict(legacy) == dataclasses.asdict(graph)
    assert graph.verdict == "keep"
    assert graph.metrics == EXEC_RESULT["metrics"]
    assert graph.captured_predictions == CLAIMS
    assert set(graph.agent_outputs) == {
        "researcher", "data_quality_output", "factor_analyst_output",
        "strategist_output", "portfolio_construction_output",
        "risk_controller_output", "attribution_analyst_output",
        "anti_overfit_analyst_output", "backtest_diagnostics_output",
    }


def test_phase_functions_receive_identical_args(tmp_path, monkeypatch):
    legacy_calls: list = []
    graph_calls: list = []
    _install_phase_fakes(monkeypatch, legacy_calls)
    _run_phases_branch(make_ctx(tmp_path, FakeRunner()))

    _install_phase_fakes(monkeypatch, graph_calls)
    run_phases_via_graph(make_ctx(tmp_path, FakeRunner()))

    assert [c[0] for c in legacy_calls] == [
        "researcher", "claims", "execution", "evaluation",
    ]
    assert [c[0] for c in graph_calls] == [c[0] for c in legacy_calls]

    def _comparable(call):
        """call 形如 (名字, *位置参数, kwargs字典)；kwargs 里的 on_event 是
        每次新建的回调对象——剥离后比较，另断言两路都传了（agent 事件
        接线，2026-11-02）。"""
        kwargs = call[-1]
        stripped = {k: v for k, v in kwargs.items() if k != "on_event"}
        return (*call[:-1], stripped)

    def _has_on_event(call):
        # FakeRunner._make_agent_on_event 返回 None（桩不产事件），
        # 这里只断言「接线存在」——键在 kwargs 里。
        return "on_event" in call[-1]

    assert _comparable(graph_calls[0]) == _comparable(legacy_calls[0])
    assert _has_on_event(graph_calls[0]) and _has_on_event(legacy_calls[0])
    assert graph_calls[1][1:] == legacy_calls[1][1:]  # 存证参数（回测前）
    assert _comparable(graph_calls[2]) == _comparable(legacy_calls[2])
    assert _has_on_event(graph_calls[2]) and _has_on_event(legacy_calls[2])
    assert _comparable(graph_calls[3]) == _comparable(legacy_calls[3])
    assert _has_on_event(graph_calls[3]) and _has_on_event(legacy_calls[3])


def test_domain_event_sequence_identical(tmp_path, monkeypatch):
    _install_phase_fakes(monkeypatch, [])

    legacy_runner = FakeRunner()
    _run_phases_branch(make_ctx(tmp_path, legacy_runner))
    graph_runner = FakeRunner()
    run_phases_via_graph(make_ctx(tmp_path, graph_runner))

    assert legacy_runner.domain_events == graph_runner.domain_events
    phases = [(e[2]["phase"], e[2]["status"]) for e in graph_runner.domain_events]
    assert phases == [
        ("researcher", "started"), ("researcher", "done"),
        ("execution", "started"), ("execution", "done"),
        ("evaluation", "started"), ("evaluation", "done"),
    ]


def test_non_dict_researcher_defense_identical(tmp_path, monkeypatch):
    import strategy_research.core.autoresearch as ar

    _install_phase_fakes(monkeypatch, [])
    # 后装者生效：researcher 返回字符串占位（覆盖 _install 里的 dict fake）
    monkeypatch.setattr(
        ar, "run_researcher_phase",
        lambda *a, **k: {"researcher_output": "max_iter placeholder"},
    )

    legacy = _run_phases_branch(make_ctx(tmp_path, FakeRunner()))
    graph = run_phases_via_graph(make_ctx(tmp_path, FakeRunner()))

    assert dataclasses.asdict(legacy) == dataclasses.asdict(graph)
    assert graph.agent_outputs["researcher"] == {}


# ── abort（novelty 拒绝）──────────────────────────────────


def test_abort_path_identical_and_side_effects_match(tmp_path, monkeypatch):
    calls: list = []
    _install_phase_fakes(monkeypatch, calls)

    legacy_runner = FakeRunner(novel=False)
    legacy = _run_phases_branch(make_ctx(tmp_path, legacy_runner))
    graph_runner = FakeRunner(novel=False)
    graph = run_phases_via_graph(make_ctx(tmp_path, graph_runner))

    assert legacy == graph == {
        "round": 3, "run_name": "run_x",
        "aborted": True, "reason": "novelty_rejected",
    }
    # 下游从未执行：claims 未存证、execution/evaluation 未调用
    assert not any(c[0] in ("claims", "execution", "evaluation") for c in calls)
    # 归档 + 拒绝事件各一次，且两路径一致
    assert legacy_runner.archived == graph_runner.archived
    assert len(graph_runner.archived) == 1
    legacy_rejects = [e for e in legacy_runner.domain_events if e[1] == "study_round_rejected"]
    graph_rejects = [e for e in graph_runner.domain_events if e[1] == "study_round_rejected"]
    assert legacy_rejects == graph_rejects and len(graph_rejects) == 1


# ── 图拓扑（平台观测事件）────────────────────────────────


def test_topology_runs_five_stages_in_order(tmp_path, monkeypatch):
    _install_phase_fakes(monkeypatch, [])
    probe = Probe()
    ctx = make_ctx(tmp_path, FakeRunner())

    engine = build_research_engine(ctx, sink=probe)
    engine.run({})

    assert probe.started() == ["researcher", "novelty", "claims", "execution", "evaluation"]


def test_novelty_dup_routes_to_end_before_claims(tmp_path, monkeypatch):
    _install_phase_fakes(monkeypatch, [])
    probe = Probe()
    ctx = make_ctx(tmp_path, FakeRunner(novel=False))

    engine = build_research_engine(ctx, sink=probe)
    state = engine.run({})

    assert probe.started() == ["researcher", "novelty"]
    assert state["novelty_ok"] is False
    assert state["hypothesis"] == "h1"  # 提取发生在拒绝之前，state 保留


# ── 灰度门 ───────────────────────────────────────────────


def test_branch_resolves_to_graph_always(monkeypatch):
    """阶段 4：research_graph 门已退役——phases 分支恒走图实现。"""
    monkeypatch.delenv("QNODES_RESEARCH_GRAPH", raising=False)
    assert _resolve_branch_fn("phases") is run_phases_via_graph


def test_branch_flag_env_has_no_effect(monkeypatch):
    """退役后环境变量不再改变解析结果（记录删除契约）。"""
    for value in ("0", "1", "false"):
        monkeypatch.setenv("QNODES_RESEARCH_GRAPH", value)
        assert _resolve_branch_fn("phases") is run_phases_via_graph


def test_branch_unknown_engine_fallback(monkeypatch):
    """脏 engine 值回退 phases（=图形态，同一语义）。"""
    monkeypatch.delenv("QNODES_RESEARCH_GRAPH", raising=False)
    assert _resolve_branch_fn("not_a_real_engine") is run_phases_via_graph


def test_langgraph_engine_value_falls_back_to_graph(monkeypatch):
    """P5-2 退役后：engine='langgraph' 走回落（warning → 图路径），
    灰度门环境变量不影响解析。"""
    from strategy_research.core.study.graph_preset import run_phases_via_graph

    monkeypatch.setenv("QNODES_RESEARCH_GRAPH", "1")
    assert _resolve_branch_fn("langgraph") is run_phases_via_graph
    monkeypatch.delenv("QNODES_RESEARCH_GRAPH")
    assert _resolve_branch_fn("langgraph") is run_phases_via_graph
