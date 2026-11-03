"""W-split: Knowledge collection and keep-evidence for AutoresearchRunner."""
from __future__ import annotations

from typing import Any

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class RunnerKnowledgeMixin:
# ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _emit: Any
    _get_study: Any
    _goal_store: Any
    _loop_strategy: Any

    def _collect_knowledge(self, topics: list[str]) -> int:
        """v2: run the collector agent and append to knowledge.md.

        Returns the number of appended entries (0 on stub/failure).
        """
        from strategy_research.kernel.agent.role_factory import (
            run_agent_via_llm,
            should_use_real_llm,
        )
        from strategy_research.apps.study.method import review_loop as rl
        from strategy_research.apps.study import state_store as ss

        sid = self._get_study().study_id
        path = Path(self._get_study().workspace_path).resolve()
        root = ss.study_root(path, sid)
        knowledge_path = root / "knowledge.md"
        knowledge_text = (
            knowledge_path.read_text(encoding="utf-8")
            if knowledge_path.exists() else ""
        )
        try:
            if self._get_study().behavior is None and should_use_real_llm():
                raw_entries = run_agent_via_llm(
                    role="study_collector",
                    workspace_path=path,
                    strategy_name=self._get_study().strategy_name,
                    task=(
                        f"objective: {self._get_study().objective}\n"
                        f"topics: {json.dumps(topics, ensure_ascii=False)}\n"
                        f"existing knowledge:\n{knowledge_text[-2000:]}\n"
                    ),
                    max_iterations=3,
                    loop_strategy=self._loop_strategy,
                )
                entries = rl.parse_review_output(raw_entries)
                if isinstance(entries, dict):
                    entries = [entries]
            else:
                entries = []
            collected = rl.append_knowledge(
                knowledge_path, entries, self._get_study().objective,
            )
            state = ss.load(path, sid)
            state.last_collect_round = state.last_completed_round or 0
            ss.save(path, sid, state)
            if collected:
                self._emit(self._get_study().session_id, "study_knowledge_update", {
                    "study_id": sid, "entries_added": collected,
                })
            return collected
        except Exception as exc:  # noqa: BLE001
            logger.warning("collector failed: %s", exc)
            return 0

    def _record_keep_evidence(self, round_num: int, run_name: str, metrics: dict) -> None:
        """E1: keep rounds append goal evidence + emit progress (design §16.3)."""
        sid = self._get_study().study_id
        session = self._get_study().session_id
        try:
            from strategy_research.apps.goal import EvidenceInput
            ev = self._goal_store.append_evidence(
                session_id=session,
                goal_id=self._get_study().goal_id,
                expected_goal_id=self._get_study().goal_id,
                evidence=EvidenceInput(
                    text=(
                        f"Study round {round_num} keep — {run_name}: "
                        f"Calmar={metrics.get('calmar')} Sharpe={metrics.get('sharpe')} "
                        f"MaxDD={metrics.get('max_dd')}"
                    ),
                    evidence_type="study_keep", run_id=run_name,
                    source_provider="study", source_type="round_keep",
                ),
            )
            self._emit(session, "study_evidence", {
                "study_id": sid, "evidence_id": ev.evidence_id,
                "run": run_name,
            })
            criteria = self._goal_store.list_criteria(self._get_study().goal_id)
            evidence = self._goal_store.list_evidence(self._get_study().goal_id)
            seen = {e.criterion_id for e in evidence if e.criterion_id}
            covered = sum(1 for c in criteria if c.criterion_id in seen)
            total = len(criteria)
            self._emit(session, "study_progress", {
                "study_id": sid, "covered": covered, "total": total,
                "percent": round(covered / total * 100) if total else 100,
            })
            # ── SSE: study_goal_snapshot (real-time goal state) ──
            snap = self._goal_store.get_goal_snapshot(
                self._get_study().goal_id,
            )
            if snap:
                g = snap.get("goal", {}) or {}
                self._emit(session, "study_goal_snapshot", {
                    "study_id": sid,
                    "goal_snapshot": {
                        "goal_id": g.get("goal_id"),
                        "goal_status": g.get("status"),
                        "objective": g.get("objective"),
                        "progress_percent": g.get("progress_percent", 0),
                        "evidence_count": snap.get("evidence_count", 0),
                        "criteria": [
                            {"criterion_id": c.get("criterion_id"), "text": c.get("text"),
                             "status": c.get("status"), "required": c.get("required", True)}
                            for c in snap.get("criteria", []) or []
                        ],
                    },
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("study %s keep evidence failed: %s", sid, exc)
