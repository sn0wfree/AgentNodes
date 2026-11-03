"""W-split: GoalStore pure helpers."""
from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from pathlib import Path

from strategy_research.foundation.storage.sqlite import resolve_db_path

_DB_PATH_ENV = "QUANTNODES_RESEARCH_GOAL_DB_PATH"


def _default_db_path() -> Path:
    """Return the configured goal ledger database path.

    Order of resolution:
        1. ``QUANTNODES_RESEARCH_GOAL_DB_PATH`` environment variable
        2. ``~/.quantnodes-research/goals.db`` (default)
    """
    return resolve_db_path("goals.db", _DB_PATH_ENV)


def _to_json_dict(value: object) -> dict:
    data = asdict(value)
    for key, item in list(data.items()):
        if isinstance(item, Enum):
            data[key] = item.value
    return data


def _safe_artifact_path(raw: str | None) -> Path | None:
    """Best-effort resolve an artifact path. Returns None on failure."""
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _safe_run_id(raw: str | None) -> Path | None:
    """Resolve a bare run id (single segment) to a path. Returns None on failure."""
    if not raw or not raw.strip():
        return None
    candidate = Path(raw)
    if (
        candidate.is_absolute()
        or len(candidate.parts) != 1
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        return None
    return candidate.resolve()


def _label_similarity(a: str, b: str) -> float:
    """Simple Jaccard similarity over character n-grams (trigrams)."""
    if not a or not b:
        return 0.0
    a_low, b_low = a.lower(), b.lower()
    if a_low == b_low:
        return 1.0
    def _trigrams(s: str) -> set[str]:
        return {s[i:i+3] for i in range(len(s) - 2)}
    tri_a, tri_b = _trigrams(a_low), _trigrams(b_low)
    if not tri_a or not tri_b:
        return 0.0
    return len(tri_a & tri_b) / len(tri_a | tri_b)


