"""evals/run_evals.py 单元测试——此前 0% 覆盖（A7 金任务评分入口）。

走真实的 golden_tasks.jsonl 资产（模拟运行，无 LLM 调用）。
"""
from __future__ import annotations

import json

import pytest

from agentnodes.evals import run_evals
from agentnodes.evals.scorecard import load_golden_tasks


@pytest.fixture(scope="module")
def golden_path() -> str:
    from pathlib import Path

    p = Path(run_evals.__file__).parent / "golden_tasks.jsonl"
    assert p.exists(), "golden_tasks.jsonl 资产缺失"
    return str(p)


class TestSimulateRuns:
    def test_yields_one_record_per_task(self, golden_path):
        tasks = load_golden_tasks(golden_path)
        assert tasks, "金任务资产为空"
        runs = list(run_evals._simulate_runs(tasks))
        assert len(runs) == len(tasks)
        for t, r in zip(tasks, runs):
            assert r.task_id == t.id
            assert r.success is True
            assert r.finished_reason == "stop"

    def test_tools_used_falls_back_to_read(self, golden_path):
        """expected_tools 为空 → 回退 ["read"]。"""
        tasks = load_golden_tasks(golden_path)
        runs = {r.task_id: r for r in run_evals._simulate_runs(tasks)}
        empty = [t for t in tasks if not t.expected_tools]
        for t in empty:
            assert runs[t.id].tools_used == ["read"]

    def test_budget_capped_at_500(self, golden_path):
        tasks = load_golden_tasks(golden_path)
        runs = list(run_evals._simulate_runs(tasks))
        assert all(r.tokens_used <= 500 for r in runs)


class TestMain:
    def test_human_output(self, golden_path, capsys):
        rc = run_evals.main(["--tasks", golden_path])
        assert rc == 0
        out = capsys.readouterr().out
        assert "tasks=" in out and "mean=" in out and "pass@0.7=" in out

    def test_json_output_is_valid_scorecard(self, golden_path, capsys):
        rc = run_evals.main(["--tasks", golden_path, "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["n"] > 0
        assert 0.0 <= payload["mean"] <= 1.0
        assert "pass_rate@0.7" in payload
        assert len(payload["tasks"]) == payload["n"]

    def test_missing_tasks_file_raises(self, tmp_path):
        with pytest.raises(Exception):
            run_evals.main(["--tasks", str(tmp_path / "nope.jsonl")])
