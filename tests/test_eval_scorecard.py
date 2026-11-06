"""Unit tests for A7 eval scorecard + golden tasks."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentnodes.evals.scorecard import (  # noqa: E402
    GoldenTask,
    RunRecord,
    build_scorecard,
    load_golden_tasks,
    normalized_cost,
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

    def test_golden_set_is_heldin_split(self):
        """C9 双集：``golden_tasks.jsonl`` 现为 **held-in** 子集。

        原 70 条全集拆成 held-in（仓库内，本文件）/ held-out（仓库外）。
        总数断言改到 manifest（它记录了并集条数），避免拿子集去断言全集。
        """
        tasks = load_golden_tasks(GOLDEN)
        ids = [t.id for t in tasks]
        assert len(ids) == len(set(ids))
        # held-in 必须是非空真子集
        assert 0 < len(tasks) < 70
        # Safety / budget / hard-reason slices must stay present.
        assert any("safety" in t.tags for t in tasks)
        assert any("budget" in t.tags for t in tasks)
        assert any("multi" in t.tags for t in tasks)
        assert any("injection" in t.tags for t in tasks)
        assert any("reason" in t.tags for t in tasks)
        assert any("source:public-pattern" in t.tags for t in tasks)

    def test_golden_union_is_still_70_tasks(self):
        """并集条数（manifest）必须是原来那 70 条——拆分不丢任务。"""
        manifest = Path(_scorecard_mod.__file__).parent / "golden_sets_manifest.json"
        if not manifest.exists():
            pytest.skip("split manifest missing (run scripts/arch/split_golden_sets.py)")
        import json

        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["total"] == 70
        assert data["heldin"]["count"] + data["heldout"]["count"] == 70

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


class TestCostDimension:
    """交付 1：cost 是独立维度，不进 total（D3 / W2）。"""

    def test_probe1_budget_task_cost(self):
        """探针 ①：预算任务 cost = 1 - min(1, tokens/budget)。"""
        t = GoldenTask(id="a", prompt="p", budget_max_tokens=2000)
        s = score_run(t, RunRecord(task_id="a", tokens_used=500))
        assert s.cost == pytest.approx(0.75)

    def test_cost_floors_at_zero_when_over_budget(self):
        t = GoldenTask(id="a", prompt="p", budget_max_tokens=1000)
        s = score_run(t, RunRecord(task_id="a", tokens_used=5000))
        assert s.cost == 0.0, "超预算 cost 必须夹到 0，不得为负"

    def test_probe2_no_budget_cost_none(self):
        """探针 ②：无预算任务 cost=None（不是 0，不参与 cost 均值）。"""
        t = GoldenTask(id="a", prompt="p")
        s = score_run(t, RunRecord(task_id="a", tokens_used=5000))
        assert s.cost is None
        assert normalized_cost(t, RunRecord(task_id="a", tokens_used=5000)) is None

    def test_total_unchanged_by_cost(self):
        """total 五项权重一个字不改：加了 cost 也一分不动。"""
        t = GoldenTask(id="a", prompt="p", expected_tools=["read"], budget_max_tokens=2000)
        cheap = score_run(t, RunRecord(task_id="a", tools_used=["read"], tokens_used=10))
        dear = score_run(t, RunRecord(task_id="a", tools_used=["read"], tokens_used=1900))
        assert cheap.total == dear.total == 1.0
        assert cheap.cost > dear.cost

    def test_cost_mean_excludes_none(self):
        """禁混合均值：cost_mean 只取有预算任务的 cost 样本。"""
        tasks = [
            GoldenTask(id="bg", prompt="p", budget_max_tokens=1000),
            GoldenTask(id="free1", prompt="p"),
            GoldenTask(id="free2", prompt="p"),
        ]
        runs = [
            RunRecord(task_id="bg", tokens_used=500),
            RunRecord(task_id="free1", tokens_used=99999),
            RunRecord(task_id="free2", tokens_used=99999),
        ]
        card = build_scorecard(tasks, runs)
        assert card.cost_samples == [0.5]
        assert card.cost_mean == pytest.approx(0.5)
        d = card.to_dict()
        assert d["cost"]["samples"] == 1
        assert d["cost"]["tasks_total"] == 3

    def test_no_budget_at_all_cost_mean_none(self):
        card = build_scorecard(
            [GoldenTask(id="a", prompt="p")],
            [RunRecord(task_id="a", tokens_used=10)],
        )
        assert card.cost_mean is None
        assert card.to_dict()["cost"]["mean"] is None

    def test_duration_passthrough(self):
        t = GoldenTask(id="a", prompt="p")
        s = score_run(t, RunRecord(task_id="a", duration_ms=1234))
        assert s.duration_ms == 1234
        assert s.total == 1.0, "延迟是观测值，不进 total"

    def test_duration_mean_ignores_missing(self):
        tasks = [GoldenTask(id="a", prompt="p"), GoldenTask(id="b", prompt="p")]
        runs = [
            RunRecord(task_id="a", duration_ms=1000),
            RunRecord(task_id="b", duration_ms=0),
        ]
        card = build_scorecard(tasks, runs)
        assert card.duration_ms_mean == 1000.0
        assert card.to_dict()["duration_ms_mean"] == 1000.0


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
