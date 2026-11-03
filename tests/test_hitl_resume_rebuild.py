"""PR-A regression: HITL approval polling.

- ``_wait_for_approval`` must report the actual decision
  (approved/rejected/timeout), not collapse rejection into timeout.

(P5-2: the langgraph resume/profile tests were retired with the branch;
graph-path resume is covered in test_hitl_gate_graph.py.)
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def _bare_runner(**attrs):
    """Build an AutoresearchRunner without running its heavy __init__."""
    from strategy_research.apps.study.runner import AutoresearchRunner, ControlToken

    runner = AutoresearchRunner.__new__(AutoresearchRunner)
    # __init__ is skipped, so provide the attributes methods read:
    # _wait_for_approval polls runner.control.cancelled/paused.
    runner.control = ControlToken()
    for key, value in attrs.items():
        setattr(runner, key, value)
    return runner


# ── _wait_for_approval ──────────────────────────────────────

@pytest.mark.asyncio
@patch("strategy_research.apps.study.runner.time.time")
async def test_wait_for_approval_returns_approved(mock_time):
    from types import SimpleNamespace

    mock_time.side_effect = lambda: 0.0
    intr = SimpleNamespace(status="approved")
    store = MagicMock()
    store.get_interrupt_for_round.return_value = intr
    runner = _bare_runner(study_store=store)

    result = await runner._wait_for_approval("s", 2, timeout_s=10)
    assert result == "approved"


@pytest.mark.asyncio
@patch("strategy_research.apps.study.runner.time.time")
async def test_wait_for_approval_returns_rejected(mock_time):
    """Rejection must be distinguishable from timeout (B2 flip side)."""
    from types import SimpleNamespace

    mock_time.side_effect = lambda: 0.0
    intr = SimpleNamespace(status="rejected")
    store = MagicMock()
    store.get_interrupt_for_round.return_value = intr
    runner = _bare_runner(study_store=store)

    result = await runner._wait_for_approval("s", 2, timeout_s=10)
    assert result == "rejected"


@pytest.mark.asyncio
async def test_wait_for_approval_times_out(monkeypatch):
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    import strategy_research.apps.study.runner as runner_mod

    clock = {"now": 0.0}
    monkeypatch.setattr(runner_mod.time, "time", lambda: clock["now"])

    def _advance(_sid, _rn):
        clock["now"] += 999.0  # each poll consumes the whole budget
        return None

    store = MagicMock()
    store.get_interrupt_for_round.side_effect = _advance
    runner = _bare_runner(study_store=store)

    result = await runner._wait_for_approval("s", 2, timeout_s=600)
    assert result == "timeout"
