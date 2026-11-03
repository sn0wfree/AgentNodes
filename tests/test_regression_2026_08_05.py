"""Regression tests for two bugs fixed in 2026-08-05:

1. ``AgentLoop._amaybe_compact`` had a nested ``_AchatAdapter`` that
   called ``asyncio.run(asyncio.to_thread(...))`` from inside a running
   event loop, raising ``RuntimeError`` and producing a
   "coroutine 'to_thread' was never awaited" warning.  The fix wraps
   the entire ``compact_messages(...)`` call in ``asyncio.to_thread``,
   so the sync LLM client runs in a worker thread with no nested
   event loop.

2. ``goal_needs_continuation`` previously returned ``True``
   unconditionally whenever the goal status was in
   ``CONTINUABLE_GOAL_STATUSES``, even when there were no criteria
   (nothing to drive continuation).  The fix returns ``False`` when
   no required criterion remains uncovered.
"""

from __future__ import annotations

import inspect
import warnings
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.kernel.agent.compact import CompactConfig

# ════════════════════════════════════════════════════════════════════
# Fix 1: _amaybe_compact no longer uses nested asyncio.run + to_thread
# ════════════════════════════════════════════════════════════════════


def _strip_docstring(source: str) -> str:
    """Remove the leading docstring so we only inspect executable code."""
    lines = source.split("\n")
    in_doc = False
    out: list[str] = []
    for line in lines:
        if '"""' in line and not in_doc:
            in_doc = True
            if line.count('"""') == 2:
                # Single-line docstring
                in_doc = False
            continue
        if in_doc and '"""' in line:
            in_doc = False
            continue
        if not in_doc:
            out.append(line)
    return "\n".join(out)


class TestAmaybeCompactStructural:
    """Static structural checks on the rewritten _amaybe_compact."""

    def test_no_inner_achat_adapter_class(self):
        from strategy_research.kernel.agent.loop import AgentLoop
        src = _strip_docstring(inspect.getsource(AgentLoop._amaybe_compact))
        assert "class _AchatAdapter" not in src

    def test_no_nested_asyncio_run(self):
        """asyncio.run() cannot be called from a running event loop."""
        from strategy_research.kernel.agent.loop import AgentLoop
        src = _strip_docstring(inspect.getsource(AgentLoop._amaybe_compact))
        assert "asyncio.run(" not in src

    def test_uses_to_thread_to_offload_compact_messages(self):
        from strategy_research.kernel.agent.loop import AgentLoop
        src = _strip_docstring(inspect.getsource(AgentLoop._amaybe_compact))
        assert "await asyncio.to_thread(" in src
        # The offloaded unit is the shared sync core (run_compact injected)
        assert "_maybe_compact_impl" in src
        # The engine invocation passes the sync client directly (no adapter)
        impl_src = _strip_docstring(inspect.getsource(AgentLoop._run_compact_messages))
        assert "compact_messages(" in impl_src
        assert "llm_client=self.client" in impl_src


class TestAmaybeCompactRuntime:
    """Runtime checks: the offloaded call actually runs without warnings."""

    def _make_loop(self):
        from strategy_research.kernel.agent.loop import AgentLoop
        cfg = CompactConfig()
        config = MagicMock()
        config.model_context_tokens = 1_000_000
        config.model_max_output_tokens = 128_000
        config.compact_config = cfg
        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.cc = cfg
        loop.session_id = "ses_regression"
        loop._previous_summary = None
        loop._event_bus = None
        loop.threshold_tokens = None
        loop.client = MagicMock()
        return loop

    @pytest.mark.asyncio
    async def test_below_threshold_emits_no_warning(self):
        """Below-threshold path returns early without to_thread.

        The previous bug was in the L4 path, not the early return;
        this test ensures we did not regress the cheap path.
        """
        loop = self._make_loop()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            msgs = [{"role": "user", "content": "hi"}]
            result, applied = await loop._amaybe_compact(msgs)
        assert applied == []
        # No "coroutine was never awaited" warnings
        to_thread_warnings = [
            w for w in caught
            if "never awaited" in str(w.message).lower()
        ]
        assert to_thread_warnings == []

    @pytest.mark.asyncio
    async def test_compact_messages_invoked_via_to_thread(self):
        """When compaction is forced, compact_messages runs in a thread.

        We assert the to_thread coroutine was awaited by spying on
        ``asyncio.to_thread`` itself: every call to it must have been
        awaited.  The cleanest way to detect an unawaited coroutine is
        the warning filter above, so we use that.
        """
        loop = self._make_loop()
        # Provide a minimal event_bus so _persist_compaction_event takes
        # the event-sourcing path (no persister required) — the legacy
        # path would now fail-fast without a registered persister.
        loop._event_bus = MagicMock()
        msgs = [
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
            {"role": "user", "content": "z" * 400},
        ]
        # compact_messages returns compressed result (L4 fired)
        mock_result = (
            [{"role": "system", "content": "sum"}, msgs[-1]],
            ["llm_summarize(3->2)"],
            "summary text",
            "recent text",
        )
        with patch(
            "strategy_research.kernel.agent.loop.compact_messages",
            return_value=mock_result,
        ), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result, applied = await loop._amaybe_compact(list(msgs))
        # No unawaited coroutine warnings
        to_thread_warnings = [
            w for w in caught
            if "never awaited" in str(w.message).lower()
        ]
        assert to_thread_warnings == [], (
            f"Got unawaited-coroutine warnings: "
            f"{[str(w.message) for w in to_thread_warnings]}"
        )
        assert "llm_summarize(3->2)" in applied

    @pytest.mark.asyncio
    async def test_llm_client_called_directly_not_via_adapter(self):
        """Inside the worker thread, compact_messages invokes self.client.chat
        directly (no sync wrapper).  We verify by mocking compact_messages
        to capture the llm_client it receives and confirm it is self.client.
        """
        loop = self._make_loop()
        # event_bus mock so _persist_compaction_event takes the
        # event-sourcing path (avoid fail-fast on missing persister).
        loop._event_bus = MagicMock()
        captured_client: dict[str, Any] = {}

        def fake_compact(messages, **kwargs):
            captured_client["llm_client"] = kwargs.get("llm_client")
            return (
                [{"role": "system", "content": "s"}],
                [],
                "",
                "",
            )

        msgs = [{"role": "user", "content": "x" * 400}]
        with patch(
            "strategy_research.kernel.agent.loop.compact_messages",
            side_effect=fake_compact,
        ):
            await loop._amaybe_compact(list(msgs))
        # The fix passes self.client directly, not an adapter
        assert captured_client["llm_client"] is loop.client


# ════════════════════════════════════════════════════════════════════
# Fix 2: goal_needs_continuation returns False when nothing is open
# ════════════════════════════════════════════════════════════════════


class TestGoalNeedsContinuationEdgeCases:
    """Previously the function returned True unconditionally."""

    def _make_snapshot(
        self,
        *,
        status: str = "active",
        criteria: list[dict] | None = None,
        evidence: list[dict] | None = None,
    ) -> dict[str, Any]:
        return {
            "goal": {
                "goal_id": "g",
                "session_id": "s",
                "status": status,
                "objective": "obj",
                "ui_summary": "obj",
                "source": "api",
                "protocol": "thesis_review",
                "risk_tier": "research_general",
                "token_budget": None,
                "tokens_used": 0,
                "turn_budget": None,
                "turns_used": 0,
                "time_budget_seconds": None,
                "time_used_seconds": 0,
                "budget_wrapup_sent": False,
                "created_at": "2026-07-22T00:00:00Z",
                "updated_at": "2026-07-22T00:00:00Z",
                "completed_at": None,
                "recap": None,
            },
            "claims": [],
            "criteria": criteria or [],
            "evidence": evidence or [],
            "evidence_count": len(evidence or []),
        }

    def _get_function(self):
        from strategy_research.apps.goal.context import goal_needs_continuation
        return goal_needs_continuation

    def test_empty_criteria_returns_false(self):
        """Bug fix: empty criteria list -> no work to drive -> False."""
        func = self._get_function()
        for criteria in ([], None):
            snap = self._make_snapshot(status="active", criteria=criteria)
            assert func(snap) is False, (
                f"empty criteria should return False, got {func(snap)}"
            )

    def test_all_required_covered_by_status_returns_false(self):
        func = self._get_function()
        snap = self._make_snapshot(
            status="active",
            criteria=[
                {"criterion_id": "c1", "required": True, "status": "covered", "text": "t"},
                {"criterion_id": "c2", "required": True, "status": "satisfied", "text": "t"},
            ],
            evidence=[],
        )
        assert func(snap) is False

    def test_all_required_covered_by_evidence_returns_false(self):
        func = self._get_function()
        snap = self._make_snapshot(
            status="active",
            criteria=[
                {"criterion_id": "c1", "required": True, "status": "pending", "text": "t"},
            ],
            evidence=[{"criterion_id": "c1"}],
        )
        assert func(snap) is False

    def test_open_required_criterion_returns_true(self):
        func = self._get_function()
        snap = self._make_snapshot(
            status="active",
            criteria=[
                {"criterion_id": "c1", "required": True, "status": "pending", "text": "t"},
            ],
            evidence=[],
        )
        assert func(snap) is True

    def test_optional_open_required_closed_returns_true(self):
        """Optional criterion open + required closed -> still True (required is open)."""
        func = self._get_function()
        snap = self._make_snapshot(
            status="active",
            criteria=[
                {"criterion_id": "c1", "required": False, "status": "pending", "text": "opt"},
                {"criterion_id": "c2", "required": True, "status": "pending", "text": "req"},
            ],
            evidence=[],
        )
        assert func(snap) is True

    def test_only_optional_open_required_all_closed_returns_false(self):
        """When only optional criteria are open, no required work to drive."""
        func = self._get_function()
        snap = self._make_snapshot(
            status="active",
            criteria=[
                {"criterion_id": "c1", "required": False, "status": "pending", "text": "opt"},
                {"criterion_id": "c2", "required": True, "status": "covered", "text": "req"},
            ],
            evidence=[],
        )
        assert func(snap) is False

    def test_continuable_status_with_open_work(self):
        """needs_refresh and insufficient_evidence also drive continuation."""
        func = self._get_function()
        open_crit = [{"criterion_id": "c1", "required": True, "status": "pending", "text": "t"}]
        for status in ("needs_refresh", "insufficient_evidence"):
            snap = self._make_snapshot(status=status, criteria=open_crit)
            assert func(snap) is True, f"status={status} should drive continuation"

    def test_non_continuable_status_returns_false(self):
        func = self._get_function()
        open_crit = [{"criterion_id": "c1", "required": True, "status": "pending", "text": "t"}]
        for status in ("complete", "cancelled", "paused", "blocked"):
            snap = self._make_snapshot(status=status, criteria=open_crit)
            assert func(snap) is False, f"status={status} should not continue"

    def test_missing_goal_key_returns_false(self):
        """Defensive: snapshot without 'goal' key."""
        func = self._get_function()
        assert func({"criteria": []}) is False
