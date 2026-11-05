"""Unit tests for openai_client.py helper functions (no HTTP mocking needed)."""

from __future__ import annotations

import datetime
import email.utils
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.foundation.llm import openai_client as oc_mod
from agentnodes.foundation.llm.config import LLMConfig
from agentnodes.foundation.llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMServerError,
)

# Minimal provider adapter so the new ``adapter`` parameters of
# _build_headers / _build_payload get exercised (see openai_client.py).
_FAKE_ADAPTER = mock.Mock()
_FAKE_ADAPTER.custom_headers.return_value = {}
_FAKE_ADAPTER.custom_payload.side_effect = lambda payload, config: payload
_FAKE_ADAPTER.custom_stream_options.return_value = None

class TestIsRetryableStatus(unittest.TestCase):
    """_is_retryable_status: 429 and 5xx are retryable."""

    def test_429_is_retryable(self) -> None:
        self.assertTrue(oc_mod._is_retryable_status(429))

    def test_500_is_retryable(self) -> None:
        self.assertTrue(oc_mod._is_retryable_status(500))

    def test_503_is_retryable(self) -> None:
        self.assertTrue(oc_mod._is_retryable_status(503))

    def test_599_is_retryable(self) -> None:
        self.assertTrue(oc_mod._is_retryable_status(599))

    def test_200_is_not_retryable(self) -> None:
        self.assertFalse(oc_mod._is_retryable_status(200))

    def test_400_is_not_retryable(self) -> None:
        self.assertFalse(oc_mod._is_retryable_status(400))

    def test_401_is_not_retryable(self) -> None:
        self.assertFalse(oc_mod._is_retryable_status(401))

    def test_403_is_not_retryable(self) -> None:
        self.assertFalse(oc_mod._is_retryable_status(403))

    def test_404_is_not_retryable(self) -> None:
        self.assertFalse(oc_mod._is_retryable_status(404))

class TestBackoffDelay(unittest.TestCase):
    """_backoff_delay: base * 2^attempt, capped at 60."""

    def test_attempt_0_base_1(self) -> None:
        self.assertEqual(oc_mod._backoff_delay(0, 1.0), 1.0)

    def test_attempt_1_base_1(self) -> None:
        self.assertEqual(oc_mod._backoff_delay(1, 1.0), 2.0)

    def test_attempt_2_base_1(self) -> None:
        self.assertEqual(oc_mod._backoff_delay(2, 1.0), 4.0)

    def test_attempt_3_base_1(self) -> None:
        self.assertEqual(oc_mod._backoff_delay(3, 1.0), 8.0)

    def test_attempt_10_base_1(self) -> None:
        self.assertEqual(oc_mod._backoff_delay(10, 1.0), 60.0)

    def test_attempt_0_base_2(self) -> None:
        self.assertEqual(oc_mod._backoff_delay(0, 2.0), 2.0)

    def test_attempt_1_base_2(self) -> None:
        self.assertEqual(oc_mod._backoff_delay(1, 2.0), 4.0)

class TestParseRetryAfter(unittest.TestCase):
    """_parse_retry_after: seconds string or HTTP-date."""

    def test_seconds(self) -> None:
        self.assertEqual(oc_mod._parse_retry_after("10"), 10.0)

    def test_zero(self) -> None:
        self.assertEqual(oc_mod._parse_retry_after("0"), 0.0)

    def test_float_seconds(self) -> None:
        self.assertEqual(oc_mod._parse_retry_after("2.5"), 2.5)

    def test_empty_string(self) -> None:
        self.assertIsNone(oc_mod._parse_retry_after(""))

    def test_none(self) -> None:
        self.assertIsNone(oc_mod._parse_retry_after(None))

    def test_invalid_string(self) -> None:
        self.assertIsNone(oc_mod._parse_retry_after("invalid"))

    def test_http_date(self) -> None:
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)
        header = email.utils.formatdate(future.timestamp(), usegmt=True)
        result = oc_mod._parse_retry_after(header)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)
        self.assertLess(result, 60)

    def test_http_date_no_tz(self) -> None:
        dt = datetime.datetime.now() + datetime.timedelta(seconds=10)
        header = email.utils.formatdate(dt.timestamp(), usegmt=True)
        result = oc_mod._parse_retry_after(header)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result, 0)

class TestComputeRetryDelay(unittest.TestCase):
    """_compute_retry_delay: exponential backoff + Retry-After + jitter."""

    def test_basic_exponential(self) -> None:
        import httpx
        resp = httpx.Response(429)
        delay = oc_mod._compute_retry_delay(resp, 0, 1.0, max_backoff=60.0, jitter_fraction=0)
        self.assertEqual(delay, 1.0)

    def test_attempt_one(self) -> None:
        import httpx
        resp = httpx.Response(429)
        delay = oc_mod._compute_retry_delay(resp, 1, 1.0, max_backoff=60.0, jitter_fraction=0)
        self.assertEqual(delay, 2.0)

    def test_capped(self) -> None:
        import httpx
        resp = httpx.Response(429)
        delay = oc_mod._compute_retry_delay(resp, 10, 1.0, max_backoff=60.0, jitter_fraction=0)
        self.assertEqual(delay, 60.0)

    def test_retry_after_honored(self) -> None:
        import httpx
        resp = httpx.Response(429, headers={"Retry-After": "10"})
        delay = oc_mod._compute_retry_delay(resp, 0, 1.0, max_backoff=60.0, jitter_fraction=0)
        self.assertGreaterEqual(delay, 10.0)
        self.assertLessEqual(delay, 60.0)

    def test_retry_after_overrides_exponential(self) -> None:
        import httpx
        resp = httpx.Response(429, headers={"Retry-After": "30"})
        delay = oc_mod._compute_retry_delay(resp, 0, 1.0, max_backoff=60.0, jitter_fraction=0)
        self.assertGreaterEqual(delay, 30.0)

    def test_retry_after_capped(self) -> None:
        import httpx
        resp = httpx.Response(429, headers={"Retry-After": "999"})
        delay = oc_mod._compute_retry_delay(resp, 0, 1.0, max_backoff=60.0, jitter_fraction=0)
        self.assertEqual(delay, 60.0)

    def test_jitter_applied(self) -> None:
        import httpx
        resp = httpx.Response(429)
        delays = set()
        for _ in range(50):
            d = oc_mod._compute_retry_delay(resp, 0, 1.0, max_backoff=60.0, jitter_fraction=0.3)
            delays.add(round(d, 2))
        self.assertGreater(len(delays), 1)

    def test_jitter_fraction_zero(self) -> None:
        import httpx
        resp = httpx.Response(429)
        delay = oc_mod._compute_retry_delay(resp, 0, 1.0, max_backoff=60.0, jitter_fraction=0)
        self.assertEqual(delay, 1.0)

class TestEnsureApiKey(unittest.TestCase):
    """_ensure_api_key: raises LLMConfigError when missing/empty."""

    def test_returns_key(self) -> None:
        cfg = LLMConfig(api_key="sk-test")
        self.assertEqual(oc_mod._ensure_api_key(cfg), "sk-test")

    def test_empty_raises(self) -> None:
        cfg = LLMConfig(api_key="")
        with self.assertRaises(LLMConfigError):
            oc_mod._ensure_api_key(cfg)

    def test_error_message(self) -> None:
        cfg = LLMConfig(api_key="")
        with self.assertRaises(LLMConfigError) as ctx:
            oc_mod._ensure_api_key(cfg)
        self.assertIn("API key", str(ctx.exception))

class TestBuildHeaders(unittest.TestCase):
    """_build_headers: Authorization, Content-Type, Accept."""

    def test_basic_headers(self) -> None:
        cfg = LLMConfig(api_key="sk-test")
        headers = oc_mod._build_headers(cfg, _FAKE_ADAPTER)
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")

class TestBuildPayload(unittest.TestCase):
    """_build_payload: request body construction."""

    def test_minimal(self) -> None:
        cfg = LLMConfig(api_key="sk-test", model="gpt-4o-mini")
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertEqual(payload["model"], "gpt-4o-mini")
        self.assertEqual(len(payload["messages"]), 1)
        self.assertNotIn("stream", payload)

    def test_with_temperature(self) -> None:
        cfg = LLMConfig(api_key="sk-test", temperature=0.5)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertEqual(payload["temperature"], 0.5)

    def test_temperature_none_excluded(self) -> None:
        cfg = LLMConfig(api_key="sk-test", temperature=None)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertNotIn("temperature", payload)

    def test_with_top_p(self) -> None:
        cfg = LLMConfig(api_key="sk-test", top_p=0.9)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertEqual(payload["top_p"], 0.9)

    def test_top_p_default_excluded(self) -> None:
        cfg = LLMConfig(api_key="sk-test", top_p=1.0)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertNotIn("top_p", payload)

    def test_max_tokens_none_auto_fills(self) -> None:
        cfg = LLMConfig(api_key="sk-test", max_tokens=None)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertIn("max_tokens", payload)
        self.assertGreaterEqual(payload["max_tokens"], 256)

    def test_max_tokens_set(self) -> None:
        cfg = LLMConfig(api_key="sk-test", max_tokens=4096)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertEqual(payload["max_tokens"], 4096)

    def test_max_tokens_tiny_raised_to_floor(self) -> None:
        cfg = LLMConfig(api_key="sk-test", max_tokens=16)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertGreaterEqual(payload["max_tokens"], 256)

    def test_overrides_model(self) -> None:
        cfg = LLMConfig(api_key="sk-test", model="gpt-4o-mini")
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {"model": "gpt-4o"}, _FAKE_ADAPTER)
        self.assertEqual(payload["model"], "gpt-4o")

    def test_overrides_temperature(self) -> None:
        cfg = LLMConfig(api_key="sk-test", temperature=0.7)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {"temperature": 0.1}, _FAKE_ADAPTER)
        self.assertEqual(payload["temperature"], 0.1)

    def test_with_tools(self) -> None:
        cfg = LLMConfig(api_key="sk-test")
        tools = [{"type": "function", "function": {"name": "read"}}]
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], tools, None, {}, _FAKE_ADAPTER)
        self.assertIn("tools", payload)
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertTrue(payload["parallel_tool_calls"])

    def test_with_tool_choice(self) -> None:
        cfg = LLMConfig(api_key="sk-test")
        tools = [{"type": "function", "function": {"name": "read"}}]
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], tools, "required", {}, _FAKE_ADAPTER)
        self.assertEqual(payload["tool_choice"], "required")

    def test_frequency_penalty(self) -> None:
        cfg = LLMConfig(api_key="sk-test", frequency_penalty=0.5)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertEqual(payload["frequency_penalty"], 0.5)

    def test_presence_penalty(self) -> None:
        cfg = LLMConfig(api_key="sk-test", presence_penalty=0.5)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertEqual(payload["presence_penalty"], 0.5)

    def test_stop(self) -> None:
        cfg = LLMConfig(api_key="sk-test", stop=("END", "STOP"))
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertEqual(payload["stop"], ["END", "STOP"])

    def test_seed(self) -> None:
        cfg = LLMConfig(api_key="sk-test", seed=42)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertEqual(payload["seed"], 42)

    def test_seed_none_excluded(self) -> None:
        cfg = LLMConfig(api_key="sk-test", seed=None)
        payload = oc_mod._build_payload(cfg, [{"role": "user", "content": "hi"}], None, None, {}, _FAKE_ADAPTER)
        self.assertNotIn("seed", payload)

class TestExtractErrorCode(unittest.TestCase):
    """_extract_error_code: various JSON shapes."""

    def test_error_dict_with_code(self) -> None:
        body = {"error": {"code": "quota_exceeded"}}
        self.assertEqual(oc_mod._extract_error_code(body), "quota_exceeded")

    def test_error_string(self) -> None:
        body = {"error": "invalid_api_key"}
        self.assertEqual(oc_mod._extract_error_code(body), "invalid_api_key")

    def test_top_level_code(self) -> None:
        body = {"code": "rate_limit"}
        self.assertEqual(oc_mod._extract_error_code(body), "rate_limit")

    def test_not_dict_returns_empty(self) -> None:
        self.assertEqual(oc_mod._extract_error_code("string"), "")

    def test_empty_body(self) -> None:
        self.assertEqual(oc_mod._extract_error_code({}), "")

    def test_no_code(self) -> None:
        body = {"error": {"message": "something went wrong"}}
        self.assertEqual(oc_mod._extract_error_code(body), "")

    def test_error_section_empty_string(self) -> None:
        body = {"error": ""}
        self.assertEqual(oc_mod._extract_error_code(body), "")

class TestRaiseForStatus(unittest.TestCase):
    """_raise_for_status: status code -> exception mapping."""

    def test_200_no_error(self) -> None:
        import httpx
        oc_mod._raise_for_status(httpx.Response(200))

    def test_401_raises_auth(self) -> None:
        import httpx
        with self.assertRaises(LLMAuthError):
            oc_mod._raise_for_status(httpx.Response(401, json={"error": "unauth"}))

    def test_403_raises_auth(self) -> None:
        import httpx
        with self.assertRaises(LLMAuthError):
            oc_mod._raise_for_status(httpx.Response(403, json={"error": "forbidden"}))

    def test_429_raises_rate_limit(self) -> None:
        import httpx
        with self.assertRaises(LLMRateLimitError):
            oc_mod._raise_for_status(httpx.Response(429, json={"error": "rate"}))

    def test_500_raises_server_error(self) -> None:
        import httpx
        with self.assertRaises(LLMServerError):
            oc_mod._raise_for_status(httpx.Response(500, json={"error": "server"}))

    def test_503_raises_server_error(self) -> None:
        import httpx
        with self.assertRaises(LLMServerError):
            oc_mod._raise_for_status(httpx.Response(503, json={"error": "unavailable"}))

    def test_400_raises_llm_error(self) -> None:
        import httpx
        with self.assertRaises(LLMError):
            oc_mod._raise_for_status(httpx.Response(400, json={"error": "bad"}))

    def test_response_body_fallback_to_text(self) -> None:
        import httpx
        with self.assertRaises(LLMError) as ctx:
            oc_mod._raise_for_status(httpx.Response(400, text="not json"))
        self.assertIn("not json", str(ctx.exception))

class TestClientUrlAndKwargs(unittest.TestCase):
    """_chat_url and _client_kwargs."""

    def test_chat_url_default(self) -> None:
        cfg = LLMConfig(api_key="sk-test")
        c = oc_mod.OpenAICompatClient(cfg)
        self.assertEqual(c._chat_url(), "https://api.openai.com/v1/chat/completions")

    def test_chat_url_custom_base(self) -> None:
        cfg = LLMConfig(api_key="sk-test", base_url="https://api.deepseek.com/v1")
        c = oc_mod.OpenAICompatClient(cfg)
        self.assertEqual(c._chat_url(), "https://api.deepseek.com/v1/chat/completions")

    def test_chat_url_trailing_slash(self) -> None:
        cfg = LLMConfig(api_key="sk-test", base_url="https://api.example.com/v1/")
        c = oc_mod.OpenAICompatClient(cfg)
        self.assertEqual(c._chat_url(), "https://api.example.com/v1/chat/completions")

    def test_client_kwargs_timeout(self) -> None:
        cfg = LLMConfig(api_key="sk-test", timeout_s=30.0)
        c = oc_mod.OpenAICompatClient(cfg)
        kwargs = c._client_kwargs()
        self.assertEqual(kwargs["timeout"], 30.0)

    def test_client_kwargs_with_proxy(self) -> None:
        cfg = LLMConfig(api_key="sk-test", proxy="http://proxy:8080")
        c = oc_mod.OpenAICompatClient(cfg)
        kwargs = c._client_kwargs()
        self.assertEqual(kwargs["proxy"], "http://proxy:8080")

    def test_client_kwargs_with_transport(self) -> None:
        import httpx
        cfg = LLMConfig(api_key="sk-test")
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        c = oc_mod.OpenAICompatClient(cfg, transport=transport)
        kwargs = c._client_kwargs()
        self.assertIs(kwargs["transport"], transport)

    def test_client_kwargs_no_proxy(self) -> None:
        cfg = LLMConfig(api_key="sk-test")
        c = oc_mod.OpenAICompatClient(cfg)
        kwargs = c._client_kwargs()
        self.assertNotIn("proxy", kwargs)

class TestLLMQuotaError(unittest.TestCase):
    """LLMQuotaError is a distinct exception type."""

    def test_quota_error_is_llm_error(self) -> None:
        from agentnodes.foundation.llm.errors import LLMQuotaError
        self.assertTrue(issubclass(LLMQuotaError, LLMError))

    def test_quota_error_can_be_raised(self) -> None:
        from agentnodes.foundation.llm.errors import LLMQuotaError
        with self.assertRaises(LLMError):
            raise LLMQuotaError("quota exceeded")

    def test_quota_error_message(self) -> None:
        from agentnodes.foundation.llm.errors import LLMQuotaError
        err = LLMQuotaError("monthly quota reached")
        self.assertIn("quota", str(err))


class TestWithConfig(unittest.TestCase):
    """OpenAICompatClient.with_config."""

    def test_with_config_returns_new_instance(self) -> None:
        c1 = oc_mod.OpenAICompatClient(LLMConfig(api_key="sk", temperature=0.7))
        c2 = c1.with_config(temperature=0.1)
        self.assertEqual(c1.config.temperature, 0.7)
        self.assertEqual(c2.config.temperature, 0.1)
        self.assertEqual(c2.config.api_key, "sk")

    def test_with_config_multiple_fields(self) -> None:
        c1 = oc_mod.OpenAICompatClient(LLMConfig(api_key="sk"))
        c2 = c1.with_config(temperature=0.2, model="x-model", max_tokens=1024)
        self.assertEqual(c2.config.temperature, 0.2)
        self.assertEqual(c2.config.model, "x-model")
        self.assertEqual(c2.config.max_tokens, 1024)

