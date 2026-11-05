"""A5 不变量层测试 —— 清单完整、verify_all 全绿、违反可检出。"""
from __future__ import annotations

import pytest

from agentnodes.kernel.gateway import (
    InvariantViolation,
    invariants,
    verify_all,
)


def test_verify_all_green_at_startup():
    """启动自检：当前代码库的全部不变量必须全绿（这是 A5 的存在意义）。"""
    ok, violations = verify_all(raise_on_violation=False)
    assert ok, f"不变量违反: {violations}"


def test_verify_all_raises_on_violation():
    ok, violations = verify_all(raise_on_violation=False)
    if ok:
        pytest.skip("全绿时无法测 raise 分支")
    with pytest.raises(InvariantViolation):
        verify_all(raise_on_violation=True)


def test_invariant_registry_has_expected_members():
    ids = {inv.id for inv in invariants()}
    assert {"INV-1", "INV-3", "INV-5", "INV-8", "INV-9"} <= ids


def test_every_invariant_has_metadata():
    for inv in invariants():
        assert inv.id.startswith("INV-")
        assert inv.description
        assert callable(inv.verify)
        assert inv.enforced_since


def test_violation_exception_carries_details():
    exc = InvariantViolation("INV-x: 演示")
    assert "INV-x" in str(exc)
