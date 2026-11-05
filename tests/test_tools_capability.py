"""P0-2 D — ToolContext capability seam tests.

Covers: ToolContext accepts new optional fields, helper functions
raise a helpful error when the seam is missing, and AgentLoop's
``_build_data_store`` / ``_build_sandbox`` produce working instances.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.sandbox import (
    ExecutionSandbox,
    StaticSandbox,
)
from strategy_research.kernel.agent.tools import ToolContext
from strategy_research.kernel.agent.tools_capability import (
    ToolCapabilityError,
    get_data_store,
    get_sandbox,
)
# W7/W10 (2026-11-04): the concrete registry moved to apps — it adapts
# ``apps.db``, so it lives at L2; only the ``DataStore`` Protocol stayed in
# L4. Importing ``get_store`` from ``foundation.storage`` has failed since
# then (it is not re-exported there on purpose: an L4 → L2 re-export would
# itself be an upward import).
#
# 2026-11-05 批次 5：DuckDBDataStore 随数据域删除，注册表初始为空。
# 测试改为注入一个内存替身（_FakeStore）而不是向全局注册表要实例 ——
# 这样测的是「ToolContext 能装任意 DataStore 实现」，比依赖具体 provider 更准。
from strategy_research.apps.data_store_registry import get_store, register_store
from strategy_research.foundation.storage.data_store import DataStore


class _FakeStore:
    """最小 DataStore 实现 —— 只为验证「任意实现都能装进 ToolContext」。

    ``DataStore`` 是 ``runtime_checkable`` Protocol，``isinstance`` 只检查
    方法**是否存在**（``__getattr__`` 动态兜底不算）。所以下面显式补齐
    全部 24 个方法，每个都是 no-op —— 本测试不验语义，只验「装得进去」。
    """

    def __init__(self):
        pass


# 把 Protocol 的方法逐个绑成 no-op（isinstance 需要真属性）
def _noop(self, *args, **kwargs):
    return None


for _m in (
    "init", "get_connection", "save_ohlcv", "load_price_data", "load_ohlcv_data",
    "save_factor_data", "load_factor_data", "register_factor", "list_factors",
    "remove_factor", "save_backtest_result", "list_backtest_results",
    "get_backtest_result", "save_weight_history", "load_weight_history",
    "save_nav_history", "load_nav_history", "cache_validation",
    "list_validation_cache", "get_last_import_date", "update_import_meta",
    "update_data_fingerprint", "get_data_fingerprint",
):
    setattr(_FakeStore, _m, _noop)


@pytest.fixture()
def store():
    """一个可注入的 DataStore 实例（不碰全局注册表）。"""
    return _FakeStore()


class TestToolContextFields:
    def test_default_fields_still_none(self):
        ctx = ToolContext()
        assert ctx.data_store is None
        assert ctx.sandbox is None

    def test_fields_assignable(self, tmp_path, store):
        sandbox = StaticSandbox(tmp_path)
        ctx = ToolContext(data_store=store, sandbox=sandbox)
        assert ctx.data_store is store
        assert ctx.sandbox is sandbox


class TestCapabilityHelpers:
    def test_get_data_store_returns_injected(self, tmp_path, store):
        ctx = ToolContext(data_store=store)
        assert get_data_store(ctx) is store

    def test_get_sandbox_returns_injected(self, tmp_path):
        sandbox = StaticSandbox(tmp_path)
        ctx = ToolContext(sandbox=sandbox)
        assert get_sandbox(ctx) is sandbox
        assert isinstance(get_sandbox(ctx), ExecutionSandbox)

    def test_get_data_store_missing_raises(self):
        ctx = ToolContext()
        with pytest.raises(ToolCapabilityError) as ei:
            get_data_store(ctx)
        assert "data_store" in str(ei.value)
        assert "ToolContext" in str(ei.value)

    def test_get_sandbox_missing_raises(self):
        ctx = ToolContext()
        with pytest.raises(ToolCapabilityError) as ei:
            get_sandbox(ctx)
        assert "sandbox" in str(ei.value)
        assert "ToolContext" in str(ei.value)

    def test_get_data_store_none_context_raises(self):
        with pytest.raises(ToolCapabilityError):
            get_data_store(None)

    def test_get_sandbox_none_context_raises(self):
        with pytest.raises(ToolCapabilityError):
            get_sandbox(None)

    def test_data_store_is_protocol_compatible(self, tmp_path, store):
        """The injected value is a real DataStore — not just truthy."""
        ctx = ToolContext(data_store=store)
        assert isinstance(get_data_store(ctx), DataStore)

    def test_registry_empty_by_default(self):
        """批次 5：默认注册表为空，get_store() 给出可操作错误。"""
        with pytest.raises(KeyError) as ei:
            get_store()
        assert "register_store" in str(ei.value)

    def test_registry_accepts_new_provider(self, store):
        """接缝仍在：register_store 后 get_store 能拿到实例。"""
        register_store("fake", _FakeStore)
        try:
            assert isinstance(get_store("fake"), DataStore)
        finally:
            from strategy_research.apps.data_store_registry import DataStoreRegistry
            DataStoreRegistry.unregister("fake")
