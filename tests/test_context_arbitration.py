"""B5 W2: ContextPlan arbitration + budget sourcing.

Contract: docs/archive/2026-10/stage-c1-b5-w2-arbitration-20261029.md

Two properties must hold at once:
  1. ``budget_tokens=None`` is byte-identical to W0 (no arbitration).
  2. When a budget is supplied, trimming is deterministic and never drops
     protected kinds -- goals survive even if that breaks the "fits in
     budget" invariant, and the breach is reported instead of hidden.
"""
from __future__ import annotations

from types import SimpleNamespace

from agentnodes.kernel.context.hub import ContextHub
from agentnodes.kernel.context.policy import ContextPolicy
from agentnodes.kernel.context.protocol import ContextItem, ContextQuery


def _provider(name: str, items: list[ContextItem], scopes=None):
    return SimpleNamespace(
        name=name,
        scopes=scopes if scopes is not None else frozenset({"session"}),
        provide=lambda q: [ContextItem(
            kind=i.kind, content=i.content, source=i.source,
            provider=name, priority=i.priority, tokens=i.tokens,
            scope=i.scope, meta=dict(i.meta),
        ) for i in items],
    )


def _item(kind: str, tokens: int, priority: int = 0, source: str = "s"):
    """tokens is a *claim*; the hub only falls back to estimating when 0."""
    return ContextItem(
        kind=kind, content="x" * 1, source=source, provider="p",
        priority=priority, tokens=tokens,
    )


# ── T1: default is no arbitration (W0 compatibility) ─────────


def test_no_budget_means_no_arbitration():
    hub = ContextHub()
    hub.register(_provider("p", [
        _item("memory", 100), _item("goal", 100), _item("todo", 100),
    ]))
    bundle = hub.build(ContextQuery(task_text="t", session_id="s"))
    assert len(bundle.items) == 3
    assert "over_budget" not in bundle.accounting


def test_budget_under_total_is_noop():
    hub = ContextHub()
    hub.register(_provider("p", [_item("memory", 10)]))
    bundle = hub.build(
        ContextQuery(task_text="t", session_id="s"), budget_tokens=1000,
    )
    assert len(bundle.items) == 1
    assert bundle.accounting["over_budget"] is False


# ── T2: deterministic trim order ─────────────────────────────


def test_trim_drops_low_rank_first():
    """tool_result (rank 8) goes before memory (rank 0)."""
    hub = ContextHub()
    hub.register(_provider("p", [
        _item("memory", 100), _item("tool_result", 100),
    ]))
    bundle = hub.build(
        ContextQuery(task_text="t", session_id="s"),
        budget_tokens=100, policy=ContextPolicy(protected_kinds=frozenset()),
    )
    assert [i.kind for i in bundle.items] == ["memory"]


def test_survivor_order_preserved():
    hub = ContextHub()
    hub.register(_provider("p", [
        _item("memory", 10, source="a"),
        _item("goal", 10, source="b"),
        _item("tool_result", 100, source="c"),
    ]))
    bundle = hub.build(
        ContextQuery(task_text="t", session_id="s"),
        budget_tokens=20, policy=ContextPolicy(protected_kinds=frozenset()),
    )
    assert [i.source for i in bundle.items] == ["a", "b"]


def test_trim_is_deterministic_across_calls():
    def run():
        hub = ContextHub()
        hub.register(_provider("p", [
            _item("memory", 50, priority=1),
            _item("goal", 50, priority=0),
            _item("todo", 50, priority=2),
            _item("knowledge", 50, priority=0),
        ]))
        return [i.kind for i in hub.build(
            ContextQuery(task_text="t", session_id="s"),
            budget_tokens=100,
            policy=ContextPolicy(protected_kinds=frozenset()),
        ).items]

    assert run() == run()


def test_drops_larger_item_first_within_same_rank():
    hub = ContextHub()
    hub.register(_provider("a", [_item("memory", 90, source="big")]))
    hub.register(_provider("b", [_item("memory", 10, source="small")]))
    bundle = hub.build(
        ContextQuery(task_text="t", session_id="s"),
        budget_tokens=20, policy=ContextPolicy(protected_kinds=frozenset()),
    )
    assert [i.source for i in bundle.items] == ["small"]


# ── T3: protected kinds survive, breach is reported ─────────


def test_protected_kind_never_dropped():
    hub = ContextHub()
    hub.register(_provider("p", [
        _item("goal", 500), _item("memory", 500),
    ]))
    bundle = hub.build(
        ContextQuery(task_text="t", session_id="s"), budget_tokens=100,
    )
    assert [i.kind for i in bundle.items] == ["goal"]


def test_protected_alone_over_budget_is_flagged_not_hidden():
    hub = ContextHub()
    hub.register(_provider("p", [_item("goal", 500)]))
    bundle = hub.build(
        ContextQuery(task_text="t", session_id="s"), budget_tokens=100,
    )
    assert len(bundle.items) == 1
    assert bundle.accounting["over_budget"] is True
    reasons = [d.get("reason") for d in bundle.dropped]
    assert "budget_exhausted_protected" in reasons


# ── T4: per-provider caps ───────────────────────────────────


def test_provider_cap_marks_and_trims_that_provider_first():
    hub = ContextHub()
    hub.register(_provider("chatty", [_item("memory", 200, source="c1")]))
    hub.register(_provider("quiet", [_item("memory", 100, source="q1")]))
    bundle = hub.build(
        ContextQuery(task_text="t", session_id="s"),
        budget_tokens=150,
        policy=ContextPolicy(
            protected_kinds=frozenset(), provider_caps={"chatty": 50},
        ),
    )
    assert bundle.accounting["per_provider"]["chatty"]["over_cap"] is True
    assert [i.source for i in bundle.items] == ["q1"]


# ── T8: scope isolation ─────────────────────────────────────


def test_study_only_provider_skipped_for_session_query():
    hub = ContextHub()
    hub.register(_provider(
        "study_notes", [_item("knowledge", 10)],
        scopes=frozenset({"study"}),
    ))
    bundle = hub.build(ContextQuery(task_text="t", session_id="s"))
    assert bundle.items == []
    assert any("scope:" in d.get("reason", "") for d in bundle.dropped)


def test_study_provider_runs_for_study_query():
    hub = ContextHub()
    hub.register(_provider(
        "study_notes", [_item("knowledge", 10)],
        scopes=frozenset({"study"}),
    ))
    bundle = hub.build(
        ContextQuery(task_text="t", session_id="s", study_id="st1"),
    )
    assert len(bundle.items) == 1


def test_session_provider_still_runs_for_study_query():
    hub = ContextHub()
    hub.register(_provider("todos", [_item("todo", 10)]))
    bundle = hub.build(
        ContextQuery(task_text="t", session_id="s", study_id="st1"),
    )
    assert len(bundle.items) == 1


# ── T6: budget sourced from client.config (R-3) ─────────────


def test_model_limits_read_from_client_config():
    """OpenAICompatClient has no such attrs; they live on .config."""
    from agentnodes.kernel.gateway.session_controller import SessionController
    from agentnodes.kernel.orchestration import EntryPoint

    client = SimpleNamespace(
        config=SimpleNamespace(
            model_context_tokens=128_000, model_max_output_tokens=8_192,
        )
    )
    ctrl = SessionController(EntryPoint(), client=client)
    assert ctrl._model_limits() == (128_000, 8_192)
    assert ctrl._context_budget_tokens() == int(128_000 * 0.8)


def test_missing_config_yields_no_budget():
    from agentnodes.kernel.gateway.session_controller import SessionController
    from agentnodes.kernel.orchestration import EntryPoint

    ctrl = SessionController(EntryPoint(), client=SimpleNamespace())
    assert ctrl._model_limits() == (None, None)
    assert ctrl._context_budget_tokens() is None


# ── T7: over_budget is surfaced as an event ─────────────────


def test_over_budget_emits_event():
    from agentnodes.kernel.gateway.session_controller import SessionController
    from agentnodes.kernel.orchestration import EntryPoint

    hub = ContextHub()
    hub.register(_provider("p", [_item("goal", 500)]))
    events: list[tuple[str, dict]] = []
    ctrl = SessionController(
        EntryPoint(),
        client=SimpleNamespace(
            config=SimpleNamespace(model_context_tokens=100),
        ),
        sink=lambda k, d: events.append((k, d)),
        context_hub=hub,
        context_policy=ContextPolicy(),
    )
    ctrl._inject_context([{"role": "user", "content": "hi"}], "s1")
    assert any(k == "context.over_budget" for k, _ in events)


# ── W0 compatibility: legacy hub without kwargs ─────────────


def test_legacy_duck_typed_hub_still_works():
    from agentnodes.kernel.gateway.session_controller import SessionController
    from agentnodes.kernel.orchestration import EntryPoint

    class _LegacyHub:
        def __init__(self):
            self.called = False

        def build(self, query):  # no kwargs -- W0 signature
            self.called = True
            return SimpleNamespace(items=[], accounting={})

    hub = _LegacyHub()
    ctrl = SessionController(
        EntryPoint(),
        client=SimpleNamespace(config=SimpleNamespace(model_context_tokens=1000)),
        context_hub=hub,
    )
    ctrl._inject_context([{"role": "user", "content": "hi"}], "s1")
    assert hub.called
