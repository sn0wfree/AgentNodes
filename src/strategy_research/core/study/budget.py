"""Budget and goal completion utilities.

Extracted from runner.py to reduce file size and improve testability.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def account_round_budget(
    total_used_time: float,
    total_used_turns: int,
    round_start_clock: float | None,
    exec_result: dict,
) -> tuple[float, int]:
    """Update budget counters after a round. Returns (time, turns)."""
    if round_start_clock is not None:
        total_used_time += time.perf_counter() - round_start_clock
    outs = exec_result.get("agent_outputs") or {}
    total_used_turns += sum(
        1 for v in outs.values()
        if v and not (isinstance(v, dict) and v.get("error"))
    )
    return total_used_time, total_used_turns


def budget_exceeded(
    study: Any,
    total_used_time: float,
    total_used_turns: int,
) -> bool:
    """Check if budget limits are exceeded."""
    if study.budget_time_seconds is not None and total_used_time >= study.budget_time_seconds:
        return True
    if study.budget_turn is not None and total_used_turns >= study.budget_turn:
        return True
    return False


def budget_summary(total_used_turns: int, total_used_time: float) -> str:
    """Human-readable budget summary."""
    return f"turns_used={total_used_turns}, time_used={total_used_time:.1f}s"


def complete_goal(
    goal_store: Any,
    study: Any,
    exec_result: dict,
) -> None:
    """Mark goal as complete with evidence when targets are met."""
    if not study.goal_id:
        return
    try:
        from strategy_research.core.goal import EvidenceInput
        metrics = exec_result.get("metrics", {})
        run_name = exec_result.get("run_name", "")
        existing = goal_store.list_evidence(study.goal_id)
        seen = {ev.criterion_id for ev in existing if ev.criterion_id}
        criteria = goal_store.list_criteria(study.goal_id)
        for c in criteria:
            if not c.required or c.criterion_id in seen:
                continue
            goal_store.append_evidence(
                session_id=study.session_id,
                goal_id=study.goal_id,
                expected_goal_id=study.goal_id,
                evidence=EvidenceInput(
                    text=f"Study 达标自动覆盖 — {run_name}: Calmar={metrics.get('calmar')} Sharpe={metrics.get('sharpe')} MaxDD={metrics.get('max_dd')}",
                    criterion_id=c.criterion_id, evidence_type="acceptance",
                    run_id=run_name, source_provider="study", source_type="metric_targets_met",
                ),
            )
        goal_store.complete_lite(
            session_id=study.session_id, goal_id=study.goal_id,
            expected_goal_id=study.goal_id,
            recap=f"研究达标 — Calmar={metrics.get('calmar')}, Sharpe={metrics.get('sharpe')}, MaxDD={metrics.get('max_dd')}",
        )
    except Exception as exc:
        logger.exception("study %s goal completion failed: %s", study.study_id, exc)


def round_cooldown(study: Any) -> float:
    """Calculate cooldown seconds for the next round."""
    from strategy_research.apps.autoresearch import get_cooldown_seconds
    return get_cooldown_seconds(
        study.cooldown_base * 2, study.cooldown_jitter * 2, study.min_cooldown * 2,
    )


def maybe_load_previous_summary(study: Any) -> dict | None:
    """Load the most recent round summary, or None."""
    try:
        runs_dir = Path(study.workspace_path) / "strategies" / study.strategy_name / "runs"
        if not runs_dir.exists():
            return None
        from strategy_research.apps.autoresearch import load_run_summary
        nums: list[int] = []
        for d in runs_dir.iterdir():
            if d.is_dir() and d.name.startswith("run_"):
                try:
                    nums.append(int(d.name.split("_")[1]))
                except (ValueError, IndexError):
                    pass
        if not nums:
            return None
        return load_run_summary(runs_dir / f"run_{max(nums):04d}")
    except Exception:
        return None
