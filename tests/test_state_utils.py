"""Unit tests for study.state_utils — terminal marking, directives, emit."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from strategy_research.apps.study.state_utils import (
    emit_with_trace,
    format_directives,
    mark_terminal,
)


class TestMarkTerminal:
    def test_persists_status_and_metrics(self):
        store = MagicMock()
        mark_terminal(
            store,
            "s1",
            SimpleNamespace(value="done"),
            last_metrics={"sharpe": 1.0},
        )
        store.update_execution_status.assert_called_once()
        args, kwargs = store.update_execution_status.call_args
        assert args[0] == "s1"
        assert args[1].value == "done"
        assert kwargs["last_metrics"] == {"sharpe": 1.0}

    def test_reason_prefixes_error(self):
        store = MagicMock()
        mark_terminal(
            store, "s1", SimpleNamespace(value="failed"),
            last_error="boom", reason="timeout",
        )
        _, kwargs = store.update_execution_status.call_args
        assert kwargs["last_error"].startswith("timeout:")
        assert "boom" in kwargs["last_error"]

    def test_reason_without_error(self):
        store = MagicMock()
        mark_terminal(
            store, "s1", SimpleNamespace(value="failed"),
            reason="cancelled",
        )
        _, kwargs = store.update_execution_status.call_args
        assert kwargs["last_error"].startswith("cancelled:")

    def test_store_failure_is_swallowed(self):
        store = MagicMock()
        store.update_execution_status.side_effect = RuntimeError("db down")
        mark_terminal(store, "s1", SimpleNamespace(value="done"))  # must not raise


class TestFormatDirectives:
    def test_wraps_with_tags(self):
        out = format_directives([])
        assert out.startswith("<user-directives>")
        assert out.endswith("</user-directives>")

    def test_includes_content_and_timestamp(self):
        d = SimpleNamespace(created_at="2024-01-01", content="use A-share")
        out = format_directives([d])
        assert "use A-share" in out
        assert "2024-01-01" in out

    def test_newlines_flattened(self):
        d = SimpleNamespace(created_at="t", content="line1\nline2")
        out = format_directives([d])
        assert "\nline2" not in out
        assert "line1 line2" in out

    def test_multiple_directives_all_listed(self):
        ds = [
            SimpleNamespace(created_at="t1", content="a"),
            SimpleNamespace(created_at="t2", content="b"),
        ]
        out = format_directives(ds)
        assert "- [t1] a" in out
        assert "- [t2] b" in out


class TestEmitWithTrace:
    def test_emits_event(self):
        emitter = MagicMock()
        emit_with_trace(emitter, "sess", "ev", {"x": 1})
        emitter.emit.assert_called_once()
        args = emitter.emit.call_args[0]
        assert args[0] == "sess"
        assert args[1] == "ev"

    def test_data_keys_merged(self):
        emitter = MagicMock()
        emit_with_trace(emitter, "sess", "ev", {"study_id": "s9", "x": 1})
        data = emitter.emit.call_args[0][2]
        assert data["x"] == 1
        assert "trace_id" in data  # decoration always added

    def test_emitter_failure_swallowed(self):
        emitter = MagicMock()
        emitter.emit.side_effect = RuntimeError("sse down")
        emit_with_trace(emitter, "s", "e", {})  # must not raise
