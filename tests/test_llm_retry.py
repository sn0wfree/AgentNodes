"""Tests for LLM retry behavior across chat/achat/stream/astream.

Uses httpx.MockTransport injected via the transport= constructor kwarg.
"""

from __future__ import annotations

import datetime
import email.utils

import httpx
import pytest

from strategy_research.foundation.llm import (
    LLMConfig,
    LLMRateLimitError,
    LLMServerError,
    OpenAICompatClient,
)
from strategy_research.foundation.llm import openai_client as oc_mod

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Make all retry backoffs near-zero so tests are fast."""
    monkeypatch.setattr(oc_mod, "_backoff_delay", lambda a, b: 0.001)


@pytest.fixture()
def fast_config():
    return LLMConfig(
        api_key="sk-test",
        model="gpt-4o-mini",
        max_retries=3,
        retry_backoff_s=0.001,
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _ok_chat_response() -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _sse_lines(content: str = "hello", finish: bool = True) -> list[bytes]:
    delta = {"choices": [{"delta": {"content": content}, "finish_reason": "stop" if finish else None}]}
    lines = [
        f"data: {_json(delta)}\n\n".encode(),
    ]
    if finish:
        lines.append(b"data: [DONE]\n\n")
    return lines


def _json(obj) -> str:
    import json
    return json.dumps(obj)


# ── chat() retry tests ───────────────────────────────────────────────


class TestChatRetry:
    def test_429_retries_then_succeeds(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(429, json={"error": {"message": "rate limit"}})
            return httpx.Response(200, json=_ok_chat_response())

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "hello"
        assert call_count == 3  # 2 failures + 1 success = 3 total attempts

    def test_429_exhausted_raises_rate_limit_error(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, json={"error": {"message": "rate limit"}})

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        with pytest.raises(LLMRateLimitError):
            client.chat([{"role": "user", "content": "hi"}])
        assert call_count == 3  # exactly max_retries total attempts

    def test_500_exhausted_raises_server_error(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(500, text="internal error")

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        with pytest.raises(LLMServerError):
            client.chat([{"role": "user", "content": "hi"}])
        assert call_count == 3

    def test_401_no_retry(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(401, json={"error": {"message": "unauthorized"}})

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        from strategy_research.foundation.llm import LLMAuthError
        with pytest.raises(LLMAuthError):
            client.chat([{"role": "user", "content": "hi"}])
        assert call_count == 1  # no retry on auth errors

    def test_total_attempts_equals_max_retries(self, fast_config):
        """max_retries is the total number of attempts, not retries."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, json={"error": {"message": "rate limit"}})

        cfg = fast_config.with_config(max_retries=5)
        client = OpenAICompatClient(cfg, transport=httpx.MockTransport(handler))
        with pytest.raises(LLMRateLimitError):
            client.chat([{"role": "user", "content": "hi"}])
        assert call_count == 5


# ── achat() retry tests ──────────────────────────────────────────────


class TestAchatRetry:
    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(429, json={"error": {"message": "rate limit"}})
            return httpx.Response(200, json=_ok_chat_response())

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        resp = await client.achat([{"role": "user", "content": "hi"}])
        assert resp.content == "hello"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_429_exhausted_raises_rate_limit(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, json={"error": {"message": "rate limit"}})

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        with pytest.raises(LLMRateLimitError):
            await client.achat([{"role": "user", "content": "hi"}])
        assert call_count == 3


# ── Retry-After tests ────────────────────────────────────────────────


class TestRetryAfter:
    def test_parse_retry_after_seconds(self):
        assert oc_mod._parse_retry_after("10") == 10.0
        assert oc_mod._parse_retry_after("0") == 0.0
        assert oc_mod._parse_retry_after("") is None
        assert oc_mod._parse_retry_after("invalid-stuff") is None

    def test_parse_retry_after_http_date(self):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)
        header = email.utils.formatdate(future.timestamp(), usegmt=True)
        result = oc_mod._parse_retry_after(header)
        assert result is not None
        assert 25 < result < 35  # allow some drift

    def test_chat_honors_retry_after_header(self, fast_config, monkeypatch):
        """Retry delay should be at least Retry-After value."""
        delays = []
        original_compute = oc_mod._compute_retry_delay

        def track_delay(response, attempt, base, **kw):
            d = original_compute(response, attempt, base, **kw)
            delays.append(d)
            return d

        monkeypatch.setattr(oc_mod, "_compute_retry_delay", track_delay)

        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(
                    429,
                    json={"error": {"message": "rate limit"}},
                    headers={"Retry-After": "5"},
                )
            return httpx.Response(200, json=_ok_chat_response())

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "hello"
        assert call_count == 3
        assert len(delays) == 2
        # Both retry delays should be at least 5s (from Retry-After)
        # but capped by 60s and with jitter
        for d in delays:
            assert 3.0 <= d <= 60.0  # jitter down to 70% of 5s = 3.5, allow margin


# ── stream() retry tests ─────────────────────────────────────────────


class TestStreamRetry:
    def test_429_retries_then_succeeds(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(429, json={"error": {"message": "rate limit"}})
            content = b"".join(_sse_lines("hello world"))
            return httpx.Response(200, content=content, headers={"Content-Type": "text/event-stream"})

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        chunks = list(client.stream([{"role": "user", "content": "hi"}]))
        assert any(c.finish_reason for c in chunks)
        assert call_count == 3

    def test_429_exhausted_raises_rate_limit(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, json={"error": {"message": "rate limit"}})

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        with pytest.raises(LLMRateLimitError):
            list(client.stream([{"role": "user", "content": "hi"}]))
        assert call_count == 3

    def test_500_exhausted_raises_server_error(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(500, text="boom")

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        with pytest.raises(LLMServerError):
            list(client.stream([{"role": "user", "content": "hi"}]))
        assert call_count == 3

    def test_total_attempts_equals_max_retries(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, json={"error": {"message": "rate limit"}})

        cfg = fast_config.with_config(max_retries=4)
        client = OpenAICompatClient(cfg, transport=httpx.MockTransport(handler))
        with pytest.raises(LLMRateLimitError):
            list(client.stream([{"role": "user", "content": "hi"}]))
        assert call_count == 4

    def test_401_no_retry(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(401, json={"error": {"message": "unauth"}})

        from strategy_research.foundation.llm import LLMAuthError
        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        with pytest.raises(LLMAuthError):
            list(client.stream([{"role": "user", "content": "hi"}]))
        assert call_count == 1

    def test_success_no_retry(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            content = b"".join(_sse_lines("ok"))
            return httpx.Response(200, content=content, headers={"Content-Type": "text/event-stream"})

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        chunks = list(client.stream([{"role": "user", "content": "hi"}]))
        assert any(c.finish_reason for c in chunks)
        assert call_count == 1


# ── astream() retry tests ────────────────────────────────────────────


class TestAstreamRetry:
    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(429, json={"error": {"message": "rate limit"}})
            content = b"".join(_sse_lines("hello async"))
            return httpx.Response(200, content=content, headers={"Content-Type": "text/event-stream"})

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        chunks = []
        async for chunk in client.astream([{"role": "user", "content": "hi"}]):
            chunks.append(chunk)
        assert any(c.finish_reason for c in chunks)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_429_exhausted_raises_rate_limit(self, fast_config):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, json={"error": {"message": "rate limit"}})

        client = OpenAICompatClient(fast_config, transport=httpx.MockTransport(handler))
        with pytest.raises(LLMRateLimitError):
            async for _ in client.astream([{"role": "user", "content": "hi"}]):
                pass
        assert call_count == 3


# ── mid-stream no-retry tests ────────────────────────────────────────


class TestStreamMidStreamNoRetry:
    """Verify stream() does NOT retry after first chunk is yielded.

    Once `started=True` is set, any further transport error must propagate
    as LLMError to the caller (typically raising a mid-stream error that
    the agent loop surfaces to the user).
    """

    def test_transport_error_after_first_chunk_does_not_retry(self, fast_config):
        # First chunk (a valid SSE delta) followed by disconnect
        first_chunk = b'data: {"choices": [{"delta": {"content": "hi"}, "finish_reason": null}]}\n\n'

        class FailingTransport(httpx.BaseTransport):
            def __init__(self):
                self.calls = 0

            def handle_request(self, request: httpx.Request) -> httpx.Response:
                self.calls += 1

                class _RaisingBody(httpx.SyncByteStream):
                    def __iter__(self):
                        yield first_chunk
                        raise httpx.RemoteProtocolError(
                            "simulated mid-stream disconnect"
                        )

                    def close(self):
                        pass

                return httpx.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    stream=_RaisingBody(),
                )

        transport = FailingTransport()
        client = OpenAICompatClient(fast_config, transport=transport)

        from strategy_research.foundation.llm import LLMError
        with pytest.raises(LLMError):
            for _chunk in client.stream([{"role": "user", "content": "hi"}]):
                pass

        # Critical assertion: only ONE request was made (no retry after
        # first chunk yielded).
        assert transport.calls == 1

    @pytest.mark.asyncio
    async def test_atransport_error_after_first_chunk_does_not_retry(self, fast_config):
        first_chunk = b'data: {"choices": [{"delta": {"content": "hi"}, "finish_reason": null}]}\n\n'

        class FailingAsyncTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.calls = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.calls += 1

                class _RaisingAsyncBody(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        yield first_chunk
                        raise httpx.RemoteProtocolError(
                            "simulated mid-stream async disconnect"
                        )

                    async def aclose(self):
                        pass

                return httpx.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    stream=_RaisingAsyncBody(),
                )

        transport = FailingAsyncTransport()
        client = OpenAICompatClient(fast_config, transport=transport)

        from strategy_research.foundation.llm import LLMError
        with pytest.raises(LLMError):
            async for _ in client.astream([{"role": "user", "content": "hi"}]):
                pass

        assert transport.calls == 1
