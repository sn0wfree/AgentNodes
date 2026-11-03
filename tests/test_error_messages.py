"""Tests for error message handling in SessionService.

Covers:
- _friendly_error_text helper maps error details to user-friendly messages
- _row_to_message builds text part for error messages
- Error message type is preserved through persist + load
"""

from __future__ import annotations

from strategy_research.interfaces.api.session.service import SessionService, _friendly_error_text


class TestFriendlyErrorText:
    def test_rate_limit(self):
        assert "频率过高" in _friendly_error_text("LLMRateLimitError: rate limited (429)")
        assert "频率过高" in _friendly_error_text("429 Too Many Requests")
        assert "频率过高" in _friendly_error_text("too many requests")

    def test_timeout(self):
        assert "超时" in _friendly_error_text("LLMTimeoutError: timed out after 60s")
        assert "超时" in _friendly_error_text("request timeout")

    def test_auth(self):
        assert "鉴权" in _friendly_error_text("LLMAuthError: auth failed (401)")
        assert "鉴权" in _friendly_error_text("403 forbidden")

    def test_quota(self):
        assert "配额" in _friendly_error_text("LLMQuotaError: quota exceeded")
        assert "配额" in _friendly_error_text("balance insufficient")

    def test_server_error(self):
        assert "不可用" in _friendly_error_text("LLMServerError: server error (500)")
        assert "不可用" in _friendly_error_text("502 bad gateway")
        assert "不可用" in _friendly_error_text("503 service unavailable")

    def test_unknown_error(self):
        result = _friendly_error_text("some random error")
        assert result.startswith("⚠️")
        assert "失败" in result

    def test_empty_input(self):
        result = _friendly_error_text("")
        assert result.startswith("⚠️")

    def test_none_input(self):
        result = _friendly_error_text(None)  # type: ignore
        assert result.startswith("⚠️")


class TestErrorMessagePersistence:
    def test_row_to_message_error_builds_text_part(self):
        """_row_to_message builds text part from content for error messages."""
        from strategy_research.interfaces.api.routers.web_session import _row_to_message

        row = {
            "id": "msg-1",
            "session_id": "sess-1",
            "role": "assistant",
            "content": "⚠️ 模型请求频率过高",
            "parts_json": None,
            "tool_call_id": None,
            "created_at": 1234567890.0,
            "metadata_json": '{"status": "error", "details": "LLMRateLimitError: 429"}',
            "message_type": "error",
        }
        msg = _row_to_message(row)
        assert msg["message_type"] == "error"
        assert msg["role"] == "assistant"
        assert msg["content"] == "⚠️ 模型请求频率过高"
        assert msg["metadata"]["status"] == "error"
        assert msg["metadata"]["details"] == "LLMRateLimitError: 429"
        assert msg["parts"] is not None
        assert len(msg["parts"]) == 1
        assert msg["parts"][0]["type"] == "text"
        assert msg["parts"][0]["text"] == "⚠️ 模型请求频率过高"

    def test_row_to_message_user_still_builds_text_part(self):
        """Regression: user messages still get text parts from content."""
        from strategy_research.interfaces.api.routers.web_session import _row_to_message

        row = {
            "id": "msg-2",
            "session_id": "sess-1",
            "role": "user",
            "content": "hello world",
            "parts_json": None,
            "tool_call_id": None,
            "created_at": 1234567890.0,
            "metadata_json": None,
            "message_type": "user",
        }
        msg = _row_to_message(row)
        assert msg["message_type"] == "user"
        assert msg["parts"] is not None
        assert len(msg["parts"]) == 1
        assert msg["parts"][0]["text"] == "hello world"

    def test_row_to_message_assistant_no_parts(self):
        """Assistant messages without parts stay empty (streaming fills them)."""
        from strategy_research.interfaces.api.routers.web_session import _row_to_message

        row = {
            "id": "msg-3",
            "session_id": "sess-1",
            "role": "assistant",
            "content": "some content",
            "parts_json": None,
            "tool_call_id": None,
            "created_at": 1234567890.0,
            "metadata_json": None,
            "message_type": "assistant",
        }
        msg = _row_to_message(row)
        assert msg["message_type"] == "assistant"
        assert msg["parts"] is None or len(msg["parts"]) == 0


class TestAssistantMessageSSEEvent:
    """Verify the service emits 'assistant_message' SSE on error path so
    the frontend can display the error bubble in real-time."""

    def test_error_result_emits_assistant_message_event(self, monkeypatch, tmp_path):
        """When the agent loop returns finished_reason='error', the
        service must emit an assistant_message SSE event with
        message_type='error' so the frontend displays the bubble live.
        """
        import asyncio
        import sqlite3
        import time

        from tests._fixtures.legacy_events import EventBus
        from strategy_research.interfaces.api.session.models import Attempt
        from strategy_research.interfaces.api.session.service import SessionService
        from strategy_research.interfaces.api.session.store import SessionStore

        # Track emitted events
        emitted = []
        bus = EventBus()
        original_emit = bus.emit
        def track_emit(session_id, event, data):
            emitted.append((event, data))
            return original_emit(session_id, event, data)
        bus.emit = track_emit  # type: ignore

        # Set up minimal DB
        from strategy_research.interfaces.api.routers.web_session import _ensure_schema
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        _ensure_schema(conn)
        # Create session row directly (no SessionStore.create_session API)
        now = time.time()
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
            "starred, tags_json, message_count, archived) "
            "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
            ("sess-err-sse", "anonymous", "test", now, now),
        )
        conn.commit()
        conn.close()

        store = SessionStore(db_path)
        attempt = Attempt(session_id="sess-err-sse", prompt="hi", message_id="msg-err")
        store.create_attempt(attempt)

        service = SessionService(store=store, event_bus=bus)

        # Stub _run_with_agent to return error
        async def fake_run_with_agent(**kwargs):
            return {
                "status": "empty",
                "content": "",
                "run_dir": None,
                "iterations": 1,
                "tool_calls_made": 0,
                "finished_reason": "error",
                "error": "LLMRateLimitError: rate limited (429)",
                "metrics": {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
            }
        service._run_with_agent = fake_run_with_agent  # type: ignore

        # Call _run_attempt
        async def run():
            await service._run_attempt(
                session_id="sess-err-sse",
                attempt=attempt,
                model="test-model",
                max_iterations=1,
                system_prompt="",
                allow_shell_tools=False,
            )

        asyncio.run(run())

        # Verify the error path: mark_failed, error message, assistant_message event
        assert attempt.status.value == "failed"
        assert "429" in attempt.error or "rate" in attempt.error.lower()

        # Find the assistant_message event
        assistant_events = [e for e in emitted if e[0] == "assistant_message"]
        assert len(assistant_events) == 1, f"Expected 1 assistant_message, got {len(assistant_events)}"
        event_name, event_data = assistant_events[0]
        assert event_data["message_type"] == "error"
        assert "模型请求" in event_data["content"] or "频率" in event_data["content"]
        assert event_data["metadata"]["status"] == "error"
        assert "LLMRateLimitError" in event_data["metadata"]["details"]

    def test_friendly_error_text_for_429(self):
        """Direct test: 429 should produce friendly Chinese text."""
        friendly = _friendly_error_text("LLMRateLimitError: rate limited (429): {...}")
        assert "频率过高" in friendly


class TestRunWithAgentCfgParam:
    """Regression tests for _run_with_agent cfg parameter.

    Bug: Phase 1 compaction filter changes accidentally left _run_with_agent
    using a `cfg` variable that was never defined in its scope. The old
    stub-based test in TestAssistantMessageSSEEvent didn't catch this
    because it completely replaced _run_with_agent.

    Fix: Pass cfg as a keyword arg from _run_attempt to _run_with_agent.
    """

    def test_run_with_agent_signature_accepts_cfg(self):
        """_run_with_agent must accept cfg as a keyword argument."""
        import inspect
        sig = inspect.signature(SessionService._run_with_agent)
        assert "cfg" in sig.parameters, (
            "Regression: _run_with_agent lost the cfg parameter. "
            "Without it, line 614 raises NameError."
        )

    def test_run_with_agent_cfg_is_required(self):
        """_run_with_agent cfg parameter should not have a default.

        Making it required ensures callers always pass it explicitly.
        """
        import inspect
        sig = inspect.signature(SessionService._run_with_agent)
        cfg_param = sig.parameters["cfg"]
        # cfg should be required (no default value)
        assert cfg_param.default is inspect.Parameter.empty, (
            "cfg should be required (no default) so all callers pass it"
        )

    def test_run_attempt_passes_cfg_to_run_with_agent(self, monkeypatch):
        """_run_attempt should pass cfg=cfg when calling _run_with_agent.

        This is the critical regression test: before the fix,
        _run_with_agent would raise NameError because cfg was undefined.
        """
        import asyncio
        import sqlite3

        # Set up minimal DB
        import tempfile
        import time

        from strategy_research.interfaces.api.routers.web_session import _ensure_schema
        from tests._fixtures.legacy_events import EventBus
        from strategy_research.interfaces.api.session.models import Attempt
        from strategy_research.interfaces.api.session.store import SessionStore
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = f"{tmpdir}/test.db"
            conn = sqlite3.connect(db_path)
            _ensure_schema(conn)
            now = time.time()
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
                "starred, tags_json, message_count, archived) "
                "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
                ("sess-1", "u1", "t", now, now),
            )
            conn.commit()
            conn.close()

            store = SessionStore(db_path)
            attempt = Attempt(session_id="sess-1", prompt="hi", message_id="msg-1")
            store.create_attempt(attempt)
            bus = EventBus()
            service = SessionService(store=store, event_bus=bus)

            # Capture the cfg passed to _run_with_agent
            captured = {}

            async def mock_run_with_agent(*, cfg, **kwargs):
                captured["cfg"] = cfg
                captured["kwargs"] = kwargs
                return {
                    "status": "empty",
                    "content": "",
                    "error": None,
                    "iterations": 0,
                    "tool_calls_made": 0,
                    "finished_reason": "error",
                    "metrics": {},
                }

            service._run_with_agent = mock_run_with_agent

            # Build cfg same way _run_attempt does
            from strategy_research.interfaces.api.routers.chat import _build_llm_config
            cfg = _build_llm_config()

            # Call _run_attempt and verify cfg is passed
            async def run():
                await service._run_attempt(
                    session_id="sess-1",
                    attempt=attempt,
                    model=None,
                    max_iterations=1,
                    system_prompt="",
                    allow_shell_tools=False,
                )

            # Should NOT raise NameError
            asyncio.run(run())

            # Verify cfg was passed correctly
            assert "cfg" in captured, "_run_with_agent must receive cfg kwarg"
            assert captured["cfg"] is not None, "cfg should not be None"
            # The cfg should be the same instance from _build_llm_config()
            assert captured["cfg"] is cfg or isinstance(captured["cfg"], type(cfg))
