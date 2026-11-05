"""阶段二验收 ①：CapabilityRegistry（施工方案 §3.7）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.capability import (
    CapabilityAlreadyRegistered,
    CapabilityNotFound,
    CapabilityRegistry,
    get_default_registry,
    reset_default_registry,
    set_default_registry,
)


class TestRegister:
    def test_register_then_resolve(self):
        reg = CapabilityRegistry()
        reg.register("db.connection", lambda r: "conn-factory")
        assert reg.resolve("db.connection") == "conn-factory"

    def test_duplicate_register_fails_fast(self):
        """重复注册必须 fail-fast（静默覆盖会造成加载顺序依赖）。"""
        reg = CapabilityRegistry()
        reg.register("k", lambda r: 1)
        with pytest.raises(CapabilityAlreadyRegistered, match="already registered"):
            reg.register("k", lambda r: 2)

    def test_empty_kind_rejected(self):
        reg = CapabilityRegistry()
        with pytest.raises(ValueError, match="non-empty str"):
            reg.register("", lambda r: 1)

    def test_non_callable_factory_rejected(self):
        reg = CapabilityRegistry()
        with pytest.raises(ValueError, match="must be callable"):
            reg.register("k", "not-callable")  # type: ignore[arg-type]

    def test_register_many(self):
        reg = CapabilityRegistry()
        reg.register_many({"a": lambda r: 1, "b": lambda r: 2})
        assert reg.list_kinds() == ["a", "b"]

    def test_register_many_conflict_is_atomic(self):
        """批量注册任一冲突 → 整体不生效。"""
        reg = CapabilityRegistry()
        reg.register("a", lambda r: 1)
        with pytest.raises(CapabilityAlreadyRegistered):
            reg.register_many({"b": lambda r: 2, "a": lambda r: 3})
        assert reg.list_kinds() == ["a"], "冲突后不得写入 b"


class TestResolve:
    def test_unregistered_raises_with_available(self):
        reg = CapabilityRegistry()
        reg.register("known.kind", lambda r: 1)
        with pytest.raises(CapabilityNotFound) as ei:
            reg.resolve("missing")
        assert "missing" in str(ei.value)
        assert "known.kind" in str(ei.value)

    def test_factory_receives_registry(self):
        """factory 拿到 registry 本身，可解析依赖（组合 capability）。"""
        reg = CapabilityRegistry()
        reg.register("base", lambda r: "BASE")
        reg.register("derived", lambda r: f"derived({r.resolve('base')})")
        assert reg.resolve("derived") == "derived(BASE)"

    def test_resolve_returns_new_instance_each_call(self):
        """Registry 不替调用方管生命周期（需要单例的由 factory 自己备忘）。"""
        reg = CapabilityRegistry()
        reg.register("obj", lambda r: object())
        assert reg.resolve("obj") is not reg.resolve("obj")


class TestListing:
    def test_list_kinds_sorted(self):
        reg = CapabilityRegistry()
        for k in ["c", "a", "b"]:
            reg.register(k, lambda r: 1)
        assert reg.list_kinds() == ["a", "b", "c"]

    def test_contains_and_len(self):
        reg = CapabilityRegistry()
        reg.register("x", lambda r: 1)
        assert "x" in reg
        assert "y" not in reg
        assert len(reg) == 1

    def test_has(self):
        reg = CapabilityRegistry()
        reg.register("x", lambda r: 1)
        assert reg.has("x") is True
        assert reg.has("y") is False

    def test_registration_order_irrelevant(self):
        """注册顺序不影响解析结果（施工方案 §3.7）。"""
        r1 = CapabilityRegistry()
        r1.register("a", lambda r: "A")
        r1.register("b", lambda r: "B")

        r2 = CapabilityRegistry()
        r2.register("b", lambda r: "B")
        r2.register("a", lambda r: "A")

        assert r1.resolve("a") == r2.resolve("a") == "A"
        assert r1.resolve("b") == r2.resolve("b") == "B"


class TestDefaultRegistry:
    def teardown_method(self):
        reset_default_registry()

    def test_default_is_lazy_singleton(self):
        reset_default_registry()
        assert get_default_registry() is get_default_registry()

    def test_set_default(self):
        custom = CapabilityRegistry()
        custom.register("k", lambda r: 42)
        set_default_registry(custom)
        assert get_default_registry() is custom

    def test_reset(self):
        a = get_default_registry()
        reset_default_registry()
        b = get_default_registry()
        assert a is not b
