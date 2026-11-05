"""Research hypothesis subsystem (P3-b).

File-backed registry of durable research hypotheses. Each hypothesis links
its thesis to backtest artifacts (run_cards) and tracks lifecycle status
across exploring → testing → validated / rejected / monitoring.

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

from .auto_create import HypothesisAutoCreator
from .registry import (
    HYPOTHESIS_STATUSES,
    VALID_TRANSITIONS,
    Hypothesis,
    HypothesisRegistry,
    default_hypotheses_path,
)
from .store import HypothesisStore, default_db_path
from .validator import DEFAULT_CRITERIA, ValidationResult, validate_hypothesis

__all__ = [
    "DEFAULT_CRITERIA",
    "HYPOTHESIS_STATUSES",
    "VALID_TRANSITIONS",
    "Hypothesis",
    "HypothesisAutoCreator",
    "HypothesisRegistry",
    "HypothesisStore",
    "ValidationResult",
    "default_db_path",
    "default_hypotheses_path",
    "validate_hypothesis",
]
