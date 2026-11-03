"""AEGIS utilities — novelty, regression, journal, scoreboard.

Extracted from runner.py to reduce file size and improve testability.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def check_novelty(
    goal_store: Any,
    goal_id: str | None,
    hypothesis: str,
    predicted_affected: list[str],
) -> tuple[bool, str | None]:
    """Check hypothesis novelty against goal journal."""
    if not goal_id:
        return True, None
    return goal_store.check_novelty(goal_id, hypothesis, [], predicted_affected)


def check_regression(
    goal_store: Any,
    goal_id: str | None,
    attribution: dict[str, str],
) -> tuple[bool, list[str]]:
    """Check for metric regression against goal baseline."""
    if not goal_id:
        return True, []
    return goal_store.check_regression(goal_id, attribution)


def archive_rejected(
    goal_store: Any,
    goal_id: str | None,
    round_num: int,
    hypothesis: str,
    reason: str,
    detail: str,
) -> None:
    """Archive a rejected hypothesis in the goal journal."""
    if not goal_id:
        return
    goal_store.archive_rejected_edit(goal_id, round_num, hypothesis, reason, detail)


def verdict_reason(eval_result: dict, strategist_output: Any) -> str:
    """Extract the verdict reason from decision/attribution output."""
    decision = eval_result.get("decision")
    if decision is not None:
        reason = getattr(decision, "reason", None) or \
            (decision.get("reason", "") if isinstance(decision, dict) else "")
        if reason:
            return str(reason)
    aoa = eval_result.get("aoa_llm_verdict")
    if isinstance(aoa, dict) and aoa.get("decision") == "discard":
        return str(aoa.get("reason", "anti-overfit rejection"))
    return ""


def build_journal_context(goal_store: Any, goal_id: str | None, current_round: int) -> str:
    """Build journal context string for the current round."""
    if not goal_id:
        return ""
    return goal_store.build_journal_context(goal_id, current_round)
