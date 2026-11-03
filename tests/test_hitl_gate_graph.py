"""HITL novelty 审批门（图路径暂停段）测试。

设计：docs/stage-hitl-gate-graph-design-20261027.md。
覆盖：构建期门注册（关门无 gate 节点、开门拓扑停在下单前）/
中断 payload 五字段契约（照抄 langgraph_engine._make_novelty_gate_node）/
落库 + awaiting_approval 事件 + paused dict 六字段（照抄
langgraph_engine.py:639-657）/ 空假设防御放行 / store hitl 列
迁移与往返。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from strategy_research.kernel.runtimes import INTERRUPT_KEY
from strategy_research.apps.study.graph_preset import (
    build_research_engine,
    run_phases_via_graph,
)
from strategy_research.apps.study.phase_engine import BranchContext, RoundOutcome

RESEARCHER_RESULT = {
    "researcher_output": {
        "hypothesis": "h1",
        "predicted_affected": ["calmar"],
        "predictions": {"calmar": 0.6},
    }
}
EXEC_RESULT = {
    "metrics": {"calmar": 0.7},
    "strategist_output": {},
    "backtest_result": {"equity": [1.0]},
    "backtest_error": None,
}
EVAL_RESULT = {"verdict": "keep"}


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind: str, data: dict) -> None:
        self.events.append((kind, data))

    def started(self) -> list[str]:
        return [d["node"] for k, d in self.events if k == "node.started"]


class FakeStudyStore:
    def __init__(self) -> None:
        self.created: list[dict] = []

    def create_interrupt(self, **kwargs):
        self.created.append(dict(kwargs))
        return SimpleNamespace(
            interrupt_id=f"interrupt_{len(self.created)}",
            **kwargs,
        )


class FakeRunner:
    def __init__(self, hitl: bool = False, novel: bool = True) -> None:
        self._loop_strategy = None
        self._idle_rounds = 0
        self.novel = novel
        self.hitl = hitl
        self.study_store = FakeStudyStore()
        self.domain_events: list[tuple[str, str, dict]] = []

    def _get_study(self):
        return SimpleNamespace(
            engine="phases", hitl=self.hitl, behavior=None,
            session_id="sess", study_id="sid",
            metric_targets=[{"name": "calmar"}],
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
        pass


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


def _install_phase_fakes(monkeypatch) -> None:
    import strategy_research.apps.autoresearch as ar
    import strategy_research.apps.study.graph_preset as gp
    import strategy_research.apps.study.phase_engine as pe

    monkeypatch.setattr(
        ar, "run_researcher_phase",
        lambda *a, **k: dict(RESEARCHER_RESULT),
    )
    monkeypatch.setattr(
        ar, "run_execution_phase", lambda *a, **k: dict(EXEC_RESULT),
    )
    monkeypatch.setattr(
        ar, "run_evaluation_phase", lambda *a, **k: dict(EVAL_RESULT),
    )
    fake_claims = lambda *a, **k: {"calmar": 0.6}  # noqa: E731
    monkeypatch.setattr(pe, "_capture_round_claims", fake_claims)
    monkeypatch.setattr(gp, "_capture_round_claims", fake_claims)


# ── 构建期门注册 ─────────────────────────────────────────


def test_gate_node_absent_when_hitl_off(tmp_path, monkeypatch):
    _install_phase_fakes(monkeypatch)
    probe = Probe()
    ctx = make_ctx(tmp_path, FakeRunner(hitl=False))

    engine = build_research_engine(ctx, sink=probe)
    state = engine.run({})

    assert "gate" not in engine._fns
    assert probe.started() == [
        "researcher", "novelty", "claims", "execution", "evaluation",
    ]
    assert INTERRUPT_KEY not in state


def test_gate_stops_graph_before_claims_when_hitl_on(tmp_path, monkeypatch):
    _install_phase_fakes(monkeypatch)
    probe = Probe()
    ctx = make_ctx(tmp_path, FakeRunner(hitl=True))

    engine = build_research_engine(ctx, sink=probe)
    state = engine.run({})

    # 停在门上：门节点启动并中断，claims/execution/evaluation 从未运行
    assert probe.started() == ["researcher", "novelty", "gate"]
    assert INTERRUPT_KEY in state
    assert state[INTERRUPT_KEY]["reason"] == "novelty_gate"
    assert state[INTERRUPT_KEY]["node"] == "gate"


# ── payload 契约（照抄旧引擎）────────────────────────────


def test_gate_payload_matches_legacy_contract(tmp_path, monkeypatch):
    _install_phase_fakes(monkeypatch)
    ctx = make_ctx(tmp_path, FakeRunner(hitl=True))

    state = build_research_engine(ctx).run({})

    payload = state[INTERRUPT_KEY]["data"]
    assert payload["type"] == "novelty_gate"
    assert payload["study_id"] == "sid"
    assert payload["round_num"] == 3
    assert payload["hypothesis"] == "h1"
    assert payload["predicted_affected"] == ["calmar"]
    # 暂停契约：后继 claims 要读 researcher_output，payload 必须携带
    assert payload["researcher_output"] == RESEARCHER_RESULT["researcher_output"]
    assert payload["message"] == "Round 3: 请审批 researcher 假设"


def test_gate_emits_interrupted_event_before_pause(tmp_path, monkeypatch):
    _install_phase_fakes(monkeypatch)
    runner = FakeRunner(hitl=True)

    build_research_engine(make_ctx(tmp_path, runner)).run({})

    phases = [
        (e[2]["phase"], e[2]["status"])
        for e in runner.domain_events if e[1] == "study_phase"
    ]
    assert phases == [("researcher", "started"), ("researcher", "done"),
                      ("novelty_gate", "interrupted")]


# ── 暂停 dict 契约 + 落库 ────────────────────────────────


def test_paused_dict_contract_and_interrupt_recorded(tmp_path, monkeypatch):
    _install_phase_fakes(monkeypatch)
    runner = FakeRunner(hitl=True)

    result = run_phases_via_graph(make_ctx(tmp_path, runner))

    # 六字段 dict（与 langgraph_engine.py:649-657 同形）
    assert result == {
        "round": 3,
        "run_name": "run_x",
        "paused_for_approval": True,
        "study_id": "sid",
        "interrupt_id": "interrupt_1",
        "hypothesis": "h1",
    }
    # 落库：payload JSON 可往返出 hypothesis
    assert len(runner.study_store.created) == 1
    rec = runner.study_store.created[0]
    assert rec["study_id"] == "sid"
    assert rec["round_num"] == 3
    assert rec["interrupt_type"] == "novelty_gate"
    assert json.loads(rec["payload"])["hypothesis"] == "h1"
    # awaiting_approval 事件（带真实 interrupt_id）
    awaiting = [
        e for e in runner.domain_events
        if e[1] == "study_phase" and e[2].get("status") == "awaiting_approval"
    ]
    assert len(awaiting) == 1
    assert awaiting[0][2]["interrupt_id"] == "interrupt_1"
    assert awaiting[0][2]["hypothesis"] == "h1"
    assert awaiting[0][2]["gate_message"] == "Round 3: 请审批 researcher 假设"


def test_hitl_off_run_returns_round_outcome(tmp_path, monkeypatch):
    _install_phase_fakes(monkeypatch)

    result = run_phases_via_graph(make_ctx(tmp_path, FakeRunner(hitl=False)))

    assert isinstance(result, RoundOutcome)
    assert result.verdict == "keep"


def test_empty_hypothesis_passes_gate_through(tmp_path, monkeypatch):
    import strategy_research.apps.autoresearch as ar

    _install_phase_fakes(monkeypatch)
    monkeypatch.setattr(
        ar, "run_researcher_phase",
        lambda *a, **k: {"researcher_output": {"hypothesis": ""}},
    )
    runner = FakeRunner(hitl=True)

    result = run_phases_via_graph(make_ctx(tmp_path, runner))

    # 防御放行：不暂停、不落库（空假设的正常处理在 novelty 去重门上游）
    assert isinstance(result, RoundOutcome)
    assert runner.study_store.created == []


# ── 未知中断落点 ─────────────────────────────────────────


def test_unknown_interrupt_becomes_abort_not_crash(tmp_path, monkeypatch):
    """max_hops 等非审批中断 → abort dict 跳轮（runner 预算入账 + continue），
    绝不落到 RoundOutcome 构造去 KeyError 崩轮。"""
    import strategy_research.apps.study.graph_preset as gp

    _install_phase_fakes(monkeypatch)
    runner = FakeRunner(hitl=True)

    real_init = gp.GraphEngine.__init__

    def init(self, *a, **k):
        k["max_hops"] = 2  # 第 3 跳（gate 节点）触发 hops 守卫
        real_init(self, *a, **k)

    monkeypatch.setattr(gp.GraphEngine, "__init__", init)

    result = run_phases_via_graph(make_ctx(tmp_path, runner))

    assert result["aborted"] is True
    assert result["reason"] == "interrupt_max_hops"
    assert runner.study_store.created == []  # 不伪装成审批暂停、不落库


# ── resume（方案 A：重建 + start=）────────────────────────


def _install_resume_fakes(monkeypatch, calls: list) -> dict:
    """记录调用序的 phase fakes；返回喂给 resume 的 interrupt payload。"""
    import strategy_research.apps.autoresearch as ar
    import strategy_research.apps.study.graph_preset as gp
    import strategy_research.apps.study.phase_engine as pe

    payload_researcher = {
        "hypothesis": "h1",
        "predicted_affected": ["calmar"],
        "predictions": {"calmar": 0.6},
    }

    monkeypatch.setattr(
        ar, "run_researcher_phase",
        lambda *a, **k: calls.append("researcher") or dict(RESEARCHER_RESULT),
    )

    def fake_exec(runner, path, strategy, current_state, researcher_output, run_dir, **k):
        calls.append(("execution", dict(researcher_output)))
        return dict(EXEC_RESULT)

    def fake_eval(*a, **k):
        calls.append("evaluation")
        return dict(EVAL_RESULT)

    def fake_claims(runner, round_num, researcher_output, predicted_affected, metric_targets):
        calls.append(("claims", dict(researcher_output), list(predicted_affected)))
        return {"calmar": 0.6}

    monkeypatch.setattr(ar, "run_evaluation_phase", fake_eval)
    monkeypatch.setattr(gp, "_run_execution_with_parse_retry", fake_exec)
    monkeypatch.setattr(pe, "_capture_round_claims", fake_claims)
    monkeypatch.setattr(gp, "_capture_round_claims", fake_claims)

    return {
        "type": "novelty_gate", "study_id": "sid", "round_num": 3,
        "hypothesis": "h1", "predicted_affected": ["calmar"],
        "researcher_output": payload_researcher,
        "message": "Round 3: 请审批 researcher 假设",
    }


def test_resume_continues_from_claims_without_rerun(tmp_path, monkeypatch):
    """方案 A 核心：claims 起跑、researcher/novelty/gate 不重跑；claims 拿到
    payload 里的 researcher_output（决策注入，防 n8n 弊病：恢复值不得被
    重新推导遮蔽）。"""
    import strategy_research.apps.study.graph_preset as gp

    calls: list = []
    payload = _install_resume_fakes(monkeypatch, calls)
    runner = FakeRunner(hitl=True)

    result = gp.resume_round_via_graph(
        make_ctx(tmp_path, runner), interrupt_payload=payload,
    )

    assert isinstance(result, RoundOutcome)
    assert result.metrics == EXEC_RESULT["metrics"]
    assert result.verdict == "keep"
    assert result.hypothesis == "h1"
    assert result.captured_predictions == {"calmar": 0.6}
    # 调用序：claims 起跑（payload 注入）→ execution → evaluation
    assert calls == [
        ("claims", payload["researcher_output"], ["calmar"]),
        ("execution", payload["researcher_output"]),
        "evaluation",
    ]
    # resume 事件可观测（与 langgraph_resume 平行的契约位）
    phases = [
        (e[2]["phase"], e[2]["status"])
        for e in runner.domain_events if e[1] == "study_phase"
    ]
    assert phases[0] == ("graph_resume", "started")
    assert phases[-1] == ("graph_resume", "done")


def test_runner_resume_dispatch_reads_payload_and_maps_dict(tmp_path, monkeypatch):
    """runner 方法端到端：从 study_interrupts 行读 payload（JSON 往返=
    跨进程重建）→ 续跑 → RoundOutcome 映射为 runner 循环消费的 dict。"""
    import json as _json
    from types import SimpleNamespace as NS

    from strategy_research.apps.study.runner import AutoresearchRunner

    calls: list = []
    payload = _install_resume_fakes(monkeypatch, calls)

    class StoreWithGet(FakeStudyStore):
        def get_interrupt_for_round(self, sid, round_num):
            return NS(
                payload=_json.dumps(payload, ensure_ascii=False),
                status="approved",
            )

    study = NS(
        engine="phases", hitl=True, behavior=None, session_id="sess",
        study_id="sid", metric_targets=[{"name": "calmar"}],
        objective="obj", lazy_detection_interval=0, keep_recent=0,
    )
    fake_self = NS(
        study_store=StoreWithGet(),
        _get_study=lambda: study,
        _emit=lambda session, kind, data: None,
        _loop_strategy=None,
        # agent 事件接线（2026-11-02）的新 runner 契约；桩不产事件。
        _make_agent_on_event=lambda session_id: None,
    )

    result = AutoresearchRunner._resume_round_via_graph(
        fake_self, tmp_path, "strat", {"k": 1}, tmp_path / "run_0001",
        None, session="sess", sid="sid", round_num=3, directive_text=None,
    )

    # runner 循环契约（H1 段随后补 passed_now/e2_passed）
    assert result["metrics"] == EXEC_RESULT["metrics"]
    assert result["verdict"] == "keep"
    assert result["run_name"] == "run_0001"
    assert result["hypothesis"] == "h1"
    assert calls == [
        ("claims", payload["researcher_output"], ["calmar"]),
        ("execution", payload["researcher_output"]),
        "evaluation",
    ]


# ── store：hitl 列迁移与往返 ─────────────────────────────


def test_study_store_hitl_roundtrip(tmp_path):
    from strategy_research.apps.study.store import StudyStore

    db = tmp_path / "hitl.db"
    with StudyStore(db_path=db) as store:
        study = store.create_study(
            owner_session_id="owner", goal_id=None,
            objective="obj", workspace_path=str(tmp_path),
            strategy_name="s1", hitl=True,
        )
        fetched = store.get_study(study.study_id)
        assert fetched.hitl is True
        default_study = store.create_study(
            owner_session_id="owner", goal_id=None,
            objective="obj2", workspace_path=str(tmp_path),
            strategy_name="s2",
        )
        assert store.get_study(default_study.study_id).hitl is False


def test_study_store_hitl_migration_from_pre_hitl_db(tmp_path):
    """旧库（无 hitl 列）打开即迁移：列补齐、旧行默认关门。"""
    import sqlite3

    from strategy_research.apps.study.store import StudyStore

    db = tmp_path / "legacy.db"
    with StudyStore(db_path=db) as store:
        old = store.create_study(
            owner_session_id="owner", goal_id=None,
            objective="old", workspace_path=str(tmp_path),
            strategy_name="s0",
        )

    # 模拟 pre-hitl 库：删列（SQLite ≥3.35 支持 DROP COLUMN）
    raw = sqlite3.connect(db)
    raw.execute("ALTER TABLE studies DROP COLUMN hitl")
    raw.commit()
    raw.close()

    with StudyStore(db_path=db) as store:
        # 旧行回填默认关门
        assert store.get_study(old.study_id).hitl is False
        # 新行可开门
        new = store.create_study(
            owner_session_id="owner", goal_id=None,
            objective="new", workspace_path=str(tmp_path),
            strategy_name="s1", hitl=True,
        )
        assert store.get_study(new.study_id).hitl is True
