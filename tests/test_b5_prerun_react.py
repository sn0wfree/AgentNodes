"""B5 收口：goal/memory 注入 react 路径（无 AgentLoop）。

契约：docs/stage-c1-b5-prerun-react-20261029.md

关键设计：两个 pre-run injector 只读 loop 的三个属性
（enable_goal_injection / enable_memory_injection / session_id），
所以用 3 属性 facade 复用整条已验证的 injector 链，不写第二个 provider。
"""
from __future__ import annotations

from unittest.mock import patch

from strategy_research.core.agent.context_providers import (
    build_prerun_provider_for_session,
)
from strategy_research.kernel.context.protocol import ContextQuery


def _build(**kwargs):
    return build_prerun_provider_for_session(**kwargs)


# ── T1: goal block appears for a session with an active goal ──


def test_goal_block_injected():
    provider = _build()
    with patch(
        "strategy_research.core.goal.get_current_goal_context",
        return_value=("<current-research-goal>G</current-research-goal>", None),
    ):
        items = provider.provide(ContextQuery(task_text="do work", session_id="s1"))
    assert len(items) == 1
    assert items[0].kind == "goal"
    assert "current-research-goal" in items[0].content


def test_goal_disabled_skips_injection():
    provider = _build(enable_goal_injection=False)
    with patch(
        "strategy_research.core.goal.get_current_goal_context",
        return_value=("<current-research-goal>G</current-research-goal>", None),
    ):
        items = provider.provide(ContextQuery(task_text="do work", session_id="s1"))
    assert items == []


def test_no_session_skips_injection():
    provider = _build()
    with patch(
        "strategy_research.core.goal.get_current_goal_context",
        return_value=("<current-research-goal>G</current-research-goal>", None),
    ):
        items = provider.provide(ContextQuery(task_text="do work"))
    assert items == []


# ── T3: memory block ────────────────────────────────────────


class _FakeMemory:
    def find_relevant(self, task, max_results=5):
        return [{"body": "prior finding about alpha", "kind": "note"}]


def test_memory_block_injected():
    provider = _build()
    with patch(
        "strategy_research.kernel.memory.persistent.PersistentMemory",
        return_value=_FakeMemory(),
    ), patch(
        "strategy_research.core.goal.get_current_goal_context",
        return_value=(None, None),
    ):
        items = provider.provide(ContextQuery(task_text="alpha?", session_id="s1"))
    assert len(items) == 1
    assert items[0].kind == "memory"
    assert "relevant-memory" in items[0].content


def test_memory_disabled_skips_injection():
    provider = _build(enable_memory_injection=False)
    with patch(
        "strategy_research.kernel.memory.persistent.PersistentMemory",
        return_value=_FakeMemory(),
    ), patch(
        "strategy_research.core.goal.get_current_goal_context",
        return_value=(None, None),
    ):
        items = provider.provide(ContextQuery(task_text="alpha?", session_id="s1"))
    assert items == []


# ── T6: per-query session scoping on a shared provider ──────


def test_session_switches_per_query():
    """The hub is a process singleton: each query's session must reach the
    injectors, or every session would see the first one's goal."""
    provider = _build()
    seen: list[str] = []

    def fake_goal(session_id):
        seen.append(session_id)
        return (None, None)

    with patch(
        "strategy_research.core.goal.get_current_goal_context",
        side_effect=fake_goal,
    ):
        provider.provide(ContextQuery(task_text="a", session_id="s1"))
        provider.provide(ContextQuery(task_text="b", session_id="s2"))

    assert seen == ["s1", "s2"]


# ── T7: order matches the AgentLoop render order ────────────


def test_render_order_matches_agentloop():
    """``PreRunContextProvider`` reverses its items (context_providers.py:92)
    so the emitted order is memory→goal — the same order the AgentLoop path
    renders, and the same order ``DEFAULT_KIND_RANK`` encodes (memory=0).
    Changing it would move bytes, so the reversal is deliberate W0 behaviour.
    """
    provider = _build()
    with patch(
        "strategy_research.core.goal.get_current_goal_context",
        return_value=("<current-research-goal>G</current-research-goal>", None),
    ), patch(
        "strategy_research.kernel.memory.persistent.PersistentMemory",
        return_value=_FakeMemory(),
    ):
        items = provider.provide(ContextQuery(task_text="alpha?", session_id="s1"))
    assert [i.kind for i in items] == ["memory", "goal"]


# ── singleton wiring ────────────────────────────────────────


def test_singleton_hub_registers_prerun_and_todos():
    from strategy_research.core.gateway.singleton import (
        _build_context_hub,
        reset_gateway,
    )

    reset_gateway()
    hub = _build_context_hub()
    names = [p.name for p in hub._providers]
    assert "prerun_context" in names
    assert "todos" in names
