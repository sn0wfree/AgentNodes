"""Tests for TraceProjection (Phase A2).

Verifies that ``GET /session/{id}/trace`` — via TraceProjection — derives
``llm_request`` records from the event_log (single source of truth) and
reconstructs large offloaded fields (system_prompt, tools_schema) from
their sidecar blobs, so a separate trace.jsonl is no longer required.
"""

from __future__ import annotations

from agentnodes.kernel.agent.event_store import EventStore


def _big(n: int) -> str:
    return "P" * n


class _Attempt:
    attempt_id = "att-1"
    message_id = "msg-1"
    session_id = "sess-1"


def _emit_llm_request(
    store: EventStore,
    *,
    system_prompt: str,
    tools_schema: str,
    iteration: int = 1,
) -> None:
    """Emit an llm_request through the real forwarder (so offload applies)."""
    from agentnodes.interfaces.api.session.service import _LoopEventForwarder
    from agentnodes.foundation.llm import LLMConfig

    fwd = _LoopEventForwarder(
        service=None,
        attempt=_Attempt(),
        accumulated_parts=[],
        event_bus=store,
        cfg=LLMConfig(api_key="sk-test", model="fake-model"),
    )
    fwd("llm_request", {
        "type": "llm_request",
        "iteration": iteration,
        "session_id": "sess-1",
        "history_count": 2,
        "system_prompt_len": len(system_prompt),
        "system_prompt": system_prompt,
        "tools_schema": tools_schema,
    })


def _store(tmp_path) -> EventStore:
    return EventStore(db_path=tmp_path / "sessions.db")


def test_projection_reconstructs_offloaded_fields(tmp_path, monkeypatch) -> None:
    """Large system_prompt / tools_schema are offloaded at emit time and
    reconstructed by the projection from the sidecar blobs."""
    monkeypatch.setenv("AN_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    store = _store(tmp_path)

    big_prompt = _big(5000)
    big_tools = "[" + ",".join(f"t{i}" for i in range(3000)) + "]"
    _emit_llm_request(store, system_prompt=big_prompt, tools_schema=big_tools)

    from agentnodes.interfaces.api.session.trace_projection import TraceProjection

    records = TraceProjection(store).project("sess-1")
    assert len(records) == 1
    r = records[0]
    assert r["type"] == "llm_request"
    assert r["system_prompt"] == big_prompt
    assert r["tools_schema"] == big_tools


def test_projection_keeps_small_fields_inline(tmp_path) -> None:
    """Fields under the threshold stay inline and pass through unchanged."""
    store = _store(tmp_path)
    _emit_llm_request(store, system_prompt="short sys", tools_schema="[]")

    from agentnodes.interfaces.api.session.trace_projection import TraceProjection

    records = TraceProjection(store).project("sess-1")
    assert len(records) == 1
    r = records[0]
    assert r["system_prompt"] == "short sys"
    assert r["tools_schema"] == "[]"
    assert "system_prompt_path" not in r


def test_projection_orders_and_limits(tmp_path) -> None:
    """Records come back append-ordered and truncated to the last ``limit``."""
    store = _store(tmp_path)
    for i in range(1, 4):
        _emit_llm_request(store, system_prompt=f"sys-{i}", tools_schema="[]", iteration=i)

    from agentnodes.interfaces.api.session.trace_projection import TraceProjection

    records = TraceProjection(store).project("sess-1", limit=2)
    assert [r["iteration"] for r in records] == [2, 3]


def test_projection_type_filter(tmp_path, monkeypatch) -> None:
    """A ``types`` allowlist filters which event types are returned."""
    monkeypatch.setenv("AN_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    store = _store(tmp_path)
    _emit_llm_request(store, system_prompt=_big(5000), tools_schema="[]")
    # A non-trace event (text_delta) should be excluded by the default
    # curated vocabulary; explicit types still wins.
    store.emit("sess-1", "text_delta", {"delta": "hi"})
    store.emit("sess-1", "tool_result", {"name": "ls", "result": "ok"})

    from agentnodes.interfaces.api.session.trace_projection import TraceProjection

    recs = TraceProjection(store).project("sess-1")
    assert [r["type"] for r in recs] == ["llm_request", "tool_result"]

    recs2 = TraceProjection(store).project("sess-1", types="tool_result")
    assert [r["type"] for r in recs2] == ["tool_result"]

    # text_delta excluded by default (raw delta, not trajectory-level).
    assert "text_delta" not in [r["type"] for r in recs]


def test_projection_empty_session(tmp_path) -> None:
    """A session with no events yields no records."""
    from agentnodes.interfaces.api.session.trace_projection import TraceProjection

    assert TraceProjection(_store(tmp_path)).project("sess-missing") == []


def test_projection_reconstructs_llm_response_content(tmp_path, monkeypatch) -> None:
    """A large llm_response content offloaded by the forwarder is reconstructed
    by the projection from the sidecar blob."""
    monkeypatch.setenv("AN_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    store = _store(tmp_path)

    from agentnodes.interfaces.api.session.service import _LoopEventForwarder
    from agentnodes.foundation.llm import LLMConfig

    fwd = _LoopEventForwarder(
        service=None, attempt=_Attempt(), accumulated_parts=[],
        event_bus=store, cfg=LLMConfig(api_key="sk-test", model="fake-model"),
    )
    big_content = "A" * 5000
    fwd("llm_response", {
        "type": "llm_response", "iteration": 1, "finish_reason": "stop",
        "has_tool_calls": False, "tool_call_count": 0,
        "content": big_content, "content_preview": big_content[:200],
    })

    from agentnodes.interfaces.api.session.trace_projection import TraceProjection

    records = TraceProjection(store).project("sess-1")
    assert len(records) == 1
    assert records[0]["type"] == "llm_response"
    assert records[0]["content"] == big_content


def test_projection_no_offload_when_missing_blob(tmp_path, monkeypatch) -> None:
    """A dangling ``_path`` reference (blob missing) is skipped, not fatal."""
    monkeypatch.setenv("AN_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    store = _store(tmp_path)
    _emit_llm_request(store, system_prompt=_big(5000), tools_schema="[]")

    # Remove the sidecar blob so reconstruction has nothing to read.
    blob_dir = tmp_path / "trace-blobs"
    for f in blob_dir.glob("*.txt"):
        f.unlink()

    from agentnodes.interfaces.api.session.trace_projection import TraceProjection

    records = TraceProjection(store).project("sess-1")
    assert len(records) == 1
    assert "system_prompt" not in records[0]
