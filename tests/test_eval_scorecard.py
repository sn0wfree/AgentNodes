"""Unit tests for A7 eval scorecard + golden tasks."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentnodes.evals.scorecard import (  # noqa: E402
    GoldenTask,
    RunRecord,
    build_scorecard,
    load_golden_tasks,
    score_run,
)

# 2026-11-05：evals/ 已收编进 src/agentnodes/evals/（随包分发）。
# 用目标模块的 __file__ 定位，而不是数 parent 层数——包再搬家时自愈。
import agentnodes.evals.scorecard as _scorecard_mod

GOLDEN = Path(_scorecard_mod.__file__).parent / "golden_tasks.jsonl"


class TestLoad:
    def test_loads_jsonl(self):
        tasks = load_golden_tasks(GOLDEN)
        assert len(tasks) >= 4
        assert tasks[0].id
        assert tasks[0].prompt

    def test_golden_set_is_20_tasks(self):
        tasks = load_golden_tasks(GOLDEN)
        assert len(tasks) == 70
        ids = [t.id for t in tasks]
        assert len(ids) == len(set(ids))
        # Safety / budget / hard-reason slices must stay present.
        assert any("safety" in t.tags for t in tasks)
        assert any("budget" in t.tags for t in tasks)
        assert any("multi" in t.tags for t in tasks)
        assert any("injection" in t.tags for t in tasks)
        assert any("reason" in t.tags for t in tasks)
        assert any("source:public-pattern" in t.tags for t in tasks)

    def test_from_dict_defaults(self):
        t = GoldenTask.from_dict({"id": "x", "prompt": "p"})
        assert t.expected_tools == []
        assert t.toolset is None


class TestScoreRun:
    def test_perfect_run(self):
        t = GoldenTask(id="a", prompt="p", expected_tools=["read", "write"], must_contain=["IC"])
        r = RunRecord(task_id="a", tools_used=["read", "write"], answer="IC=0.1")
        s = score_run(t, r)
        assert s.tool_recall == 1.0
        assert s.total == 1.0

    def test_partial_tool_recall(self):
        t = GoldenTask(id="a", prompt="p", expected_tools=["read", "write"])
        r = RunRecord(task_id="a", tools_used=["read"])
        s = score_run(t, r)
        assert s.tool_recall == 0.5
        assert s.total < 1.0

    def test_forbidden_tool_penalty(self):
        t = GoldenTask(id="a", prompt="p", expected_tools=[], forbidden_tools=["write"])
        r = RunRecord(task_id="a", tools_used=["write"])
        s = score_run(t, r)
        assert s.tool_violations == 1
        assert s.total < 0.9

    def test_budget_violation(self):
        t = GoldenTask(id="a", prompt="p", budget_max_tokens=100)
        r = RunRecord(task_id="a", tokens_used=1000)
        s = score_run(t, r)
        assert s.budget_ok is False
        assert "budget_exceeded" in "".join(s.notes)

    def test_must_contain(self):
        t = GoldenTask(id="a", prompt="p", must_contain=["sharpe"])
        bad = score_run(t, RunRecord(task_id="a", answer="nothing"))
        good = score_run(t, RunRecord(task_id="a", answer="sharpe=1.2"))
        assert bad.content_ok is False
        assert good.content_ok is True

    def test_not_completed(self):
        t = GoldenTask(id="a", prompt="p")
        r = RunRecord(task_id="a", success=False, finished_reason="error")
        s = score_run(t, r)
        assert s.completed is False


class TestScorecard:
    def test_build_and_mean(self):
        tasks = [
            GoldenTask(id="t1", prompt="p", expected_tools=["read"]),
            GoldenTask(id="t2", prompt="p", expected_tools=["read"]),
        ]
        runs = [
            RunRecord(task_id="t1", tools_used=["read"], success=True),
            RunRecord(task_id="t2", tools_used=[], success=False, finished_reason="error"),
        ]
        card = build_scorecard(tasks, runs)
        assert card.mean > 0
        assert 0.0 <= card.pass_rate(0.7) <= 1.0
        d = card.to_dict()
        assert d["n"] == 2

    def test_missing_run_skipped(self):
        tasks = [GoldenTask(id="t1", prompt="p")]
        card = build_scorecard(tasks, [])
        assert card.scores == []
        assert card.mean == 0.0
