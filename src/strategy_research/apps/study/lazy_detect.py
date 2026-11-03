"""W-split: lazy-behavior detection for research agents."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)
def detect_lazy_behavior(
    agent_name: str,
    current_output: dict[str, Any],
    history: list[dict[str, Any]],
    threshold: int = 3,
) -> dict[str, Any]:
    """检测 Agent 是否在偷懒。

    Args:
        agent_name: Agent 名称
        current_output: 当前输出
        history: 历史记录 (最近 N 轮)
        threshold: 重复阈值 (默认 3)

    Returns:
        {"lazy_score": float, "issues": list[str], "is_lazy": bool}
    """
    lazy_score = 0.0
    issues = []

    if not history:
        return {"lazy_score": 0.0, "issues": [], "is_lazy": False}

    recent_outputs = [h.get("output", {}) for h in history[-threshold:]]

    detector = _LAZY_DETECTORS.get(agent_name)
    if detector is not None:
        lazy_score, issues = detector(current_output, recent_outputs, threshold)

    return {
        "lazy_score": min(lazy_score, 1.0),
        "issues": issues,
        "is_lazy": lazy_score >= 0.3,
    }


def _detect_researcher_lazy(
    current_output: dict[str, Any], recent_outputs: list, threshold: int
) -> tuple[float, list[str]]:
    """Laziness checks for the researcher agent."""
    lazy_score = 0.0
    issues: list[str] = []
    recent_hypotheses = [h.get("hypothesis") for h in recent_outputs if h.get("hypothesis")]
    if current_output.get("hypothesis") in recent_hypotheses:
        lazy_score += 0.5
        issues.append("hypothesis 与上轮相同")
    recent_actions = [h.get("action") for h in recent_outputs if h.get("action")]
    if current_output.get("action") in recent_actions:
        lazy_score += 0.3
        issues.append("action 与上轮相同")
    return lazy_score, issues


def _detect_factor_analyst_lazy(
    current_output: dict[str, Any], recent_outputs: list, threshold: int
) -> tuple[float, list[str]]:
    """Laziness checks for the factor_analyst agent."""
    lazy_score = 0.0
    issues: list[str] = []
    recent_candidates = [h.get("candidates", []) for h in recent_outputs]
    if all(len(c) == 0 for c in recent_candidates) and len(recent_candidates) >= threshold:
        lazy_score += 0.3
        issues.append(f"连续 {threshold} 轮无候选因子")
    recent_rejected_names = [
        [r.get("factor_name") for r in h.get("rejected", [])]
        for h in recent_outputs
    ]
    current_rejected_names = [r.get("factor_name") for r in current_output.get("rejected", [])]
    if recent_rejected_names and current_rejected_names:
        if all(set(current_rejected_names) == set(r) for r in recent_rejected_names):
            lazy_score += 0.2
            issues.append("rejected 因子与上轮相同")
    return lazy_score, issues


def _detect_strategist_lazy(
    current_output: dict[str, Any], recent_outputs: list, threshold: int
) -> tuple[float, list[str]]:
    """Laziness checks for the strategist agent."""
    lazy_score = 0.0
    issues: list[str] = []
    recent_changes = [h.get("changes", []) for h in recent_outputs]
    if all(len(c) == 0 for c in recent_changes) and len(recent_changes) >= threshold:
        lazy_score += 0.4
        issues.append(f"连续 {threshold} 轮无 changes")
    recent_actions = [h.get("action") for h in recent_outputs if h.get("action")]
    if recent_actions and all(a == recent_actions[0] for a in recent_actions):
        lazy_score += 0.3
        issues.append("action 连续相同")
    return lazy_score, issues


def _detect_risk_controller_lazy(
    current_output: dict[str, Any], recent_outputs: list, threshold: int
) -> tuple[float, list[str]]:
    """Laziness checks for the risk_controller agent."""
    lazy_score = 0.0
    issues: list[str] = []
    recent_ratings = [h.get("risk_rating") for h in recent_outputs if h.get("risk_rating")]
    if recent_ratings and all(r == recent_ratings[0] for r in recent_ratings):
        lazy_score += 0.2
        issues.append("risk_rating 连续相同")
    return lazy_score, issues


def _detect_anti_overfit_lazy(
    current_output: dict[str, Any], recent_outputs: list, threshold: int
) -> tuple[float, list[str]]:
    """Laziness checks for the anti_overfit_analyst agent."""
    lazy_score = 0.0
    issues: list[str] = []
    recent_verdicts = [h.get("verdict") for h in recent_outputs if h.get("verdict")]
    if recent_verdicts and all(v == "discard" for v in recent_verdicts):
        lazy_score += 0.4
        issues.append(f"连续 {len(recent_verdicts)} 轮 verdict=discard")
    recent_overfit = [h.get("overfit_passed") for h in recent_outputs if "overfit_passed" in h]
    if recent_overfit and all(not v for v in recent_overfit):
        lazy_score += 0.3
        issues.append("overfit_passed 连续 false")
    return lazy_score, issues


_LAZY_DETECTORS = {
    "researcher": _detect_researcher_lazy,
    "factor_analyst": _detect_factor_analyst_lazy,
    "strategist": _detect_strategist_lazy,
    "risk_controller": _detect_risk_controller_lazy,
    "anti_overfit_analyst": _detect_anti_overfit_lazy,
}


