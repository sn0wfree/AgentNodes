"""Study v2 M4 tests — review cycle + knowledge management.

Covers review_loop pure functions (parsing/normalize/gap/todos/knowledge/
compact) and the runner-integrated deviation loop (3x high → stop, phase-2
manifest overlay, todos application).
"""

from __future__ import annotations

import json

from strategy_research.apps.study.method import review_loop as rl
from strategy_research.apps.study import state_store as ss
from strategy_research.apps.study.runner import (
    SR_STUDY_MAX_DEVIATION,
    ShutdownReason,
)

# ── reviewer output parsing ────────────────────────────────────────────


def test_parse_review_json_and_fence():
    raw = json.dumps({"deviation": "high", "info_gap": True, "topics": ["动量"]})
    assert rl.parse_review_output(raw)["deviation"] == "high"
    fenced = "```json\n" + raw + "\n```"
    assert rl.parse_review_output(fenced)["deviation"] == "high"
    assert rl.parse_review_output("not json") == {}
    assert rl.parse_review_output("") == {}


def test_parse_collector_array():
    """Collector outputs a JSON array of entries — parsed as a list."""
    raw = json.dumps([
        {"topic": "动量崩盘研究", "source_url": "https://a",
         "summary": "s", "idea": "i", "relevance": "high",
         "collected_at": "2026-08-12"},
    ])
    out = rl.parse_review_output(raw)
    assert isinstance(out, list)
    assert out[0]["topic"] == "动量崩盘研究"
    fenced = "```json\n" + raw + "\n```"
    out2 = rl.parse_review_output(fenced)
    assert isinstance(out2, list)
    assert out2[0]["topic"] == "动量崩盘研究"
    # collector 输出 → append_knowledge 完整落条目（非"未命名"占位）
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "knowledge.md"
    n = rl.append_knowledge(p, out, "目标")
    assert n == 1
    text = p.read_text(encoding="utf-8")
    assert "动量崩盘研究" in text and "https://a" in text


def test_normalize_review_tolerates_bad_fields():
    r = rl.normalize_review({
        "deviation": "bogus", "info_gap": 1,
        "todo_updates": [{"action": "bogus", "id": "x"}, "not-a-dict"],
    })
    assert r["deviation"] == "low"
    assert r["info_gap"] is True
    assert r["todo_updates"] == []
    r2 = rl.normalize_review({
        "deviation": "high",
        "todo_updates": [
            {"action": "add", "id": "todo-005", "title": "验证背离因子", "note": "评审 round 5"},
        ],
    })
    assert r2["todo_updates"][0]["id"] == "todo-005"


# ── gap check (zero-LLM) ───────────────────────────────────────────────


def test_gap_check_zero_llm():
    # focus keywords not covered by objective/knowledge → gaps
    gaps = rl.gap_check("研究动量因子", "验证量价背离信号", "# 知识\n## 动量回归\n")
    assert any("背离" in g for g in gaps)
    # covered focus → no gaps
    gaps2 = rl.gap_check("研究动量因子", "动量因子回归验证", "动量 回归 验证")
    assert gaps2 == []


# ── todos application ──────────────────────────────────────────────────


def test_apply_todos_full_cycle(tmp_path):
    p = tmp_path / "todos.md"
    p.write_text(
        "# 任务子任务清单（评审维护）\n\n## 待办\n- [ ] todo-001 建立基线\n\n"
        "## 进行中\n\n## 已放弃\n\n",
        encoding="utf-8",
    )
    n = rl.apply_todos(tmp_path / "todos.md", [
        {"action": "add", "id": "todo-002", "title": "验证背离因子", "note": ""},
        {"action": "done", "id": "todo-001", "title": "建立基线", "note": "round 2 完成"},
        {"action": "abandon", "id": "todo-002", "title": "验证背离因子", "note": "数据不足"},
        {"action": "bogus", "id": "x", "title": "y"},   # dropped
    ], "研究动量")
    assert n == 3
    text = p.read_text(encoding="utf-8")
    assert "[x] todo-001 建立基线（round 2 完成）" in text      # done → 进行中
    assert "已放弃" in text and "todo-002" in text              # abandoned
    assert "bogus" not in text


def test_apply_todos_auto_id(tmp_path):
    p = tmp_path / "todos.md"
    n = rl.apply_todos(tmp_path / "todos.md", [
        {"action": "add", "id": "", "title": "首个任务", "note": ""},
    ], "目标")
    assert n == 1
    assert "todo-001" in p.read_text(encoding="utf-8")


# ── knowledge append + dedup ───────────────────────────────────────────


def test_append_knowledge_and_dedup(tmp_path):
    p = tmp_path / "knowledge.md"
    entries = [
        {"topic": "动量新研究", "source_url": "https://a", "summary": "s1",
         "idea": "i1", "relevance": "high", "collected_at": "2026-08-12"},
    ]
    assert rl.append_knowledge(p, entries, "目标") == 1
    # same topic+source → skipped
    assert rl.append_knowledge(p, entries, "目标") == 0
    text = p.read_text(encoding="utf-8")
    assert "动量新研究" in text and "https://a" in text


# ── collect scheduling ─────────────────────────────────────────────────


def test_should_collect():
    assert rl.should_collect(info_gap=True, round_num=1,
                             last_collect_round=1, collect_interval=5)
    assert rl.should_collect(info_gap=False, round_num=6,
                             last_collect_round=1, collect_interval=5)
    assert not rl.should_collect(info_gap=False, round_num=3,
                                 last_collect_round=1, collect_interval=5)


# ── knowledge compaction (rule prescreen) ──────────────────────────────


def test_maybe_compact_dedup(tmp_path):
    p = tmp_path / "knowledge.md"
    block = (
        "## 2026-08-12 · 动量新研究（relevance: high）\n"
        "- 来源：https://a\n- 摘要：s\n- idea：i\n\n"
    )
    # build file below threshold → no compaction
    small = "# 知识储备与 Idea 池\n\n" + block
    p.write_text(small, encoding="utf-8")
    assert rl.maybe_compact(p, max_size=100000) == {}
    # duplicate topics + exceeding size → compaction merges
    big = "# 知识储备与 Idea 池\n\n" + block * 3
    # make each block's topic unique except one duplicate pair
    big = big.replace("动量新研究", "动量新研究X", 2)
    p.write_text(big, encoding="utf-8")
    out = rl.maybe_compact(p, max_size=100, archive_path=tmp_path / "archive.md")
    assert out["removed"] >= 1
    assert out["kept"] >= 1
    assert (tmp_path / "archive.md").exists()


# ── runner integration: deviation loop stops study ─────────────────────


def test_runner_high_deviation_stops_after_3(tmp_path, monkeypatch):
    import asyncio
    import itertools

    import strategy_research.apps.autoresearch as ar_mod
    from strategy_research.apps.study.bootstrap import init_study_dir as _init_study_dir
    from strategy_research.core.goal import GoalStore
    from strategy_research.core.goal.context import default_goal_criteria
    from strategy_research.apps.study import runner as runner_mod
    from strategy_research.apps.study.runner import AutoresearchRunner, ControlToken
    from strategy_research.apps.study.scheduler import NullEmitter
    from strategy_research.apps.study.store import StudyStore

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    ws = tmp_path / "ws"
    (ws / "strategies" / "demo").mkdir(parents=True)
    (ws / "strategies" / "demo" / "strategy.py").write_text("PARAMS = {}\n")

    gs = GoalStore()
    store = StudyStore()
    goal = gs.replace_goal(
        session_id="study_dev", objective="x",
        criteria=default_goal_criteria(), supersede=False,
    )
    study = store.create_study(
        owner_session_id="s", goal_id=goal.goal_id, objective="x",
        workspace_path=str(ws), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
        max_rounds=None,
    )
    store.update_goal_id(study.study_id, goal.goal_id)
    _init_study_dir(ws, study.study_id, "demo", "x")

    h_counter = itertools.count(1)

    def rp(path, strategy, state, run_dir, **kw):
        # unique hypothesis per round, or AEGIS novelty rejects and the
        # round is aborted before the review cycle ever runs
        return {"researcher_output": {"hypothesis": f"h{next(h_counter)}",
                                      "predicted_affected": ["calmar"]}}

    def ep(path, strategy, state, researcher, run_dir, **kw):
        return {"data_quality_output": {}, "factor_analyst_output": {},
                "strategist_output": {"action": "optimize", "changes": []},
                "portfolio_construction_output": {},
                "backtest_result": {"success": True, "run": "run_0001",
                                    "metrics": {"calmar": 0.1}},
                "metrics": {"calmar": 0.1, "sharpe": 0.0, "max_dd": -0.3},
                "backtest_error": None}

    class D:
        def to_dict(self):
            return {"stagnation_triggered": False, "reason": "ok"}

    def ev(path, strategy, backtest, metrics, run_dir, **kw):
        return {"risk_controller_output": {}, "attribution_analyst_output": {},
                "anti_overfit_analyst_output": {}, "backtest_diagnostics_output": {},
                "decision": D(), "verdict": "discard"}

    # reviewer always says HIGH deviation
    def fake_reviewer(role, workspace_path, strategy_name, task, **kw):
        return json.dumps({
            "deviation": "high", "deviation_reason": "偏离",
            "info_gap": False, "topics": [],
            "todo_updates": [{"action": "add", "id": "", "title": "纠正方向", "note": ""}],
            "next_focus": "回到目标",
        })

    ar_mod.run_researcher_phase = rp
    ar_mod.run_execution_phase = ep
    ar_mod.run_evaluation_phase = ev
    runner_mod.AutoresearchRunner._round_cooldown = lambda self: 0.0
    runner_mod.AutoresearchRunner._maybe_load_previous_summary = lambda self, s: None
    # force "real" reviewer path but stub the LLM call itself
    import strategy_research.core.agent.role_factory as rf_mod
    monkeypatch.setattr(rf_mod, "should_use_real_llm", lambda: True)
    monkeypatch.setattr(rf_mod, "run_agent_via_llm", fake_reviewer)

    runner = AutoresearchRunner(
        study, store, control=ControlToken(), emitter=NullEmitter(),
    )

    async def main():
        reason = await runner.run()
        assert reason == ShutdownReason.REPEATED_DEVIATION
        st = ss.load(ws, study.study_id)
        assert st.continuous_deviation == SR_STUDY_MAX_DEVIATION
        # todos got applied (one add per round, dedup via id reuse is fine)
        todos = (ws / "study" / study.study_id / "todos.md").read_text(encoding="utf-8")
        assert "纠正方向" in todos
        # manifest phase-2 overlay present on the last round
        from strategy_research.apps.study import round_manifest as rm
        m = rm.load_manifest(ws, study.study_id, st.last_completed_round)
        assert m is not None and m.get("review", {}).get("deviation") == "high"

    asyncio.run(main())
