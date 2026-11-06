"""CompletionStrategy — Strategy pattern for goal workflow completion.

Replaces the ``if/elif`` chain in ``GoalWorkflowRunner._auto_complete``
with pluggable strategies.  Three built-in modes:

  - AutoCompleteStrategy: full audit + update_status(COMPLETE, audit=...)
  - LiteCompleteStrategy:  evidence-only via complete_lite()
  - ManualCompleteStrategy: no-op (waits for explicit /goal complete)

New strategies can be registered via ``CompletionStrategyFactory.register``.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class CompletionStrategy(Protocol):
    """How a goal workflow finishes its goal."""

    async def complete(
        self,
        store: Any,
        session_id: str,
        goal_id: str,
        criteria: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        workflow_name: str,
    ) -> bool:
        """Execute the completion. Returns True on success."""
        ...


# ── Built-in Strategies ──────────────────────────────────────


class AutoCompleteStrategy:
    """Full audit + ``update_status(COMPLETE, audit=...)``.

    Builds one audit row per required criterion and submits them with
    the completion status update.
    """

    async def complete(
        self,
        store: Any,
        session_id: str,
        goal_id: str,
        criteria: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        workflow_name: str,
    ) -> bool:
        from .models import AuditRow, GoalStatus

        audit_rows = []
        all_evidence_ids = [e.get("evidence_id", "") for e in evidence]
        for criterion in criteria:
            if not criterion.get("required", True):
                continue
            cid = criterion.get("criterion_id", "")
            cid_evidence_ids = [
                e.get("evidence_id", "") for e in evidence
                if e.get("criterion_id") == cid
            ]
            audit_rows.append(AuditRow(
                criterion_id=cid,
                result="satisfied",
                evidence_ids=cid_evidence_ids or all_evidence_ids[:1],
                notes=f"Auto-completed by workflow {workflow_name}",
            ))

        store.update_status(
            session_id=session_id,
            goal_id=goal_id,
            expected_goal_id=goal_id,
            status=GoalStatus.COMPLETE,
            audit=audit_rows,
            recap=f"Workflow {workflow_name} auto-completed",
        )
        return True


class LiteCompleteStrategy:
    """Evidence-only completion via ``complete_lite()``.

    Does not require audit rows — only that every required criterion
    has at least one linked evidence record.
    """

    async def complete(
        self,
        store: Any,
        session_id: str,
        goal_id: str,
        criteria: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        workflow_name: str,
    ) -> bool:
        store.complete_lite(
            session_id=session_id,
            goal_id=goal_id,
            expected_goal_id=goal_id,
            recap=f"Workflow {workflow_name} auto-completed (lite)",
        )
        return True


class ManualCompleteStrategy:
    """No automatic completion.  Waits for an explicit ``/goal complete``.

    Just signals completion to the runner (status="completed") without
    calling any GoalStore mutation.  The user/agent must invoke
    ``/goal complete`` afterwards.
    """

    async def complete(
        self,
        store: Any,
        session_id: str,
        goal_id: str,
        criteria: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        workflow_name: str,
    ) -> bool:
        # Intentionally no-op — the runner sets state to "completed"
        # and the user calls /goal complete manually.
        logger.info(
            "Manual mode: workflow %s finished, awaiting /goal complete",
            workflow_name,
        )
        return True


# ── Factory ───────────────────────────────────────────────────


class CompletionStrategyFactory:
    """Factory: select a CompletionStrategy by mode name."""

    _strategies: dict[str, CompletionStrategy] = {
        "auto": AutoCompleteStrategy(),
        "lite": LiteCompleteStrategy(),
        "manual": ManualCompleteStrategy(),
    }

    @classmethod
    def get(cls, mode: str) -> CompletionStrategy:
        """Get a strategy by mode name.  Falls back to ``"auto"``."""
        return cls._strategies.get(mode, cls._strategies["auto"])

    @classmethod
    def register(cls, mode: str, strategy: CompletionStrategy) -> None:
        """Register a custom strategy under a new mode name."""
        cls._strategies[mode] = strategy

    @classmethod
    def list_modes(cls) -> list[str]:
        """List all registered mode names."""
        return list(cls._strategies.keys())


__all__ = [
    "CompletionStrategy",
    "AutoCompleteStrategy",
    "LiteCompleteStrategy",
    "ManualCompleteStrategy",
    "CompletionStrategyFactory",
]
