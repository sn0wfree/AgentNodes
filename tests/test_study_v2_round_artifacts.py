"""Study v2 M3 tests — directory bootstrap, inheritance, artifacts.

Covers:
- state_store: load/save/init + atomic write + missing fallback
- round_manifest: manifest build/render/journal + inheritance decisions
- store.update_round review overlay
- _init_study_dir bootstrap layout
- end-to-end round artifacts via a stubbed runner
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.core.study.bootstrap import init_study_dir as _init_study_dir
from strategy_research.core.study import StudyStore
from strategy_research.core.study.round_manifest import (
    append_journal_md,
    build_manifest,
    load_manifest,
    render_round_markdown,
    resolve_adopted_run,
    resolve_adopted_run_for_start,
    save_manifest,
)
from strategy_research.core.study.state_store import (
    init,
    load,
    save,
)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db")
    )
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json")
    )


@pytest.fixture
def store(tmp_path: Path):
    return StudyStore(db_path=tmp_path / "goals.db")


@pytest.fixture
def goal_store():
    from strategy_research.core.goal import GoalStore
    return GoalStore()


# ── state_store ────────────────────────────────────────────────────────


def test_state_missing_returns_defaults(tmp_path):
    st = load(tmp_path, "study_abc")
    assert st.last_completed_round == 0
    assert st.last_keep_run_dir is None


def test_state_roundtrip_and_atomic(tmp_path):
    init(tmp_path, "study_abc", baseline_best={"calmar": 0.3})
    st = load(tmp_path, "study_abc")
    assert st.baseline_best == {"calmar": 0.3}
    st.last_completed_round = 5
    st.last_keep_run_dir = "rounds/round_0005/run_0001"
    st.best_metrics = {"calmar": 1.2}
    save(tmp_path, "study_abc", st)
    reloaded = load(tmp_path, "study_abc")
    assert reloaded.last_completed_round == 5
    assert reloaded.last_keep_run_dir == "rounds/round_0005/run_0001"
    assert reloaded.best_metrics == {"calmar": 1.2}
    # no tmp file left behind
    assert not (tmp_path / "study" / "study_abc" / "state.json.tmp").exists()


def test_state_corrupt_returns_defaults(tmp_path):
    p = tmp_path / "study" / "study_abc" / "state.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    st = load(tmp_path, "study_abc")
    assert st.last_completed_round == 0


# ── round_manifest ─────────────────────────────────────────────────────


def _manifest(round_num: int = 3, verdict: str = "keep", reason: str = "ok") -> dict:
    return build_manifest(
        round_num=round_num,
        inherited_from="rounds/round_0002/run_0001",
        adopted_run="rounds/round_0002/run_0001",
        run_name="run_0001",
        hypothesis="动量因子",
        levers=["momentum"], predicted_affected=["calmar"],
        strategy_changes=[{"param": "top_n", "old": 10, "new": 20}],
        metrics={"calmar": 1.2, "sharpe": 0.6, "max_dd": -0.1},
        prev_metrics={"calmar": 0.9},
        baseline_metrics={"calmar": 0.5},
        verdict=verdict, verdict_reason=reason,
        gates=[{"id": "risk-max-dd", "result": "pass", "enforced": True}],
        budget={"turns_used": 10, "time_used_s": 300,
                "total": {"turns": 100, "time_s": 7200}},
    )


def test_manifest_phase1_shape(tmp_path):
    m = _manifest()
    assert m["round"] == 3
    assert m["inherited_from"] == "rounds/round_0002/run_0001"
    assert m["metrics"]["vs_prev"]["calmar"] == "+0.300"
    assert m["review"] is None          # phase 2 fills later
    p = save_manifest(m, tmp_path, "s1", 3)
    assert p.exists()
    assert load_manifest(tmp_path, "s1", 3)["round"] == 3


def test_summary_md_renders(tmp_path):
    md = render_round_markdown(_manifest())
    assert "# Round 3 总结" in md
    assert "keep" in md
    assert "动量因子" in md
    # discard rendering
    md2 = render_round_markdown(_manifest(verdict="discard", reason="过拟合"))
    assert "discard" in md2 and "过拟合" in md2


def test_journal_append_and_discard_mark(tmp_path):
    p = append_journal_md(tmp_path, "s1", _manifest(round_num=3), "研究动量")
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "[keep ✓]" in content and "研究动量" in content
    append_journal_md(
        tmp_path, "s1", _manifest(round_num=4, verdict="discard", reason="过拟合"),
        "研究动量",
    )
    content2 = p.read_text(encoding="utf-8")
    assert "[discard ❌]" in content2 and "否决：过拟合" in content2


def test_inheritance_decisions():
    # keep → adopted run is the current run
    a, i, stop = resolve_adopted_run(
        keep_run_dir="rounds/round_0003/run_0001", round_num=3,
        verdict="keep", discard_streak=0, max_discard_streak=5,
    )
    assert a == "rounds/round_0003/run_0001" and not stop
    # discard → roll back to last keep (or baseline)
    a2, i2, _ = resolve_adopted_run(
        keep_run_dir=None, round_num=3, verdict="discard",
        discard_streak=1, max_discard_streak=5,
    )
    assert a2 == "baseline"
    # streak hits the cap → stop
    _, _, stop3 = resolve_adopted_run(
        keep_run_dir=None, round_num=3, verdict="discard",
        discard_streak=4, max_discard_streak=5,
    )
    assert stop3
    assert resolve_adopted_run_for_start(None) == "baseline"
    assert resolve_adopted_run_for_start("rounds/round_0002/run_0001") == \
        "rounds/round_0002/run_0001"


# ── store.update_round (phase-2 overlay) ───────────────────────────────


def test_update_round_review_overlay(store, goal_store):
    goal, study = _setup_store(store, goal_store)
    store.append_round(study.study_id, 1, "run_0001", verdict="keep")
    updated = store.update_round(
        study.study_id, 1, {"deviation": "low", "info_gap": False, "next_focus": "x"},
    )
    assert updated.review == {"deviation": "low", "info_gap": False, "next_focus": "x"}
    rec = store.get_round(study.study_id, 1)
    assert rec.review["deviation"] == "low"


def _setup_store(store, goal_store):
    from strategy_research.core.goal.context import default_goal_criteria
    goal = goal_store.replace_goal(
        session_id="sess-m3", objective="目标", criteria=default_goal_criteria(),
    )
    study = store.create_study(
        owner_session_id="sess-m3", goal_id=goal.goal_id,
        objective="目标", workspace_path="/tmp/ws", strategy_name="demo",
    )
    return goal, study


# ── _init_study_dir bootstrap ──────────────────────────────────────────


def test_init_study_dir_layout(tmp_path):
    ws = tmp_path
    (ws / "strategies" / "demo").mkdir(parents=True)
    (ws / "strategies" / "demo" / "strategy.py").write_text(
        "PARAMS = {'top_n': 5}\n", encoding="utf-8",
    )
    _init_study_dir(ws, 'study_m3', 'demo', '研究动量')
    root = ws / "study" / "study_m3"
    # baseline copied from existing strategy
    assert "top_n" in (root / "baseline" / "strategy.py").read_text(encoding="utf-8")
    # results.tsv header with round column last
    header = (root / "results.tsv").read_text(encoding="utf-8").split("\n")[0]
    assert header.endswith("round")
    # guidance/todos/knowledge/state
    assert (root / "guidance.md").exists()
    assert "研究指引" in (root / "guidance.md").read_text(encoding="utf-8")
    assert (root / "todos.md").exists()
    assert (root / "knowledge.md").exists()
    st = load(ws, "study_m3")
    assert st.last_completed_round == 0
    # guidance override wins
    _init_study_dir(ws, "study_m3", "demo", "x", guidance_md="# 自定义指引")
    assert (root / "guidance.md").read_text(encoding="utf-8") == "# 自定义指引"


# ── end-to-end: stubbed runner produces artifacts ──────────────────────


def test_runner_round_artifacts_end_to_end(store, goal_store, monkeypatch, tmp_path):
    """A full study run (stubbed round) produces the v2 directory layout:
    rounds/round_NNNN/run_XXXX/strategy.py + manifest.json + summary.md +
    journal.md + state.json."""

    from strategy_research.core.study import runner as runner_mod
    from strategy_research.core.study.runner import AutoresearchRunner
    from strategy_research.core.study.scheduler import NullEmitter

    ws = tmp_path / "ws"
    (ws / "strategies" / "demo").mkdir(parents=True)
    (ws / "strategies" / "demo" / "strategy.py").write_text(
        "PARAMS = {}\nFACTOR_EXPRS = []\n", encoding="utf-8",
    )

    from strategy_research.core.goal.context import default_goal_criteria
    goal = goal_store.replace_goal(
        session_id="study_e2e", objective="研究动量",
        criteria=default_goal_criteria(), supersede=False,
    )
    study = store.create_study(
        owner_session_id="sess-e2e", goal_id=goal.goal_id,
        objective="研究动量", workspace_path=str(ws), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
        max_rounds=1,
    )
    store.update_goal_id(study.study_id, goal.goal_id)
    # bootstrap AFTER the study id exists (mirrors study_start order)
    _init_study_dir(ws, study.study_id, "demo", "研究动量")

    def _fake_researcher(path, strategy, state, run_dir, **kw):
        return {"researcher_output": {
            "hypothesis": "动量因子", "predicted_affected": ["calmar"],
        }}

    def _fake_execution(path, strategy, state, researcher, run_dir, **kw):
        return {
            "data_quality_output": {"ok": True},
            "factor_analyst_output": {"ok": True},
            "strategist_output": {"action": "optimize", "changes": [
                {"param": "top_n", "old": 10, "new": 20}]},
            "portfolio_construction_output": {"ok": True},
            "backtest_result": {"success": True, "run": "run_0001",
                               "metrics": {"calmar": 0.62}},
            "metrics": {"calmar": 0.62, "sharpe": 0.41, "max_dd": -0.1},
            "backtest_error": None,
        }

    def _fake_evaluation(path, strategy, backtest, metrics, run_dir, **kw):
        class _D:
            def to_dict(self):
                return {"stagnation_triggered": False, "reason": "ok"}
        return {
            "risk_controller_output": {"ok": True},
            "attribution_analyst_output": {"ok": True},
            "anti_overfit_analyst_output": {"ok": True},
            "backtest_diagnostics_output": {"ok": True},
            "decision": _D(),
            "verdict": "keep",
        }

    import strategy_research.apps.autoresearch as ar_mod
    monkeypatch.setattr(ar_mod, "run_researcher_phase", _fake_researcher)
    monkeypatch.setattr(ar_mod, "run_execution_phase", _fake_execution)
    monkeypatch.setattr(ar_mod, "run_evaluation_phase", _fake_evaluation)
    monkeypatch.setattr(
        runner_mod.AutoresearchRunner, "_round_cooldown", lambda self: 0.0,
    )
    monkeypatch.setattr(
        runner_mod.AutoresearchRunner, "_maybe_load_previous_summary",
        lambda self, study: None,
    )

    runner = AutoresearchRunner(
        study, store, control=runner_mod.ControlToken(), emitter=NullEmitter(),
    )

    async def main():
        from strategy_research.core.study import state_store as ss
        reason = await runner.run()
        assert reason == runner_mod.ShutdownReason.MAX_ROUNDS
        root = ws / "study" / study.study_id
        # round dir + run dir + strategy snapshot
        assert (root / "rounds" / "round_0001" / "run_0001" / "strategy.py").exists()
        # artifacts
        assert (root / "rounds" / "round_0001" / "manifest.json").exists()
        manifest = load_manifest(ws, study.study_id, 1)
        assert manifest is not None and manifest["round"] == 1
        assert (root / "rounds" / "round_0001" / "summary.md").exists()
        assert "[keep ✓]" in (root / "journal.md").read_text(encoding="utf-8")
        # state.json authority
        st = ss.load(ws, study.study_id)
        assert st.last_completed_round == 1
        assert st.last_keep_run_dir == "rounds/round_0001/run_0001"
        assert st.best_metrics["calmar"] == 0.62
        # results.tsv header has the trailing round column (row-level
        # write/update covered by dedicated unit tests)
        tsv = (root / "results.tsv").read_text(encoding="utf-8")
        assert tsv.split("\n")[0].endswith("round")
        # DB mirror row
        rec = store.get_round(study.study_id, 1)
        assert rec is not None and rec.verdict == "keep"

    import asyncio
    asyncio.run(main())
