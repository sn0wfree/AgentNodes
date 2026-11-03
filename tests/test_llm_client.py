"""Tests for OpenAI-compat client + parser + errors."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from strategy_research.foundation.llm import (
    LLMAuthError,
    LLMConfig,
    LLMConfigError,
    LLMError,
    LLMMalformedResponseError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    OpenAICompatClient,
)
from strategy_research.foundation.llm import openai_client as oc_mod
from strategy_research.foundation.llm.parser import (
    parse_chat_response,
    parse_stream_chunk,
    parse_tool_arguments,
)

# ── Helpers ──────────────────────────────────────────────────────────


def make_response(status: int = 200, json_body: Any = None, text: str | None = None) -> httpx.Response:
    if json_body is not None:
        return httpx.Response(status, json=json_body)
    return httpx.Response(status, text=text or "")


def mock_client(handler) -> OpenAICompatClient:
    """Create an OpenAICompatClient whose _request_with_retry uses the mock handler.

    The retry/status logic still runs (LLMAuthError/Retry/etc.).
    """
    cfg = LLMConfig(api_key="sk-test", model="gpt-4o-mini")
    client = OpenAICompatClient(cfg)
    transport = httpx.MockTransport(handler)

    def mock_request(payload, stream=False, adapter=None):
        attempt = 0
        last_resp = None
        while attempt <= client.config.max_retries:
            with httpx.Client(transport=transport) as c:
                response = c.post(
                    client._chat_url(),
                    json=payload,
                    headers=oc_mod._build_headers(client.config, adapter=oc_mod.get_provider(client.config.provider)),
                )
            if response.status_code < 400:
                return response
            if not oc_mod._is_retryable_status(response.status_code):
                oc_mod._raise_for_status(response, adapter=oc_mod.get_provider(cfg.provider))
            time.sleep(oc_mod._backoff_delay(attempt, client.config.retry_backoff_s))
            attempt += 1
            last_resp = response
        if last_resp is not None:
            oc_mod._raise_for_status(last_resp, adapter=oc_mod.get_provider(client.config.provider))
        raise LLMError("exhausted")

    client._request_with_retry = mock_request
    return client


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Make all retry backoffs near-zero so tests are fast."""
    monkeypatch.setattr(oc_mod, "_backoff_delay", lambda a, b: 0.001)


# ── Basic chat ───────────────────────────────────────────────────────


class TestBasicChat:
    def test_simple_text_response(self):
        def h(req):
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "hello"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            })
        c = mock_client(h)
        r = c.chat([{"role": "user", "content": "hi"}])
        assert r.content == "hello"
        assert r.finish_reason == "stop"
        assert r.usage["total_tokens"] == 8
        assert not r.has_tool_calls()

    def test_authorization_header(self):
        captured = []
        def h(req):
            captured.append(req.headers.get("authorization"))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        c = mock_client(h)
        c.chat([{"role": "user", "content": "hi"}])
        assert captured[0] == "Bearer sk-test"

    def test_chat_url_uses_base_url(self):
        cap = []
        def h(req):
            cap.append(str(req.url))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        cfg = LLMConfig(api_key="sk-x", base_url="https://api.deepseek.com/v1",
                        model="deepseek-chat")
        c = OpenAICompatClient(cfg)
        c._request_with_retry = lambda p, stream=False, adapter=None: httpx.Client(
            transport=httpx.MockTransport(h)
        ).post(c._chat_url(), json=p, headers=oc_mod._build_headers(c.config, adapter=oc_mod.get_provider(c.config.provider)))
        c.chat([{"role": "user", "content": "q"}])
        assert cap[0] == "https://api.deepseek.com/v1/chat/completions"

    def test_trailing_slash_in_base_url(self):
        cap = []
        def h(req):
            cap.append(str(req.url))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        cfg = LLMConfig(api_key="sk-x", base_url="https://api.example.com/v1/",
                        model="x")
        c = OpenAICompatClient(cfg)
        c._request_with_retry = lambda p, stream=False, adapter=None: httpx.Client(
            transport=httpx.MockTransport(h)
        ).post(c._chat_url(), json=p, headers=oc_mod._build_headers(c.config, adapter=oc_mod.get_provider(c.config.provider)))
        c.chat([{"role": "user", "content": "q"}])
        assert cap[0] == "https://api.example.com/v1/chat/completions"


# ── Tool calls ───────────────────────────────────────────────────────


class TestToolCalls:
    def test_tool_call_response(self):
        def h(req):
            return make_response(200, {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "c1", "type": "function",
                            "function": {"name": "read",
                                         "arguments": '{"path": "/foo"}'},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            })
        c = mock_client(h)
        r = c.chat(
            [{"role": "user", "content": "go"}],
            tools=[{"type": "function", "function": {"name": "read"}}],
        )
        assert r.has_tool_calls()
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].id == "c1"
        assert r.tool_calls[0].name == "read"
        assert r.tool_calls[0].arguments == {"path": "/foo"}
        assert r.finish_reason == "tool_calls"

    def test_multiple_tool_calls(self):
        def h(req):
            return make_response(200, {
                "choices": [{
                    "message": {"role": "assistant", "content": None,
                                "tool_calls": [
                                    {"id": "c1", "type": "function",
                                     "function": {"name": "a", "arguments": "{}"}},
                                    {"id": "c2", "type": "function",
                                     "function": {"name": "b", "arguments": '{"x":1}'}},
                                ]},
                    "finish_reason": "tool_calls",
                }],
            })
        c = mock_client(h)
        r = c.chat([{"role": "user", "content": "go"}],
                   tools=[{"type": "function", "function": {"name": "a"}}])
        assert len(r.tool_calls) == 2
        assert r.tool_calls[0].name == "a"
        assert r.tool_calls[1].arguments == {"x": 1}

    def test_tools_sent_in_payload(self):
        cap = []
        def h(req):
            cap.append(json.loads(req.content))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        c = mock_client(h)
        c.chat(
            [{"role": "user", "content": "q"}],
            tools=[{"type": "function", "function": {"name": "foo"}}],
        )
        payload = cap[0]
        assert "tools" in payload
        assert payload["tool_choice"] == "auto"

    def test_tool_choice_passed(self):
        cap = []
        def h(req):
            cap.append(json.loads(req.content))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        c = mock_client(h)
        c.chat([{"role": "user", "content": "q"}],
               tools=[{"type": "function", "function": {"name": "foo"}}],
               tool_choice="required")
        assert cap[0]["tool_choice"] == "required"


# ── Sampling params ─────────────────────────────────────────────────


class TestSamplingParams:
    def test_default_sampling_in_payload(self):
        cap = []
        def h(req):
            cap.append(json.loads(req.content))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        c = mock_client(h)
        c.chat([{"role": "user", "content": "q"}])
        p = cap[0]
        assert p["temperature"] == 0.7
        assert p["model"] == "gpt-4o-mini"

    def test_call_time_override_temperature(self):
        cap = []
        def h(req):
            cap.append(json.loads(req.content))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        c = mock_client(h)
        c.chat([{"role": "user", "content": "q"}], temperature=0.1)
        assert cap[0]["temperature"] == 0.1

    def test_call_time_override_model(self):
        cap = []
        def h(req):
            cap.append(json.loads(req.content))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        c = mock_client(h)
        c.chat([{"role": "user", "content": "q"}], model="gpt-4o")
        assert cap[0]["model"] == "gpt-4o"

    def test_seed_in_payload(self):
        cap = []
        def h(req):
            cap.append(json.loads(req.content))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        cfg = LLMConfig(api_key="sk", seed=42, temperature=0.0)
        c = OpenAICompatClient(cfg)
        c._request_with_retry = lambda p, stream=False, adapter=None: httpx.Client(
            transport=httpx.MockTransport(h)
        ).post(c._chat_url(), json=p, headers=oc_mod._build_headers(c.config, adapter=oc_mod.get_provider(c.config.provider)))
        c.chat([{"role": "user", "content": "q"}])
        assert cap[0]["seed"] == 42

    def test_stop_in_payload(self):
        cap = []
        def h(req):
            cap.append(json.loads(req.content))
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "x"},
                             "finish_reason": "stop"}],
            })
        cfg = LLMConfig(api_key="sk", stop=("END",))
        c = OpenAICompatClient(cfg)
        c._request_with_retry = lambda p, stream=False, adapter=None: httpx.Client(
            transport=httpx.MockTransport(h)
        ).post(c._chat_url(), json=p, headers=oc_mod._build_headers(c.config, adapter=oc_mod.get_provider(c.config.provider)))
        c.chat([{"role": "user", "content": "q"}])
        assert cap[0]["stop"] == ["END"]


# ── Error handling ──────────────────────────────────────────────────


class TestErrors:
    def test_401_raises_auth_error(self):
        def h(req): return make_response(401, {"error": "invalid"})
        c = mock_client(h)
        with pytest.raises(LLMAuthError):
            c.chat([{"role": "user", "content": "x"}])

    def test_403_raises_auth_error(self):
        def h(req): return make_response(403, {"error": "forbidden"})
        c = mock_client(h)
        with pytest.raises(LLMAuthError):
            c.chat([{"role": "user", "content": "x"}])

    def test_429_retries_then_succeeds(self):
        n = [0]
        def h(req):
            n[0] += 1
            if n[0] < 3:
                return make_response(429, {"error": "rate"})
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}],
            })
        c = mock_client(h)
        r = c.chat([{"role": "user", "content": "x"}])
        assert r.content == "ok"
        assert n[0] == 3  # 2 failures + 1 success

    def test_429_exhausts_raises_rate_limit_error(self):
        def h(req): return make_response(429, {"error": "rate"})
        c = mock_client(h)
        with pytest.raises((LLMRateLimitError, LLMServerError)):
            c.chat([{"role": "user", "content": "x"}])

    def test_500_retries_then_succeeds(self):
        n = [0]
        def h(req):
            n[0] += 1
            if n[0] == 1:
                return make_response(500, {"error": "server"})
            return make_response(200, {
                "choices": [{"message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}],
            })
        c = mock_client(h)
        r = c.chat([{"role": "user", "content": "x"}])
        assert r.content == "ok"
        assert n[0] == 2

    def test_503_raises_server_error(self):
        def h(req): return make_response(503, {"error": "unavailable"})
        c = mock_client(h)
        with pytest.raises((LLMServerError, LLMRateLimitError)):
            c.chat([{"role": "user", "content": "x"}])

    def test_400_raises_llm_error(self):
        def h(req): return make_response(400, {"error": "bad request"})
        c = mock_client(h)
        with pytest.raises(LLMError):
            c.chat([{"role": "user", "content": "x"}])

    def test_malformed_json_raises(self):
        def h(req): return make_response(200, text="not json{")
        c = mock_client(h)
        with pytest.raises(LLMMalformedResponseError):
            c.chat([{"role": "user", "content": "x"}])

    def test_missing_api_key_raises_config_error(self):
        c = OpenAICompatClient(LLMConfig())
        with pytest.raises(LLMConfigError, match="API key"):
            c.chat([{"role": "user", "content": "x"}])

    def test_404_does_not_retry(self):
        n = [0]
        def h(req):
            n[0] += 1
            return make_response(404, {"error": "not found"})
        c = mock_client(h)
        with pytest.raises(LLMError):
            c.chat([{"role": "user", "content": "x"}])
        assert n[0] == 1  # no retries on 404


# ── with_config ──────────────────────────────────────────────────────


class TestWithConfig:
    def test_with_config_returns_new_instance(self):
        c1 = OpenAICompatClient(LLMConfig(api_key="sk", temperature=0.7))
        c2 = c1.with_config(temperature=0.1)
        assert c1.config.temperature == 0.7
        assert c2.config.temperature == 0.1
        assert c2.config.api_key == "sk"

    def test_with_config_multiple_fields(self):
        c1 = OpenAICompatClient(LLMConfig(api_key="sk"))
        c2 = c1.with_config(temperature=0.2, model="x-model", max_tokens=1024)
        assert c2.config.temperature == 0.2
        assert c2.config.model == "x-model"
        assert c2.config.max_tokens == 1024


# ── Streaming ────────────────────────────────────────────────────────


class TestStreaming:
    def _stream_client(self, lines: list[str]) -> OpenAICompatClient:
        """Create a client with mocked streaming endpoint."""
        cfg = LLMConfig(api_key="sk", model="gpt-4o-mini")
        c = OpenAICompatClient(cfg)

        def stream_handler(request: httpx.Request) -> httpx.Response:
            # Return a streaming response with given SSE lines
            body = "\n".join(lines).encode("utf-8")
            return httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/event-stream"},
            )

        # Patch stream to use mock
        def mock_stream(messages, **kwargs):
            stream_handler
            # Use httpx.MockTransport for stream method
            with httpx.Client(
                transport=httpx.MockTransport(stream_handler),
                timeout=cfg.timeout_s,
            ) as client:
                with client.stream(
                    "POST",
                    cfg.base_url + "/chat/completions",
                    json={"model": cfg.model, "messages": messages, "stream": True},
                    headers=oc_mod._build_headers(cfg, adapter=oc_mod.get_provider(cfg.provider)),
                ) as response:
                    oc_mod._raise_for_status(response, adapter=oc_mod.get_provider(cfg.provider))
                    for line in response.iter_lines():
                        chunk = parse_stream_chunk(line)
                        if chunk is not None:
                            yield chunk
                            if chunk.finish_reason:
                                return

        c.stream = mock_stream
        return c

    def test_stream_basic_text(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"hello "}}]}',
            'data: {"choices":[{"delta":{"content":"world"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            'data: [DONE]',
        ]
        c = self._stream_client(lines)
        chunks = list(c.stream([{"role": "user", "content": "hi"}]))
        assert "".join(ch.delta_content for ch in chunks) == "hello world"
        # last chunk should have finish_reason
        assert chunks[-1].finish_reason == "stop"

    def test_stream_with_done_sentinel(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"x"}}]}',
            'data: [DONE]',
        ]
        c = self._stream_client(lines)
        chunks = list(c.stream([{"role": "user", "content": "q"}]))
        assert chunks[-1].finish_reason == "stop"

    def test_stream_skips_empty_lines(self):
        lines = [
            "",
            'data: {"choices":[{"delta":{"content":"a"}}]}',
            "",
            'data: {"choices":[{"delta":{"content":"b"},"finish_reason":"stop"}]}',
            "",
        ]
        c = self._stream_client(lines)
        chunks = list(c.stream([{"role": "user", "content": "q"}]))
        assert "".join(ch.delta_content for ch in chunks) == "ab"

    def test_stream_handles_malformed_chunk(self):
        lines = [
            "not a data line",
            'data: {not valid json',
            'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"stop"}]}',
        ]
        c = self._stream_client(lines)
        chunks = list(c.stream([{"role": "user", "content": "q"}]))
        # Malformed lines should be skipped silently
        assert "".join(ch.delta_content for ch in chunks) == "x"


# ── Parser unit tests ────────────────────────────────────────────────


class TestParser:
    def test_parse_basic(self):
        raw = {
            "choices": [{"message": {"role": "assistant", "content": "hi"},
                         "finish_reason": "stop"}],
            "usage": {"total_tokens": 10},
        }
        r = parse_chat_response(raw)
        assert r.content == "hi"
        assert r.finish_reason == "stop"

    def test_parse_missing_choices(self):
        with pytest.raises(LLMMalformedResponseError):
            parse_chat_response({})

    def test_parse_empty_choices(self):
        with pytest.raises(LLMMalformedResponseError):
            parse_chat_response({"choices": []})

    def test_parse_not_dict(self):
        with pytest.raises(LLMMalformedResponseError):
            parse_chat_response([1, 2, 3])

    def test_parse_tool_calls_invalid_type_warns(self):
        # tool_calls as string → silently skipped, no crash
        raw = {
            "choices": [{"message": {"role": "assistant", "content": "x",
                                     "tool_calls": "garbage"}}],
        }
        r = parse_chat_response(raw)
        assert r.tool_calls == []

    def test_parse_tool_args_standard_json(self):
        assert parse_tool_arguments('{"a":1}') == {"a": 1}

    def test_parse_tool_args_fenced_json(self):
        assert parse_tool_arguments('```json\n{"a":2}\n```') == {"a": 2}

    def test_parse_tool_args_strip_non_json(self):
        assert parse_tool_arguments('Output: {"a":3}') == {"a": 3}

    def test_parse_tool_args_empty(self):
        assert parse_tool_arguments("") == {}

    def test_parse_tool_args_garbage(self):
        assert parse_tool_arguments("complete nonsense") == {}

    def test_parse_tool_args_already_dict(self):
        assert parse_tool_arguments({"a": 4}) == {"a": 4}

    def test_parse_tool_args_non_string_non_dict(self):
        assert parse_tool_arguments(42) == {}

    def test_parse_tool_args_non_dict_json(self):
        assert parse_tool_arguments("[1,2,3]") == {"value": [1, 2, 3]}

    def test_parse_stream_chunk_data_done(self):
        ch = parse_stream_chunk("data: [DONE]")
        assert ch is not None
        assert ch.finish_reason == "stop"

    def test_parse_stream_chunk_text(self):
        ch = parse_stream_chunk('data: {"choices":[{"delta":{"content":"hi"}}]}')
        assert ch is not None
        assert ch.delta_content == "hi"

    def test_parse_stream_chunk_empty(self):
        assert parse_stream_chunk("") is None
        assert parse_stream_chunk("   ") is None

    def test_parse_stream_chunk_not_data(self):
        assert parse_stream_chunk("event: foo") is None

    def test_parse_stream_chunk_malformed(self):
        # malformed JSON returns None
        assert parse_stream_chunk("data: {garbage") is None

    def test_parse_stream_chunk_with_tool_call(self):
        ch = parse_stream_chunk(
            'data: {"choices":[{"delta":{"tool_calls":['
            '{"index":0,"id":"c1","function":{"name":"f","arguments":"{\\"a\\":1}"}}'
            ']}}]}'
        )
        assert ch is not None
        assert len(ch.delta_tool_calls) == 1
        assert ch.delta_tool_calls[0]["id"] == "c1"

    def test_parse_stream_chunk_usage(self):
        ch = parse_stream_chunk(
            'data: {"choices":[],"usage":{"total_tokens":5}}'
        )
        assert ch is not None
        assert ch.usage == {"total_tokens": 5}


# ── Errors classes ──────────────────────────────────────────────────


class TestErrorClasses:
    def test_hierarchy(self):
        assert issubclass(LLMAuthError, LLMError)
        assert issubclass(LLMRateLimitError, LLMError)
        assert issubclass(LLMTimeoutError, LLMError)
        assert issubclass(LLMServerError, LLMError)
        assert issubclass(LLMMalformedResponseError, LLMError)
        assert issubclass(LLMConfigError, LLMError)

    def test_can_be_raised(self):
        with pytest.raises(LLMAuthError):
            raise LLMAuthError("nope")
        with pytest.raises(LLMError):  # caught as base too
            raise LLMRateLimitError("rl")
