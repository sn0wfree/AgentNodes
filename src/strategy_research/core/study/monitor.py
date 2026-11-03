"""Monitor phase — post-completion periodic re-check + auto repair.

Extracted from runner.py to reduce file size and improve modularity.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from ..observability.logging_setup import dual_log

logger = logging.getLogger(__name__)


# 统一双写日志（docs/logging-unified-entry-design-20261102.md §4.2）。
_dlog = dual_log


async def monitor_phase(runner: Any) -> str:
    """Post-completion monitoring: periodic re-check + auto repair.

    Every ``monitor_interval_seconds`` the last keep run is re-backtested
    (no LLM) and compared to ``metric_targets`` only.
    """
    from .models import StudyStatus
    from .runner import ShutdownReason

    study = runner._get_study()
    sid = study.study_id
    session = study.session_id
    interval = study.monitor_interval_seconds or 0
    runner.study_store.update_execution_status(sid, StudyStatus.MONITORING)
    runner._emit(session, "study_monitoring_started", {
        "study_id": sid, "interval_seconds": interval,
    })
    _dlog("monitor", "monitoring started study=%s interval=%ss", sid, interval)

    while True:
        # Check cancelled
        if runner.control.cancelled:
            live_status = runner._current_db_status()
            if live_status == StudyStatus.ARCHIVED:
                runner._emit(session, "study_cancelled", {
                    "study_id": sid,
                    "note": f"preserved live status={live_status.value}",
                })
                return ShutdownReason.CANCELLED
            runner._mark_terminal(StudyStatus.CANCELLED, reason=ShutdownReason.CANCELLED)
            runner._emit(session, "study_cancelled", {"study_id": sid})
            return ShutdownReason.CANCELLED

        # Check paused
        if runner.control.paused:
            runner.study_store.update_execution_status(sid, StudyStatus.PAUSED)
            runner._emit(session, "study_paused", {
                "study_id": sid, "round": runner._get_study().current_round,
            })
            await runner._wait_until_resumed()
            if runner.control.cancelled:
                # Cancelled while paused (H3): let the loop top finalize
                # CANCELLED instead of flipping back to MONITORING.
                continue
            runner.study_store.update_execution_status(sid, StudyStatus.MONITORING)
            runner._emit(session, "study_resumed", {
                "study_id": sid, "round": runner._get_study().current_round,
            })

        # Sleep and check
        await _monitor_sleep(interval)
        try:
            check = await asyncio.to_thread(runner._run_monitor_check)
        except Exception as exc:
            logger.warning("monitor check %s failed: %s", sid, exc)
            runner._emit(session, "study_monitor_check_failed", {
                "study_id": sid, "error": str(exc),
            })
            continue

        drift = not check["meets_targets"]
        # update_monitor_check returns the post-write record — its
        # monitor_drift_count is authoritative. Reading the (cached)
        # study snapshot and adding +1 double-counted whenever the
        # cache had already picked up the incremented value.
        updated = runner.study_store.update_monitor_check(
            sid, last_check_at=check["now_iso"], drift=drift,
        )
        drift_count = (
            updated.monitor_drift_count
            if updated is not None
            else runner._get_study().monitor_drift_count
        )
        runner.study_store.update_last_metrics(
            sid, check["metrics"] or {}, check.get("verdict", "monitor"),
        )
        runner._emit(session, "study_monitor_check", {
            "study_id": sid,
            "metrics": check["metrics"],
            "meets_targets": check["meets_targets"],
            "drift": drift,
            "drift_count": drift_count,
        })
        if not drift:
            continue

        # Drift → needs_refresh + auto repair
        drift_reason = check.get("reason") or "monitor drift"
        runner.study_store.update_execution_status(
            sid, StudyStatus.NEEDS_REFRESH,
            last_error=f"monitor drift: {drift_reason}",
        )
        runner._emit(session, "study_drift_detected", {
            "study_id": sid, "metrics": check["metrics"],
            "reason": drift_reason,
        })
        if await _monitor_repair_rounds(runner):
            runner.study_store.update_execution_status(sid, StudyStatus.MONITORING)
            runner._emit(session, "study_monitoring_started", {
                "study_id": sid, "interval_seconds": interval,
                "repaired": True,
            })
            continue
        _dlog("monitor", "repair exhausted, staying needs_refresh study=%s", sid)
        return ShutdownReason.NEEDS_REFRESH


async def _monitor_repair_rounds(runner: Any) -> bool:
    """Up to 3 full repair rounds; True when an E2 pass restores the study."""
    from .budget import account_round_budget, budget_exceeded, budget_summary, maybe_load_previous_summary
    from .models import StudyStatus

    study = runner._get_study()
    sid = study.study_id
    session = study.session_id
    path = Path(study.workspace_path)
    from strategy_research.core.study import state_store as ss
    state = ss.load(path, sid)
    base_round = state.last_completed_round or study.current_round or 0

    for attempt in range(3):
        if budget_exceeded(study, runner._total_used_time, runner._total_used_turns):
            runner.study_store.update_execution_status(
                sid, StudyStatus.NEEDS_REFRESH,
                last_error=f"budget_limited: {budget_summary(runner._total_used_turns, runner._total_used_time)}",
            )
            runner._emit(session, "study_budget_limited", {
                "study_id": sid, "used": budget_summary(runner._total_used_turns, runner._total_used_time),
            })
            return False

        round_num = base_round + attempt + 1
        _dlog("monitor", "repair round %d study=%s", round_num, sid)
        runner._round_start_clock = time.perf_counter()
        previous_summary = maybe_load_previous_summary(study)
        result = await asyncio.to_thread(
            runner._run_one_round, round_num, previous_summary, None,
        )
        runner._total_used_time, runner._total_used_turns = account_round_budget(
            runner._total_used_time, runner._total_used_turns,
            runner._round_start_clock, result,
        )
        if result.get("paused_for_approval"):
            # A repair round paused on the HITL gate is neither passed
            # nor failed: burn no further attempts and leave the study
            # in PAUSED (the previous behavior counted the pause as a
            # failed repair 3× then dropped the checkpoint into
            # NEEDS_REFRESH).
            logger.info(
                "monitor: repair round %d paused for approval study=%s",
                round_num, sid,
            )
            runner._emit(session, "study_paused", {
                "study_id": sid, "round": round_num,
                "reason": "hitl_approval_repair",
                "interrupt_id": result.get("interrupt_id"),
                "hypothesis": result.get("hypothesis"),
            })
            runner.study_store.update_execution_status(sid, StudyStatus.PAUSED)
            return False
        if result.get("aborted"):
            # Researcher time/turns still count against the budget (M2).
            runner._account_round_budget(result)
            continue
        metrics = result.get("metrics", {})
        verdict = result.get("verdict", "discard")
        # Keep attribution honest: classify_attribution in the next
        # repair round diffs against _prev_passed, which the main loop
        # refreshes after every round — the repair loop never did.
        runner._prev_passed = result.get("passed_now", set()) or set()
        runner.study_store.update_round_heartbeat(sid, round_num)
        runner.study_store.update_last_metrics(sid, metrics, verdict)
        runner._emit(session, "study_round", {
            "study_id": sid, "round": round_num,
            "run": result.get("run_name", ""),
            "metrics": metrics, "verdict": verdict,
        })
        if result.get("e2_passed"):
            _dlog("monitor", "repair round %d passed, back to MONITORING", round_num)
            return True
    return False


async def _monitor_sleep(interval: float) -> None:
    """Sleep between monitor checks."""
    if interval > 0:
        await asyncio.sleep(interval)
