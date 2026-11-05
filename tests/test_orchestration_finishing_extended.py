"""E1 收尾三件的扩展测试 —— 边界/优先级/组合场景。"""
from __future__ import annotations

import json
import time

import pytest

from agentnodes.kernel.orchestration import BudgetGate, BudgetSpec
from agentnodes.kernel.orchestration.budget_gate import BudgetUsage
from agentnodes.kernel.orchestration.config_hub import (
    ConfigError,
    config_fingerprint,
    load_platform_config,
)


# ── ConfigHub：层优先级 ──────────────────────────────────


def test_env_overrides_file_for_same_key(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("mailbox:\n  cap: 5\n", encoding="utf-8")
    resolved = load_platform_config(
        file_path=str(cfg), env={"QNODES_MAILBOX__CAP": "9"}
    )
    assert resolved.mailbox.cap == 9  # env 层最高


def test_json_config_supported(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"mailbox": {"cap": 3}}), encoding="utf-8")
    assert load_platform_config(file_path=str(cfg), env={}).mailbox.cap == 3


def test_unsupported_extension_fail_fast(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("[mailbox]\ncap=3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式"):
        load_platform_config(file_path=str(cfg), env={})


def test_section_not_mapping_fail_fast(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"mailbox": [1, 2]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="映射"):
        load_platform_config(file_path=str(cfg), env={})


def test_top_level_not_mapping_fail_fast(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ConfigError, match="映射"):
        load_platform_config(file_path=str(cfg), env={})


def test_env_bool_variants_accepted():
    for raw, expected in [("true", True), ("TRUE", True), ("1", True),
                          ("yes", True), ("on", True), ("false", False),
                          ("0", False), ("no", False), ("off", False)]:
        resolved = load_platform_config(env={"QNODES_GATEWAY__NO_HTTP": raw})
        assert resolved.gateway.no_http is expected, raw


def test_env_float_from_string():
    resolved = load_platform_config(env={"QNODES_GATEWAY__GRACE_SECONDS": "12.5"})
    assert resolved.gateway.grace_seconds == 12.5


def test_cap_zero_fail_fast():
    with pytest.raises(ConfigError, match="cap"):
        load_platform_config(env={"QNODES_MAILBOX__CAP": "0"})


def test_fingerprint_independent_of_key_order(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.yaml"
    a.write_text(json.dumps({"mailbox": {"cap": 5}, "gateway": {"grace_seconds": 60}}), encoding="utf-8")
    b.write_text("gateway:\n  grace_seconds: 60\nmailbox:\n  cap: 5\n", encoding="utf-8")
    assert config_fingerprint(load_platform_config(file_path=str(a), env={})) == (
        config_fingerprint(load_platform_config(file_path=str(b), env={}))
    )


# ── 预算门：组合与优先级 ─────────────────────────────────


@pytest.fixture
def gate() -> BudgetGate:
    return BudgetGate()


def test_tokens_checked_before_calls(gate: BudgetGate):
    """两个维度同时超限时，tokens 优先报出（顺序稳定=日志可比）。"""
    usage = BudgetUsage(tokens_used=10**6, llm_calls=10**6, started_at=time.time())
    decision = gate.evaluate(None, usage, now=time.time())
    assert not decision.allowed
    assert decision.reason is not None and decision.reason.startswith("tokens")


def test_calls_checked_before_wallclock(gate: BudgetGate):
    usage = BudgetUsage(
        tokens_used=0, llm_calls=10**6, started_at=time.time() - 10**7
    )
    decision = gate.evaluate(None, usage, now=time.time())
    assert not decision.allowed
    assert decision.reason is not None and decision.reason.startswith("llm_calls")


def test_narrow_budget_denies_early(gate: BudgetGate):
    spec = BudgetSpec(max_tokens=10, max_llm_calls=1, wallclock_seconds=1.0)
    usage = BudgetUsage(tokens_used=11, llm_calls=0, started_at=time.time())
    assert not gate.evaluate(spec, usage, now=time.time()).allowed


def test_budget_gate_has_no_side_effects(gate: BudgetGate):
    usage = BudgetUsage(tokens_used=1, llm_calls=1, started_at=time.time())
    before = (usage.tokens_used, usage.llm_calls, usage.started_at)
    gate.evaluate(None, usage, now=time.time())
    assert (usage.tokens_used, usage.llm_calls, usage.started_at) == before


def test_budget_usage_is_frozen(gate: BudgetGate):
    usage = BudgetUsage()
    with pytest.raises(Exception):
        usage.tokens_used = 5  # type: ignore[misc]


def test_wallclock_exactly_at_limit_allowed(gate: BudgetGate):
    now = time.time()
    usage = BudgetUsage(started_at=now - 1800.0)
    assert gate.evaluate(None, usage, now=now).allowed


def test_wallclock_one_second_over_denied(gate: BudgetGate):
    now = time.time()
    usage = BudgetUsage(started_at=now - 1801.0)
    assert not gate.evaluate(None, usage, now=now).allowed
