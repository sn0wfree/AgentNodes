"""DataStoreRegistry — runtime lookup for DataStore providers.

Mirrors the LLM provider pattern (``foundation/llm/provider/__init__.py``):
- ``register(name, cls)`` adds providers without touching core.
- ``get_store(name=None)`` returns a cached instance.
- ``available()`` lists registered provider names.

**2026-11-05 做减法批次 5**：``DuckDBDataStore``（原默认 provider）随
``apps/db.py`` 一起移除 —— DuckDB OHLCV 存储是研究业务，不是框架能力。
本模块**保留**，因为它是 L4 ``DataStore`` Protocol 的注册表（接缝），
只是**初始注册表为空**。

这带来一个明确的行为变化：

- 之前：``get_store()`` 默认返回 ``DuckDBDataStore()``
- 现在：``get_store()`` 抛 ``KeyError``，消息里列出可用 provider（空列表）

调用方（``interfaces/api/app.py``）已改为 try/except —— 无 provider 时
``ToolContext.data_store`` 留空，DB 相关工具在调用时给出可操作错误，
而不是启动就崩。

**要恢复数据存储能力**：实现一个 ``DataStore`` Protocol 的 provider，
在装配根里 ``register_store("duckdb", MyStore)`` 即可 —— 不需要改本模块。
"""

from __future__ import annotations

from typing import Optional

from strategy_research.foundation.storage.data_store import DataStore

__all__ = ["DataStoreRegistry", "register_store", "get_store"]

#: provider 名 → 实现类。批次 5 后为空 —— 具体实现在业务侧提供。
_REGISTRY: dict[str, type[DataStore]] = {}

# Process-wide instance cache so we don't re-init the backend on every call.
_INSTANCES: dict[str, DataStore] = {}


class DataStoreRegistry:
    """Holder for the process-global registry."""

    @classmethod
    def register(cls, name: str, provider_cls: type[DataStore]) -> None:
        """Register a new provider under ``name``. Idempotent."""
        _REGISTRY[name] = provider_cls
        _INSTANCES.pop(name, None)  # invalidate cached instance

    @classmethod
    def unregister(cls, name: str) -> None:
        _REGISTRY.pop(name, None)
        _INSTANCES.pop(name, None)

    @classmethod
    def available(cls) -> list[str]:
        return list(_REGISTRY.keys())


def register_store(name: str, provider_cls: type[DataStore]) -> None:
    """Module-level convenience for ``DataStoreRegistry.register``."""
    DataStoreRegistry.register(name, provider_cls)


def get_store(name: Optional[str] = None) -> DataStore:
    """Return a DataStore instance.

    - ``name=None`` → the single registered provider if exactly one exists;
      otherwise ``KeyError`` (ambiguous or none registered).
    - ``name="duckdb"`` etc. looks up the registry.
    - Unknown names raise ``KeyError`` with the available list, so typos
      surface immediately.

    批次 5 后默认注册表为空，因此 ``get_store()`` 会抛 ``KeyError``。
    装配根应 try/except 后留空 ``ToolContext.data_store``。
    """
    if name is None:
        if len(_REGISTRY) == 1:
            name = next(iter(_REGISTRY))
        elif not _REGISTRY:
            raise KeyError(
                "no DataStore provider registered; "
                "call register_store(name, cls) at the composition root"
            )
        else:
            raise KeyError(
                f"multiple DataStore providers registered; specify a name: "
                f"{sorted(_REGISTRY)}"
            )
    if name in _INSTANCES:
        return _INSTANCES[name]
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown DataStore provider {name!r}; "
            f"available: {sorted(_REGISTRY.keys())}"
        )
    instance = _REGISTRY[name]()
    _INSTANCES[name] = instance
    return instance
