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
        """E1: keep rounds append goal evidence + emit progress (design §16.3).

        goal 台账已随 apps/goal 移除（2026-11-04 批次 2）：证据写入与
        goal 快照发射变为 no-op。keep 轮的指标仍在 run 产物与
        ``study_metrics`` 事件里，未丢失。
        """
        sid = self._get_study().study_id
        session = self._get_study().session_id
        # 保留一个可见的进度事件（无 goal 准则可比，percent 恒 100）。
        self._emit(session, "study_progress", {
            "study_id": sid, "covered": 0, "total": 0, "percent": 100,
        })
