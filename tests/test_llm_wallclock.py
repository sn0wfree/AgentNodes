"""Tests for the LLM wall-clock ceiling (SR_AGENT_WALLCLOCK_TIMEOUT).

Guards against "slow-trickle" streams: the server keeps emitting lines
(more often than httpx's per-read timeout) but never finishes — without
a wall clock the request would hang forever. 0 disables the guard.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.llm.config import LLMConfig
from strategy_research.foundation.llm.errors import LLMTimeoutError
from strategy_research.foundation.llm.openai_client import OpenAICompatClient


def _trickle_lines(total: int, gap: float):
    """Generator: emit SSE content lines every ``gap`` seconds, never
    finishing (slow trickle that keeps read-timeouts from firing)."""
    for i in range(total):
        time.sleep(gap)
        yield f'data: {json.dumps({"choices": [{"delta": {"content": "x"}}]})}\n\n'.encode()
    # no finish_reason — stream just stops silently


def _client(wallclock: float, trickle_total: int, gap: float) -> OpenAICompatClient:
    cfg = LLMConfig(api_key="sk", model="gpt-4o-mini",
                    wallclock_timeout_s=wallclock)
    client = OpenAICompatClient(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_trickle_lines(trickle_total, gap),
            headers={"content-type": "text/event-stream"},
        )

    client._transport = httpx.MockTransport(handler)
    return client


def _collect(client: OpenAICompatClient) -> list:
    out = []
    for chunk in client.stream([{"role": "user", "content": "hi"}]):
        out.append(chunk)
    return out


def test_sync_slow_trickle_hits_wallclock():
    # 0.05s/line → 100 lines = 5s of trickle; ceiling 0.3s must fire.
    client = _client(wallclock=0.3, trickle_total=100, gap=0.05)
    with pytest.raises(LLMTimeoutError, match="wall-clock"):
        _collect(client)


def test_sync_under_wallclock_succeeds():
    # Fast enough lines finish well inside the ceiling.
    client = _client(wallclock=30.0, trickle_total=3, gap=0.01)
    chunks = _collect(client)
    assert len(chunks) == 3


def test_sync_wallclock_disabled_never_fires():
    # 0 = disabled: the trickle finishes (5s) without the guard firing.
    client = _client(wallclock=0.0, trickle_total=2, gap=0.02)
    chunks = _collect(client)
    assert len(chunks) == 2


def test_wallclock_expired_before_retry_raises():
    """Retries must not start once the ceiling already passed."""
    cfg = LLMConfig(api_key="sk", model="gpt-4o-mini",
                    wallclock_timeout_s=0.01, max_retries=3,
                    retry_backoff_s=0.01)
    client = OpenAICompatClient(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"{}")

    client._transport = httpx.MockTransport(handler)
    # First attempt raises retryable 429, sleeps, retries — the deadline
    # check at attempt 2 must fire instead of retrying forever.
    with pytest.raises(LLMTimeoutError, match="wall-clock"):
        _collect(client)


def test_env_var_injects_wallclock():
    cfg = LLMConfig.load(env={"SR_AGENT_WALLCLOCK_TIMEOUT": "42"})
    assert cfg.wallclock_timeout_s == 42.0


def test_default_wallclock_is_30min():
    assert LLMConfig().wallclock_timeout_s == 1800.0


class TestAsyncWallclock:
    @pytest.mark.asyncio
    async def test_async_slow_trickle_hits_wallclock(self):
        async def trickle():
            for _i in range(100):
                await asyncio.sleep(0.05)
                yield f'data: {json.dumps({"choices": [{"delta": {"content": "x"}}]})}\n\n'.encode()

        cfg = LLMConfig(api_key="sk", model="gpt-4o-mini",
                        wallclock_timeout_s=0.3)
        client = OpenAICompatClient(cfg)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=trickle(),
                headers={"content-type": "text/event-stream"},
            )

        client._transport = httpx.MockTransport(handler)
        with pytest.raises(LLMTimeoutError, match="wall-clock"):
            async for _chunk in client.astream([{"role": "user", "content": "hi"}]):
                pass
