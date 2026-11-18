# -*- coding: utf-8 -*-
"""apps/chat/attempts_query 查询用例测试（顺序④，裁决 4：2026-11-18）。

裁决 4：`/attempts` 由 apps/chat 查询用例组合"持久化 Attempt 记录 ×
Gateway 实时状态"；API router 不再自行拼接。本文件覆盖兼容不变量：
旧→新迁移前后响应语义必须一致。
"""
from __future__ import annotations

from dataclasses import dataclass

from agentnodes.apps.chat.attempts_query import (
    MAX_FAILED,
    PROMPT_LIMIT,
    QUERY_STATUSES,
    query_live_attempts,
)
from agentnodes.kernel.session.models import AttemptStatus


@dataclass
class _Attempt:
    """Attempt 形状的最小替身（只含查询用例读取的字段）。"""

    attempt_id: str
    status: AttemptStatus
    prompt: str = ""
    created_at: str = "2026-11-18T00:00:00+00:00"
    message_id: str | None = None
    error: str | None = None


class _Store:
    def __init__(self, attempts: list[_Attempt], *, record_statuses=True):
        self._attempts = attempts
        self.received_statuses: list[str] = []
        self._record = record_statuses

    def list_attempts_by_status(self, session_id: str, statuses):
        if self._record:
            self.received_statuses = list(statuses)
        return list(self._attempts)


class _Gateway:
    def __init__(self, live: dict[str, str | None]):
        self._live = live

    def attempt_state(self, session_id: str, attempt_id: str):
        return self._live.get(attempt_id)


# ── 查询状态集合与常量 ───────────────────────────────────────────────


def test_query_statuses_cover_expected_three():
    assert set(QUERY_STATUSES) == {
        AttemptStatus.PENDING.value,
        AttemptStatus.RUNNING.value,
        AttemptStatus.FAILED.value,
    }


# ── 拼接语义 ────────────────────────────────────────────────────────


def test_pending_and_running_require_live_gateway_state():
    """存储里有 pending/running，但 Gateway 无实时状态 → 僵尸行丢弃。"""
    store = _Store([
        _Attempt("a-pending-zombie", AttemptStatus.PENDING),
        _Attempt("a-running-live", AttemptStatus.RUNNING),
    ])
    gateway = _Gateway({"a-running-live": "running"})

    out = query_live_attempts("s1", store=store, gateway=gateway)

    assert [r["attempt_id"] for r in out] == ["a-running-live"]
    assert out[0]["status"] == "running"


def test_queued_state_passthrough():
    store = _Store([_Attempt("a1", AttemptStatus.PENDING)])
    gateway = _Gateway({"a1": "queued"})
    out = query_live_attempts("s1", store=store, gateway=gateway)
    assert out[0]["status"] == "queued"


def test_failed_records_do_not_require_gateway_state():
    store = _Store([_Attempt("a-fail", AttemptStatus.FAILED, error="boom")])
    gateway = _Gateway({})  # 无实时状态

    out = query_live_attempts("s1", store=store, gateway=gateway)

    assert len(out) == 1
    assert out[0]["status"] == "failed"
    assert out[0]["error"] == "boom"


def test_failed_capped_at_max_failed():
    attempts = [
        _Attempt(f"a{i}", AttemptStatus.FAILED) for i in range(MAX_FAILED + 3)
    ]
    out = query_live_attempts("s1", store=_Store(attempts), gateway=_Gateway({}))
    assert len(out) == MAX_FAILED


def test_failed_cap_counts_only_failed_not_live():
    """上限只数失败记录，活跃记录不受影响。"""
    attempts = [
        _Attempt("live", AttemptStatus.RUNNING),
        *[_Attempt(f"f{i}", AttemptStatus.FAILED) for i in range(MAX_FAILED + 2)],
    ]
    out = query_live_attempts(
        "s1", store=_Store(attempts), gateway=_Gateway({"live": "running"})
    )
    assert len(out) == 1 + MAX_FAILED
    assert out[0]["attempt_id"] == "live"


def test_prompt_truncated_and_empty_preserved():
    long_prompt = "x" * (PROMPT_LIMIT + 50)
    store = _Store([
        _Attempt("a1", AttemptStatus.FAILED, prompt=long_prompt),
        _Attempt("a2", AttemptStatus.FAILED, prompt=""),
    ])
    out = query_live_attempts("s1", store=store, gateway=_Gateway({}))
    assert len(out[0]["prompt"]) == PROMPT_LIMIT
    assert out[1]["prompt"] == ""


def test_message_id_normalised_to_string():
    store = _Store([_Attempt("a1", AttemptStatus.FAILED, message_id=None)])
    out = query_live_attempts("s1", store=store, gateway=_Gateway({}))
    assert out[0]["message_id"] == ""


def test_response_shape_is_router_contract():
    """router 契约：每个条目恰好 6 个键（迁移前后响应形状不变）。"""
    store = _Store([_Attempt("a1", AttemptStatus.FAILED)])
    out = query_live_attempts("s1", store=store, gateway=_Gateway({}))
    assert set(out[0]) == {
        "attempt_id", "message_id", "status", "prompt", "created_at", "error",
    }


def test_store_receives_declared_statuses():
    store = _Store([])
    query_live_attempts("s1", store=store, gateway=_Gateway({}))
    assert store.received_statuses == list(QUERY_STATUSES)
