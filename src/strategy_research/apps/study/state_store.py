"""Study v2 state.json — the single source of truth for task-level state.

Authority (design §3.2/A4): ``state.json`` holds last_completed_round /
best_metrics (keep-only) / last_keep_run_dir / continuous_deviation /
budget usage. The DB mirrors only execution_status + heartbeat for lists
and recovery scanning. Write order: state.json first, DB second.

All writes are atomic (tmp file + rename) so a crash never leaves a
half-written state.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()

_STATE_VERSION = 1


@dataclass
class StudyState:
    version: int = _STATE_VERSION
    last_completed_round: int = 0
    best_metrics: dict[str, Any] = field(default_factory=dict)
    last_keep_run_dir: str | None = None   # "rounds/round_NNNN/run_XXXX"
    baseline_best: dict[str, Any] = field(default_factory=dict)
    continuous_deviation: int = 0
    discard_streak: int = 0
    budget_used_turns: int = 0
    budget_used_time_s: float = 0.0
    last_collect_round: int = 0            # 距上次信息收集（§11）
    last_review: dict[str, Any] = field(default_factory=dict)
    review_fail_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def study_root(workspace_path: Path, study_id: str) -> Path:
    return Path(workspace_path) / "study" / study_id


def state_path(workspace_path: Path, study_id: str) -> Path:
    return study_root(workspace_path, study_id) / "state.json"


def _default_state() -> StudyState:
    return StudyState()


def load(workspace_path: Path, study_id: str) -> StudyState:
    """Load state.json; missing/corrupt → defaults (recovery fallback,
    design §3.2: back off to DB current_round / 0)."""
    p = state_path(workspace_path, study_id)
    with _LOCK:
        if not p.exists():
            return _default_state()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            st = _default_state()
            for key in asdict(st):
                if key in raw:
                    setattr(st, key, raw[key])
            return st
        except (ValueError, OSError, TypeError):
            return _default_state()


def save(workspace_path: Path, study_id: str, state: StudyState) -> None:
    """Atomic write (tmp + rename)."""
    p = state_path(workspace_path, study_id)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(state.as_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(p)


def init(
    workspace_path: Path,
    study_id: str,
    *,
    baseline_best: dict[str, Any] | None = None,
) -> StudyState:
    """Create the initial state file (design §6.2 step 7)."""
    st = _default_state()
    if baseline_best:
        st.baseline_best = dict(baseline_best)
    save(workspace_path, study_id, st)
    return st
