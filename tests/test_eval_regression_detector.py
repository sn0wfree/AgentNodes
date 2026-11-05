"""Phase 0 acceptance — trajectory drift detector cases.

Productized from the Phase 0 acceptance run so the detector itself
cannot silently regress (docs/archive/undated/phase0-trajectory-eval-design.md §6).
The real invocation path is exercised end-to-end via subprocess.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("scripts/check_eval_regression.py")
REPO = Path(__file__).resolve().parents[1]


def _run(card: dict, baseline: dict, tmp_path: Path) -> int:
    (tmp_path / "card.json").write_text(json.dumps(card), encoding="utf-8")
    (tmp_path / "base.json").write_text(json.dumps(baseline), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--scorecard", str(tmp_path / "card.json"),
         "--baseline", str(tmp_path / "base.json")],
        capture_output=True, text=True, cwd=REPO,
    )
    return r.returncode


def _card(mean: float, tasks: list, traj: dict) -> dict:
    return {
        "n": len(tasks), "mean": mean, "pass_rate@0.7": 0.9,
        "tasks": tasks, "trajectory": traj,
    }


BASE_TASKS = [
    {"id": "a", "total": 0.9, "tool_sequence": ["read", "backtest"],
     "iterations": 2, "tool_calls_made": 2, "finished_reason": "stop"},
    {"id": "b", "total": 0.9, "tool_sequence": ["read", "write", "backtest"],
     "iterations": 3, "tool_calls_made": 3, "finished_reason": "stop"},
    {"id": "c", "total": 0.9, "tool_sequence": ["read"],
     "iterations": 1, "tool_calls_made": 1, "finished_reason": "stop"},
    {"id": "d", "total": 0.9, "tool_sequence": ["read", "backtest"],
     "iterations": 2, "tool_calls_made": 2, "finished_reason": "stop"},
]
BASE_TRAJ = {"steps_mean": 2.0, "steps_p90": 3, "tool_calls_mean": 2.0,
             "terminal": {"stop": 4}}
BASE = _card(0.9, BASE_TASKS, BASE_TRAJ)


def test_identical_trajectory_passes(tmp_path):
    assert _run(BASE, BASE, tmp_path) == 0


def test_t1_steps_mean_drift_caught(tmp_path):
    tasks = [dict(t, iterations=t["iterations"] + 3) for t in BASE_TASKS]
    traj = {"steps_mean": 5.0, "steps_p90": 6, "tool_calls_mean": 2.0,
            "terminal": {"stop": 4}}
    assert _run(_card(0.9, tasks, traj), BASE, tmp_path) == 1


def test_t2_tool_sequence_drift_caught(tmp_path):
    tasks = [
        {"id": "a", "total": 0.9, "tool_sequence": ["shell", "shell", "write"],
         "iterations": 3, "tool_calls_made": 3, "finished_reason": "stop"},
        {"id": "b", "total": 0.9, "tool_sequence": ["skill", "web_search"],
         "iterations": 2, "tool_calls_made": 2, "finished_reason": "stop"},
        {"id": "c", "total": 0.9, "tool_sequence": ["list_history", "git_diff"],
         "iterations": 2, "tool_calls_made": 2, "finished_reason": "stop"},
        {"id": "d", "total": 0.9, "tool_sequence": ["read", "backtest"],
         "iterations": 2, "tool_calls_made": 2, "finished_reason": "stop"},
    ]
    traj = {"steps_mean": 2.25, "steps_p90": 3, "tool_calls_mean": 2.25,
            "terminal": {"stop": 4}}
    assert _run(_card(0.9, tasks, traj), BASE, tmp_path) == 1


def test_t3_new_terminal_caught(tmp_path):
    tasks = [dict(t, finished_reason="budget" if t["id"] == "d" else t["finished_reason"])
             for t in BASE_TASKS]
    traj = {"steps_mean": 2.0, "steps_p90": 3, "tool_calls_mean": 2.0,
            "terminal": {"stop": 3, "budget": 1}}
    assert _run(_card(0.9, tasks, traj), BASE, tmp_path) == 1


def test_natural_jitter_passes(tmp_path):
    tasks = [dict(t, iterations=t["iterations"] + (1 if i == 0 else 0))
             for i, t in enumerate(BASE_TASKS)]
    traj = {"steps_mean": 2.25, "steps_p90": 3, "tool_calls_mean": 2.0,
            "terminal": {"stop": 4}}
    assert _run(_card(0.9, tasks, traj), BASE, tmp_path) == 0
