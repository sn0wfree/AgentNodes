"""Unit tests for A5 agent runtime seams (model_io / telemetry).

U6-2（2026-11-14）：``policy.py``（StopPolicy/StopDecision/limits_from_config）
随 legacy 循环体退役，其 10 条测试随删——预算语义由 ``BudgetDecider``
（deciders）承担并由引擎语义网（test_engine_semantics）钉住。
"""
from __future__ import annotations

from types import SimpleNamespace

from agentnodes.kernel.agent.runtime import (
    ModelIO,
    Telemetry,
)


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
