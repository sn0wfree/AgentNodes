"""Tests for trace context (ContextVar) + TraceFilter + JsonFormatter.

Validates that:
- ``bind_trace`` sets and restores ContextVars (nested-safe)
- ``TraceFilter`` injects trace fields onto LogRecord
- ``JsonFormatter`` emits valid JSON with trace fields
- ContextVars propagate across ``await`` boundaries (asyncio)
- ``get_trace_context`` snapshots current state
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.foundation.observability.trace import (
    JsonFormatter,
    TraceFilter,
    _round_num,
    _session_id,
    _study_id,
    _trace_id,
    bind_trace,
    get_trace_context,
    new_trace_id,
    setup_trace_logging,
)

# ── bind_trace ──────────────────────────────────────────────────────


class TestBindTrace:
    def test_sets_and_restores(self):
        assert _trace_id.get() is None
        with bind_trace(trace_id="t1", session_id="s1"):
            assert _trace_id.get() == "t1"
            assert _session_id.get() == "s1"
        assert _trace_id.get() is None
        assert _session_id.get() is None

    def test_nested_overrides(self):
        with bind_trace(trace_id="outer"):
            assert _trace_id.get() == "outer"
            with bind_trace(trace_id="inner"):
                assert _trace_id.get() == "inner"
            assert _trace_id.get() == "outer"
        assert _trace_id.get() is None

    def test_partial_bind(self):
        with bind_trace(session_id="s1"):
            with bind_trace(study_id="st-1"):
                assert _session_id.get() == "s1"
                assert _study_id.get() == "st-1"
            assert _study_id.get() is None
            assert _session_id.get() == "s1"

    def test_round_num(self):
        with bind_trace(round_num=7):
            assert _round_num.get() == 7
        assert _round_num.get() is None

    def test_get_trace_context_snapshot(self):
        with bind_trace(trace_id="t1", session_id="s1", study_id="st-1", round_num=3):
            ctx = get_trace_context()
            assert ctx == {
                "trace_id": "t1",
                "session_id": "s1",
                "study_id": "st-1",
                "round_num": 3,
            }

    def test_new_trace_id_unique(self):
        ids = {new_trace_id() for _ in range(100)}
        assert len(ids) == 100  # all unique
        assert all(len(i) == 12 for i in ids)


# ── TraceFilter ─────────────────────────────────────────────────────


class TestTraceFilter:
    def test_filter_injects_fields(self):
        f = TraceFilter()
        rec = logging.LogRecord(
            "test", logging.INFO, __file__, 1, "hello", None, None,
        )
        assert f.filter(rec) is True
        assert rec.trace_id == "-"
        assert rec.session_id == "-"
        assert rec.study_id == "-"
        # 未绑定轮次同样落 "-"（文本格式统一占位；JSON 端按 "-" 省略）
        assert rec.round_num == "-"

    def test_filter_picks_up_context(self):
        f = TraceFilter()
        with bind_trace(trace_id="t1", session_id="s1", study_id="st-1", round_num=2):
            rec = logging.LogRecord(
                "test", logging.INFO, __file__, 1, "hello", None, None,
            )
            f.filter(rec)
            assert rec.trace_id == "t1"
            assert rec.session_id == "s1"
            assert rec.study_id == "st-1"
            assert rec.round_num == 2


# ── JsonFormatter ───────────────────────────────────────────────────


class TestJsonFormatter:
    def test_basic_json_output(self):
        fmt = JsonFormatter(datefmt="%H:%M:%S")
        rec = logging.LogRecord(
            "my.logger", logging.WARNING, __file__, 42,
            "retry failed %d times", (3,), None,
        )
        rec.trace_id = "abc123"
        rec.session_id = "sess-1"
        rec.study_id = "-"
        rec.round_num = None
        line = fmt.format(rec)
        obj = json.loads(line)
        assert obj["level"] == "WARNING"
        assert obj["logger"] == "my.logger"
        assert obj["msg"] == "retry failed 3 times"
        assert obj["trace_id"] == "abc123"
        assert obj["session_id"] == "sess-1"
        # study_id "-" and round_num None should be omitted
        assert "study_id" not in obj
        assert "round_num" not in obj

    def test_no_trace_fields_when_unset(self):
        fmt = JsonFormatter()
        rec = logging.LogRecord(
            "test", logging.INFO, __file__, 1, "plain", None, None,
        )
        rec.trace_id = "-"
        rec.session_id = "-"
        rec.study_id = "-"
        rec.round_num = None
        line = fmt.format(rec)
        obj = json.loads(line)
        assert obj["msg"] == "plain"
        assert "trace_id" not in obj
        assert "session_id" not in obj

    def test_exception_included(self):
        fmt = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            rec = logging.LogRecord(
                "test", logging.ERROR, __file__, 1, "err", None, sys.exc_info(),
            )
            rec.trace_id = "t1"
            rec.session_id = "-"
            rec.study_id = "-"
            rec.round_num = None
        line = fmt.format(rec)
        obj = json.loads(line)
        assert "exc" in obj
        assert "ValueError: boom" in obj["exc"]


# ── asyncio propagation ─────────────────────────────────────────────


class TestAsyncPropagation:
    @pytest.mark.asyncio
    async def test_contextvar_visible_in_await_chain(self):
        with bind_trace(trace_id="async-1"):
            async def inner():
                return _trace_id.get()
            result = await inner()
            assert result == "async-1"

    @pytest.mark.asyncio
    async def test_contextvar_propagates_to_create_task(self):
        with bind_trace(trace_id="task-1", session_id="s1"):
            async def child():
                await asyncio.sleep(0.01)
                return _trace_id.get(), _session_id.get()
            task = asyncio.create_task(child())
            tid, sid = await task
            assert tid == "task-1"
            assert sid == "s1"

    @pytest.mark.asyncio
    async def test_contextvar_not_leaked_after_exit(self):
        with bind_trace(trace_id="temp"):
            pass
        assert _trace_id.get() is None

        async def check():
            return _trace_id.get()
        assert await check() is None


# ── setup_trace_logging integration ─────────────────────────────────


class TestSetupTraceLogging:
    def test_filter_installed_once(self):
        root = logging.getLogger()
        # Count TraceFilters across handlers (defect #17: filters must
        # live on handlers — logger-level filters never see records
        # propagated from child loggers).
        def _count() -> int:
            return sum(
                1
                for h in root.handlers
                for f in h.filters
                if isinstance(f, TraceFilter)
            )

        before = _count()
        setup_trace_logging(json_output=False)
        setup_trace_logging(json_output=False)
        after = _count()
        # Idempotent: every handler ends up with exactly one filter.
        assert after == len(root.handlers)
        assert after >= before

    def test_child_logger_record_carries_trace_fields(self):
        """Regression (defect #17): records from child loggers — which
        bypass root-logger filters by design — must still reach the
        formatter with trace attrs attached."""
        root = logging.getLogger()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter(
            "%(message)s t=%(trace_id)s", "%H:%M:%S",
        ))
        root.addHandler(handler)
        try:
            setup_trace_logging(json_output=False)
            with bind_trace(trace_id="child-17"):
                logging.getLogger("agentnodes.child.defect17").info(
                    "propagated")
            handler.flush()
            assert "child-17" in stream.getvalue()
        finally:
            root.removeHandler(handler)

    def test_json_formatter_applied(self):
        root = logging.getLogger()
        # Save original formatters
        saved = [(h, h.formatter) for h in root.handlers]
        try:
            setup_trace_logging(json_output=True)
            for h in root.handlers:
                assert isinstance(h.formatter, JsonFormatter)
        finally:
            for h, fmt in saved:
                h.setFormatter(fmt)

    def test_text_formatter_when_json_disabled(self):
        root = logging.getLogger()
        saved = [(h, h.formatter) for h in root.handlers]
        try:
            setup_trace_logging(json_output=False)
            for h in root.handlers:
                assert not isinstance(h.formatter, JsonFormatter)
        finally:
            for h, fmt in saved:
                h.setFormatter(fmt)

    def test_json_log_line_has_trace_id(self, caplog):
        setup_trace_logging(json_output=True, log_level=logging.DEBUG)
        logger = logging.getLogger("test.json.trace")

        # caplog's handler bypasses root logger filters, so apply
        # TraceFilter manually to inject trace fields onto each record.
        fmt = JsonFormatter(datefmt="%H:%M:%S")
        tf = TraceFilter()

        with bind_trace(trace_id="cap-1", session_id="cap-sess"):
            with caplog.at_level(logging.DEBUG):
                logger.info("hello trace")
            assert len(caplog.records) == 1
            rec = caplog.records[0]
            tf.filter(rec)  # inject trace fields while context is live
            line = fmt.format(rec)
            obj = json.loads(line)
            assert obj["msg"] == "hello trace"
            assert obj["trace_id"] == "cap-1"
            assert obj["session_id"] == "cap-sess"
