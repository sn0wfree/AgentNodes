"""openai_client 纯函数单元测试——重试/退避/Retry-After/错误码。

覆盖 docs/coverage-90-plan-20261030.md §3.2：该模块原 64.8%，缺口集中在
重试与错误处理路径。这里测**模块级纯函数**（无网络、无状态），是 A 档
里投入产出比最高的部分——它们决定失败时的行为是否正确。
"""
from __future__ import annotations

import datetime
import email.utils

import httpx
import pytest

from strategy_research.foundation.llm.openai_client import (
    _backoff_delay,
    _build_headers,
    _extract_error_code,
    _is_retryable_status,
    _jitter_backoff,
    _parse_retry_after,
    _compute_retry_delay,
)


# ── _is_retryable_status ────────────────────────────────────


@pytest.mark.parametrize("status,expected", [
    (429, True),   # rate limit
    (500, True), (502, True), (503, True), (599, True),
    (200, False), (400, False), (401, False), (404, False),
    (600, False), (499, False),
])
def test_retryable_status(status, expected):
    assert _is_retryable_status(status) is expected


# ── _backoff_delay ──────────────────────────────────────────


def test_backoff_exponential():
    assert _backoff_delay(0, 1.0) == 1.0
    assert _backoff_delay(1, 1.0) == 2.0
    assert _backoff_delay(2, 1.0) == 4.0
    assert _backoff_delay(3, 2.0) == 16.0


def test_backoff_capped_at_60():
    assert _backoff_delay(10, 1.0) == 60.0
    assert _backoff_delay(100, 1.0) == 60.0


# ── _jitter_backoff ─────────────────────────────────────────


def test_jitter_within_bounds():
    """抖动区间 ±30%，且仍受 60s 上限约束。"""
    for _ in range(50):
        d = _jitter_backoff(2, 1.0)  # 基准 4.0
        assert 4.0 * 0.7 <= d <= 4.0 * 1.3


def test_jitter_never_exceeds_cap():
    for _ in range(50):
        assert _jitter_backoff(20, 10.0) <= 60.0


# ── _parse_retry_after ──────────────────────────────────────


def test_retry_after_seconds():
    assert _parse_retry_after("30") == 30.0
    assert _parse_retry_after("0") == 0.0
    assert _parse_retry_after("1.5") == 1.5


def test_retry_after_negative_clamped_to_zero():
    assert _parse_retry_after("-5") == 0.0


def test_retry_after_http_date():
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=60
    )
    header = email.utils.format_datetime(future, usegmt=True)
    parsed = _parse_retry_after(header)
    assert parsed is not None
    assert 50 <= parsed <= 70


def test_retry_after_past_date_clamped():
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    header = email.utils.format_datetime(past, usegmt=True)
    assert _parse_retry_after(header) == 0.0


def test_retry_after_empty_returns_none():
    assert _parse_retry_after("") is None
    assert _parse_retry_after(None) is None  # type: ignore[arg-type]


def test_retry_after_garbage_returns_none():
    assert _parse_retry_after("not a date") is None


def test_retry_after_naive_date_treated_as_utc():
    """无时区的 HTTP-date 按 UTC 解释（不能抛）。"""
    naive = email.utils.formatdate(usegmt=False)
    assert _parse_retry_after(naive) is not None


# ── _compute_retry_delay ────────────────────────────────────


def _resp(headers=None) -> httpx.Response:
    return httpx.Response(
        429, headers=headers or {}, request=httpx.Request("POST", "http://x"),
    )


def test_compute_delay_uses_backoff_without_header():
    d = _compute_retry_delay(_resp(), attempt=2, base_backoff=1.0,
                             jitter_fraction=0.0)
    assert d == 4.0


def test_compute_delay_honors_retry_after_when_larger():
    d = _compute_retry_delay(
        _resp({"Retry-After": "30"}), attempt=0, base_backoff=1.0,
        jitter_fraction=0.0,
    )
    assert d == 30.0


def test_compute_delay_keeps_backoff_when_header_smaller():
    """Retry-After 小于退避时取较大者（不因服务端要求而提前重试）。"""
    d = _compute_retry_delay(
        _resp({"Retry-After": "1"}), attempt=3, base_backoff=1.0,
        jitter_fraction=0.0,
    )
    assert d == 8.0


def test_compute_delay_retry_after_capped_at_5x_max():
    d = _compute_retry_delay(
        _resp({"Retry-After": "10000"}), attempt=0, base_backoff=1.0,
        max_backoff=60.0, jitter_fraction=0.0,
    )
    assert d == 60.0  # 5*60=300 被最终 cap 拉回 60


def test_compute_delay_applies_jitter():
    d = _compute_retry_delay(_resp(), attempt=2, base_backoff=1.0,
                             jitter_fraction=0.3)
    assert 4.0 * 0.7 <= d <= 4.0 * 1.3


def test_compute_delay_final_cap():
    d = _compute_retry_delay(_resp(), attempt=20, base_backoff=1.0,
                             jitter_fraction=0.3)
    assert d <= 60.0


def test_compute_delay_bad_retry_after_ignored():
    d = _compute_retry_delay(
        _resp({"Retry-After": "garbage"}), attempt=1, base_backoff=1.0,
        jitter_fraction=0.0,
    )
    assert d == 2.0  # 回退到指数退避


# ── _extract_error_code ─────────────────────────────────────


def test_extract_error_code_nested():
    body = {"error": {"code": "rate_limit_exceeded", "message": "x"}}
    assert _extract_error_code(body) == "rate_limit_exceeded"


def test_extract_error_code_top_level():
    assert _extract_error_code({"code": "invalid_api_key"}) == "invalid_api_key"


def test_extract_error_code_missing_returns_empty():
    assert _extract_error_code({}) == ""
    # error 是字符串时直接作为 code 返回（兼容部分 provider）
    assert _extract_error_code({"error": "plain string"}) == "plain string"


def test_extract_error_code_non_dict_returns_empty():
    assert _extract_error_code(None) == ""
    assert _extract_error_code("string") == ""


# ── _build_headers ──────────────────────────────────────────


class _Cfg:
    def __init__(self, api_key="sk-test"):
        self.api_key = api_key


class _Adapter:
    """最小 provider 适配器：自定义头由它提供。"""

    def __init__(self, extra=None):
        self._extra = extra or {}

    def custom_headers(self, config):
        return dict(self._extra)


def test_build_headers_basic():
    h = _build_headers(_Cfg(), adapter=_Adapter())
    assert h["Authorization"] == "Bearer sk-test"
    assert h["Content-Type"] == "application/json"
    assert h["Accept"] == "application/json"


def test_build_headers_merges_adapter_headers():
    h = _build_headers(_Cfg(), adapter=_Adapter({"X-Trace": "abc"}))
    assert h["X-Trace"] == "abc"


def test_build_headers_missing_key_raises():
    from strategy_research.foundation.llm.errors import LLMConfigError

    with pytest.raises(LLMConfigError):
        _build_headers(_Cfg(api_key=""), adapter=_Adapter())
