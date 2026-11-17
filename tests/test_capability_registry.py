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


# ── W1 能力自检 / 跳过集提升（2026-11-17 补测：原覆盖缺口 48 行）──────


class TestSkipAndPromote:
    def test_missing_dependency_skips_not_shells(self):
        """依赖不全 → 整组不注册（降级返回 False），记录完整快照。"""
        r = CapabilityRegistry()
        assert r.register("biz.tool", lambda reg: 1, requires=("biz.data",)) is False
        assert r.skipped() == {"biz.tool": frozenset({"biz.data"})}
        assert not r.has("biz.tool")
        with pytest.raises(CapabilityNotFound):
            r.resolve("biz.tool")

    def test_promote_after_dependency_arrives(self):
        """W1 修复语义：依赖晚到 → 跳过集的 factory 真正补注册。"""
        r = CapabilityRegistry()
        r.register("biz.tool", lambda reg: "T", requires=("biz.data",))
        r.register("biz.data", lambda reg: "D")
        assert r.has("biz.tool")
        assert r.resolve("biz.tool") == "T"
        assert r.skipped() == {}

    def test_skip_emits_to_registered_sink(self):
        from agentnodes.kernel.agent.capability.registry import (
            register_skip_sink,
            reset_skip_sinks,
        )
        events = []
        register_skip_sink(lambda event, payload: events.append((event, payload)))
        try:
            r = CapabilityRegistry()
            r.register("biz.tool", lambda reg: 1, requires=("biz.data",))
            assert "capability.skipped" in [e for e, _ in events]
            payload = next(p for e, p in events if e == "capability.skipped")
            assert payload["missing"] == ["biz.data"]
        finally:
            reset_skip_sinks()

    def test_promote_emits_event(self):
        from agentnodes.kernel.agent.capability.registry import (
            register_skip_sink,
            reset_skip_sinks,
        )
        events = []
        register_skip_sink(lambda event, payload: events.append(event))
        try:
            r = CapabilityRegistry()
            r.register("biz.tool", lambda reg: 1, requires=("biz.data",))
            r.register("biz.data", lambda reg: 2)
            assert "capability.promoted" in events
        finally:
            reset_skip_sinks()

    def test_sink_exception_does_not_break_register(self):
        from agentnodes.kernel.agent.capability.registry import (
            register_skip_sink,
            reset_skip_sinks,
        )

        def bad_sink(event, payload):
            raise RuntimeError("sink dead")

        register_skip_sink(bad_sink)
        try:
            r = CapabilityRegistry()
            assert r.register("k", lambda reg: 1) is True
        finally:
            reset_skip_sinks()


class TestTwoPhaseWiring:
    def test_declare_then_register_many(self):
        """两阶段装配：declare 让依赖声明先于实现落表。

        **实测契约（2026-11-17）**：`register_many` 只提交**显式传入**的
        mapping；pending 声明参与依赖可用性判定但不会被自动提交——
        与 `declare` docstring「待后续 register_many 合并」的措辞有出入
        （零生产调用方，暂按实际行为钉住；合并语义若需要，另行立项）。
        """
        r = CapabilityRegistry()
        r.declare({"b": (lambda reg: "B", {"a"})})
        r.declare({"a": lambda reg: "A"})
        # b 在 declare 阶段就声明了依赖 a——register_many 阶段一次性提交
        r.register_many({
            "a": lambda reg: "A",
            "b": (lambda reg: "B", {"a"}),
        })
        assert r.resolve("b") == "B"

    def test_register_many_partial_skip(self):
        r = CapabilityRegistry()
        r.register_many({
            "ok": lambda reg: 1,
            "orphan": (lambda reg: 2, {"never_there"}),
        })
        assert r.has("ok") and not r.has("orphan")
        assert r.skipped() == {"orphan": frozenset({"never_there"})}

    def test_verify_dependencies_detects_backdoor(self):
        """绕过 register 直接塞 _factories → 一致性检查抓到。"""
        r = CapabilityRegistry()
        r.register("dep", lambda reg: 2)  # 先注册依赖，否则 k 进跳过集
        r.register("k", lambda reg: 1, requires=("dep",))
        assert r.verify_dependencies() == {}
        r._factories.pop("dep")
        assert r.verify_dependencies() == {"k": ["dep"]}

    def test_requires_of(self):
        r = CapabilityRegistry()
        r.register("dep", lambda reg: 2)  # 依赖先注册，k 才不会被跳过
        r.register("k", lambda reg: 1, requires=("dep",))
        assert r.requires_of("k") == frozenset({"dep"})
        assert r.requires_of("nope") == frozenset()


class TestPopulatorAndGeneration:
    def teardown_method(self):
        reset_default_registry()

    def test_populator_runs_on_first_get(self):
        from agentnodes.kernel.agent.capability.registry import (
            register_populator,
        )
        calls = []
        register_populator(lambda reg: calls.append(reg))
        reset_default_registry()
        r1 = get_default_registry()
        r2 = get_default_registry()
        assert r1 is r2
        assert calls == [r1], "populator 只在新建时跑一次"

    def test_populator_failure_tolerated(self):
        from agentnodes.kernel.agent.capability.registry import (
            register_populator,
        )
        register_populator(lambda reg: 1 / 0)
        reset_default_registry()
        assert get_default_registry() is not None

    def test_generation_increments_on_set_and_reset(self):
        from agentnodes.kernel.agent.capability.registry import (
            registry_generation,
        )
        g0 = registry_generation()
        set_default_registry(CapabilityRegistry())
        assert registry_generation() == g0 + 1
        reset_default_registry()
        assert registry_generation() == g0 + 2
