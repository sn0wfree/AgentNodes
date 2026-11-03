"""Unit tests for A5 agent runtime seams (policy / model_io / telemetry)."""
from __future__ import annotations

import time
from types import SimpleNamespace

from strategy_research.kernel.agent.runtime import (
    ModelIO,
    StopDecision,
    StopPolicy,
    Telemetry,
    limits_from_config,
)
from strategy_research.kernel.agent.strategy.budget import BudgetLimits
from strategy_research.kernel.agent.strategy.loop_strategy import LoopConfig


class TestLimitsFromConfig:
    def test_none_cfg(self):
        assert limits_from_config(None) == BudgetLimits()

    def test_reads_loop_config(self):
        cfg = LoopConfig(budget_max_tokens=100, budget_max_cost_usd=1.5, budget_max_seconds=9)
        lim = limits_from_config(cfg)
        assert lim.max_tokens == 100
        assert lim.max_cost_usd == 1.5
        assert lim.max_seconds == 9

    def test_prices(self):
        cfg = LoopConfig(price_per_mtok_input=0.1, price_per_mtok_output=0.2)
        lim = limits_from_config(cfg)
        assert lim.price_per_mtok_input == 0.1


class TestStopPolicy:
    def test_no_limits_keeps_going(self):
        d = StopPolicy().decide(tokens=10**9, iteration=100)
        assert d.should_stop is False

    def test_external_stop(self):
        d = StopPolicy().decide(external_stop=True, external_reason="user")
        assert d.should_stop and d.reason == "user" and d.kind == "external"

    def test_budget_tokens(self):
        d = StopPolicy(BudgetLimits(max_tokens=10)).decide(tokens=10)
        assert d.should_stop and d.kind == "budget" and d.reason == "budget:tokens"

    def test_budget_time(self):
        d = StopPolicy(BudgetLimits(max_seconds=0.01)).decide(
            started_at=time.perf_counter() - 1
        )
        assert d.should_stop and d.reason == "budget:time"

    def test_max_iterations(self):
        d = StopPolicy(max_iterations=3).decide(iteration=3)
        assert d.should_stop and d.reason == "max_iter"

    def test_iteration_under_cap(self):
        d = StopPolicy(max_iterations=5).decide(iteration=2)
        assert d.should_stop is False

    def test_decision_shape(self):
        d = StopPolicy().decide()
        assert isinstance(d, StopDecision)
        assert d.kind is None


class TestModelIO:
    def test_chat_delegates(self):
        calls = []

        class _Client:
            def chat(self, messages, tools=None, **kw):
                calls.append((messages, tools))
                return "resp"

        io = ModelIO(_Client())
        assert io.chat([{"role": "user", "content": "hi"}], tools=None) == "resp"
        assert calls

    def test_client_property(self):
        c = object()
        assert ModelIO(c).client is c


class TestTelemetry:
    def test_emit(self):
        events = []
        t = Telemetry(on_event=lambda etype, data: events.append((etype, data)))
        t.emit("x", {"a": 1})
        assert events == [("x", {"a": 1})]

    def test_emit_without_handler_noop(self):
        Telemetry().emit("x", {})

    def test_emit_handler_error_swallowed(self):
        def boom(etype, data):
            raise RuntimeError("no")

        Telemetry(on_event=boom).emit("x", {})

    def test_trace(self):
        written = []

        class _W:
            def write(self, entry):
                written.append(entry)

        Telemetry(trace_writer=_W()).trace({"type": "t"})
        assert written == [{"type": "t"}]

    def test_trace_and_emit(self):
        events = []
        written = []
        t = Telemetry(
            on_event=lambda e, d: events.append(e),
            trace_writer=SimpleNamespace(write=lambda e: written.append(e)),
        )
        t.trace_and_emit("ev", {"k": 1})
        assert events == ["ev"]
        assert written and written[0]["type"] == "ev"
