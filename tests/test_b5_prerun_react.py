"""B5 收口：goal/memory 注入 react 路径（无 AgentLoop）。

契约：docs/archive/2026-10/stage-c1-b5-prerun-react-20261029.md

关键设计：两个 pre-run injector 只读 loop 的三个属性
（enable_goal_injection / enable_memory_injection / session_id），
所以用 3 属性 facade 复用整条已验证的 injector 链，不写第二个 provider。
"""
from __future__ import annotations

from unittest.mock import patch

from agentnodes.kernel.agent.context_providers import (
    build_prerun_provider_for_session,
)
from agentnodes.kernel.context.protocol import ContextQuery


def _build(**kwargs):
    return build_prerun_provider_for_session(**kwargs)


class _GoalCtx:
    """Stand-in for the ``goal.context`` capability (ADR-003 端口化后).

    kernel 不再 import apps.goal，而是 ``resolve("goal.context")``。
    patch 包属性无效（``resolve`` 是函数内 import 的局部名），所以
    改为**直接替换 default registry 里的 factory**，跑完再还原——
    这是唯一能可靠影响 resolve 结果的注入点。
    """

    def __init__(self, text, _extra=None, side_effect=None):
        self._text = text
        self._side_effect = side_effect

    def get_current_goal_context(self, session_id):
        if self._side_effect is not None:
            return self._side_effect(session_id)
        return (self._text, None)


_GOAL_PATCH = "agentnodes.kernel.agent.capability.resolve"


def _patch_goal_ctx(goal_ctx):
    """Return a context manager installing ``goal_ctx`` as the goal.context kind."""
    from contextlib import contextmanager

    from agentnodes.kernel.agent.capability import get_default_registry

    @contextmanager
    def _cm():
        registry = get_default_registry()
        had = "goal.context" in registry
        prev = registry._factories.get("goal.context")
        registry._factories["goal.context"] = lambda r: goal_ctx
        try:
            yield
        finally:
            if had and prev is not None:
                registry._factories["goal.context"] = prev
            else:
                registry._factories.pop("goal.context", None)

    return _cm()


# ── T1: goal block appears for a session with an active goal ──


def test_goal_block_injected():
    provider = _build()
    with _patch_goal_ctx(
        _GoalCtx("<current-research-goal>G</current-research-goal>")
    ):
        items = provider.provide(ContextQuery(task_text="do work", session_id="s1"))
    assert len(items) == 1
    assert items[0].kind == "goal"
    assert "current-research-goal" in items[0].content


def test_goal_disabled_skips_injection():
    provider = _build(enable_goal_injection=False)
    with _patch_goal_ctx(
        _GoalCtx("<current-research-goal>G</current-research-goal>")
    ):
        items = provider.provide(ContextQuery(task_text="do work", session_id="s1"))
    assert items == []


def test_no_session_skips_injection():
    provider = _build()
    with _patch_goal_ctx(
        _GoalCtx("<current-research-goal>G</current-research-goal>")
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
        "agentnodes.kernel.memory.persistent.PersistentMemory",
        return_value=_FakeMemory(),
    ), _patch_goal_ctx(_GoalCtx(None)):
        items = provider.provide(ContextQuery(task_text="alpha?", session_id="s1"))
    assert len(items) == 1
    assert items[0].kind == "memory"
    assert "relevant-memory" in items[0].content


def test_memory_disabled_skips_injection():
    provider = _build(enable_memory_injection=False)
    with patch(
        "agentnodes.kernel.memory.persistent.PersistentMemory",
        return_value=_FakeMemory(),
    ), _patch_goal_ctx(_GoalCtx(None)):
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

    with _patch_goal_ctx(_GoalCtx(None, side_effect=fake_goal)):
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
    with _patch_goal_ctx(
        _GoalCtx("<current-research-goal>G</current-research-goal>")
    ), patch(
        "agentnodes.kernel.memory.persistent.PersistentMemory",
        return_value=_FakeMemory(),
    ):
        items = provider.provide(ContextQuery(task_text="alpha?", session_id="s1"))
    assert [i.kind for i in items] == ["memory", "goal"]


# ── singleton wiring ────────────────────────────────────────


def test_singleton_hub_registers_prerun_and_todos():
    from agentnodes.kernel.gateway.singleton import (
        _build_context_hub,
        reset_gateway,
    )

    reset_gateway()
    hub = _build_context_hub()
    names = [p.name for p in hub._providers]
    assert "prerun_context" in names
    assert "todos" in names
