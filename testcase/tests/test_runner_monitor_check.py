"""Tests for _run_monitor_check (P0-3b fix — method was missing).

The monitor phase calls ``runner._run_monitor_check`` to re-backtest
the last keep run and check metric_targets. Pre-fix this method
didn't exist → AttributeError on every MONITORING study.

Post-fix: the method returns a dict with meets_targets, metrics,
now_iso, verdict. These tests verify the contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from strategy_research.apps.study import state_store as ss
from strategy_research.apps.study.runner import AutoresearchRunner


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    return tmp_path


def _make_runner(ws, *, metrics, targets):
    """Build a real AutoresearchRunner instance with state.best_metrics set."""
    sid = "study-mt-1"
    ss.study_root(ws, sid).mkdir(parents=True, exist_ok=True)
    state = ss.load(ws, sid)
    state.best_metrics = metrics
    state.last_keep_run_dir = "baseline"
    ss.save(ws, sid, state)

    # Build a minimal runner via __new__ to bypass __init__ (which expects
    # a full scheduler / container setup we don't need for this method).
    runner = AutoresearchRunner.__new__(AutoresearchRunner)
    runner.study = None  # _get_study() falls back to self.study; we patch below

    # Patch _get_study to return our study record
    study_obj = type(
        "S",
        (),
        {
            "study_id": sid,
            "workspace_path": str(ws),
            "metric_targets": targets,
        },
    )()
    runner._get_study = lambda: study_obj
    return runner, sid


# ── Tests ───────────────────────────────────────────────────


def test_returns_required_keys(env):
    """Result must have the four keys the monitor loop reads."""
    runner, _ = _make_runner(
        env,
        metrics={"calmar": 0.7, "sharpe": 0.4, "max_dd": -0.1},
        targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
    )

    result = runner._run_monitor_check()

    assert "meets_targets" in result
    assert "metrics" in result
    assert "now_iso" in result
    assert "verdict" in result
    # verdict is always "monitor"
    assert result["verdict"] == "monitor"


def test_meets_targets_true_when_all_passes(env):
    runner, _ = _make_runner(
        env,
        metrics={"calmar": 0.7, "sharpe": 0.4, "max_dd": -0.05},
        targets=[
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
            {"name": "max_dd", "op": ">=", "value": -0.15},
        ],
    )

    result = runner._run_monitor_check()

    assert result["meets_targets"] is True
    assert result["metrics"]["calmar"] == 0.7


def test_meets_targets_false_when_one_fails(env):
    """One metric failing the target → meets_targets=False."""
    runner, _ = _make_runner(
        env,
        metrics={"calmar": 0.3, "sharpe": 0.4, "max_dd": -0.05},  # calmar < 0.5
        targets=[
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
        ],
    )

    result = runner._run_monitor_check()

    assert result["meets_targets"] is False


def test_meets_targets_false_when_no_targets_configured(env):
    """When metric_targets is empty, meets_targets is False."""
    runner, _ = _make_runner(
        env,
        metrics={"calmar": 0.9},
        targets=[],  # empty
    )

    result = runner._run_monitor_check()

    assert result["meets_targets"] is False


def test_now_iso_is_recent_iso8601(env):
    """now_iso must be a parseable ISO-8601 string in the recent past."""
    runner, _ = _make_runner(
        env,
        metrics={"calmar": 0.5},
        targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
    )

    result = runner._run_monitor_check()

    parsed = datetime.fromisoformat(result["now_iso"])
    now = datetime.now(parsed.tzinfo)
    delta = abs((now - parsed).total_seconds())
    assert delta < 3600


def test_metrics_echoes_best_metrics(env):
    """The metrics key in the result mirrors best_metrics from state."""
    runner, _ = _make_runner(
        env,
        metrics={"calmar": 0.6, "sharpe": 0.5, "max_dd": -0.08},
        targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
    )

    result = runner._run_monitor_check()

    assert result["metrics"] == {"calmar": 0.6, "sharpe": 0.5, "max_dd": -0.08}