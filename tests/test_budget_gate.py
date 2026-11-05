"""chat 预算门单元测试 —— 字段级合并 / 三维检查 / None 语义。"""
from __future__ import annotations

import time

import pytest

from agentnodes.kernel.orchestration import BudgetGate, BudgetSpec, effective_budget
from agentnodes.kernel.orchestration.budget_gate import BudgetUsage


@pytest.fixture
def gate() -> BudgetGate:
    return BudgetGate()


def test_default_budget_applied_when_spec_none(gate: BudgetGate):
    usage = BudgetUsage(tokens_used=10, llm_calls=1, started_at=time.time())
    assert gate.evaluate(None, usage).allowed
    effective = effective_budget(None)
    assert effective.max_tokens == 200_000
    assert effective.max_llm_calls == 200
    assert effective.wallclock_seconds == 1800.0


def test_field_level_merge_over_default():
    # 只声明 max_tokens：其余字段继承会话缺省
    effective = effective_budget(BudgetSpec(max_tokens=1000))
    assert effective.max_tokens == 1000
    assert effective.max_llm_calls == 200
    assert effective.wallclock_seconds == 1800.0


def test_tokens_over_denied(gate: BudgetGate):
    usage = BudgetUsage(tokens_used=200_001, llm_calls=1, started_at=time.time())
    decision = gate.evaluate(None, usage)
    assert not decision.allowed
    assert "tokens" in (decision.reason or "")


def test_llm_calls_over_denied(gate: BudgetGate):
    usage = BudgetUsage(tokens_used=0, llm_calls=201, started_at=time.time())
    decision = gate.evaluate(None, usage)
    assert not decision.allowed
    assert "llm_calls" in (decision.reason or "")


def test_wallclock_over_denied(gate: BudgetGate):
    usage = BudgetUsage(tokens_used=0, llm_calls=0, started_at=time.time() - 3600)
    decision = gate.evaluate(None, usage, now=time.time())
    assert not decision.allowed
    assert "wallclock" in (decision.reason or "")


def test_declared_spec_replaces_default_threshold(gate: BudgetGate):
    # 声明 max_tokens=100：用到 101 就拒（用声明值而非缺省 200k）
    spec = BudgetSpec(max_tokens=100)
    usage = BudgetUsage(tokens_used=101, llm_calls=0, started_at=time.time())
    decision = gate.evaluate(spec, usage)
    assert not decision.allowed


def test_all_none_spec_falls_back_to_session_default(gate: BudgetGate):
    # 全 None = 全部未声明 → 会话缺省生效（不存在「显式不限」；
    # 要放开就把 ConfigHub 的缺省阈值调高）
    spec = BudgetSpec(max_tokens=None, max_llm_calls=None, wallclock_seconds=None)
    usage = BudgetUsage(tokens_used=10**9, llm_calls=10**6, started_at=time.time() - 10**7)
    decision = gate.evaluate(spec, usage, now=time.time())
    assert not decision.allowed  # 缺省 200k tokens 仍然拦住
    assert "tokens" in (decision.reason or "")


def test_exactly_at_limit_allowed(gate: BudgetGate):
    usage = BudgetUsage(tokens_used=200_000, llm_calls=200, started_at=time.time())
    assert gate.evaluate(None, usage, now=time.time()).allowed


def test_started_at_zero_skips_wallclock_check(gate: BudgetGate):
    # started_at=0（未计时）不触发 wallclock 误判
    usage = BudgetUsage(tokens_used=0, llm_calls=0, started_at=0.0)
    assert gate.evaluate(None, usage, now=time.time()).allowed
