"""W-split: AEGIS gates for AutoresearchRunner (novelty / journal / regression)."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from .method import aegis as _aegis


class RunnerAegisMixin:
# ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _emit: Any
    _get_study: Any
    _goal_store: Any
    _scoreboard: Any

    def _novelty_gate(
        self, round_num: int, hypothesis: str, predicted_affected: list
    ) -> bool:
        """Run the AEGIS novelty gate; returns True when the round proceeds."""
        is_novel, novelty_reason = self._check_novelty(hypothesis, predicted_affected)
        if is_novel:
            return True
        self._archive_rejected(round_num, hypothesis, "novelty", novelty_reason)
        self._emit(self._get_study().session_id, "study_round_rejected", {
            "study_id": self._get_study().study_id, "round": round_num,
            "reason": "novelty", "detail": novelty_reason,
        })
        return False

    def _record_journal_and_regression(
        self,
        round_num: int,
        hypothesis: str,
        predicted_affected: list,
        lever: str,
        strategist_output,
        gating_outcome: str,
        attribution,
    ) -> None:
        """AEGIS: append journal entry + run the regression gate.

        goal 台账已随 apps/goal 移除（2026-11-04 批次 2）：journal 写入跳过，
        但回归门仍跑——它只依赖 attribution。
        """
        session = self._get_study().session_id
        if self._goal_store is not None:
            self._goal_store.append_journal_entry(
                self._get_study().goal_id, session, round_num, hypothesis, hypothesis[:60],
                levers=[lever], predicted_affected=predicted_affected,
                changeset=strategist_output.get("changes") if isinstance(strategist_output, dict) else None,
            )
            self._goal_store.fill_journal_attribution(
                self._get_study().goal_id, session, round_num, gating_outcome, attribution,
            )
        passes, regressed = self._check_regression(attribution)
        if not passes:
            self._archive_rejected(round_num, hypothesis, "regression", str(regressed))
            self._emit(session, "study_round_rejected", {
                "study_id": self._get_study().study_id, "round": round_num,
                "reason": "regression", "regressed": regressed,
            })

    # ── Graph loading (topology-aware scheduling) ────────────────

    def _verdict_reason(self, eval_result: dict, strategist_output: Any) -> str:
        """Extract the verdict reason from decision/attribution output."""
        return _aegis.verdict_reason(eval_result, strategist_output)

    # ── AEGIS helpers (delegating to aegis.py module functions) ────

    def _check_novelty(self, hypothesis: str, predicted_affected: list[str]) -> tuple[bool, str | None]:
        return _aegis.check_novelty(
            self._goal_store, self._get_study().goal_id, hypothesis, predicted_affected,
        )

    def _check_regression(self, attribution: dict[str, str]) -> tuple[bool, list[str]]:
        return _aegis.check_regression(
            self._goal_store, self._get_study().goal_id, attribution,
        )

    def _archive_rejected(self, round_num: int, hypothesis: str, reason: str, detail: str) -> None:
        _aegis.archive_rejected(
            self._goal_store, self._get_study().goal_id,
            round_num, hypothesis, reason, detail,
        )

    def _build_journal_context(self) -> str:
        s = self._get_study()
        return _aegis.build_journal_context(self._goal_store, s.goal_id, s.current_round)

    def _build_scoreboard_context(self) -> str:
        if not hasattr(self, "_scoreboard"):
            return ""
        return self._scoreboard.build_scoreboard_context()

    # ── budget (delegating to budget.py module functions) ──────────
