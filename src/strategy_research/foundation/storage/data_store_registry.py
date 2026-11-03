"""DataStoreRegistry — runtime lookup for DataStore providers.

Mirrors the LLM provider pattern (``core/llm/provider/__init__.py``):
- A default provider is registered at import time.
- ``register(name, cls)`` adds new providers without touching core.
- ``get_store(name=None)`` returns an instance (cached per-name).

P0-2.A registers only ``"duckdb"``. Future providers (in-memory for
tests, SQLite for slim deployments) plug in here.
"""

from __future__ import annotations

from typing import Optional

from .data_store import DataStore
from .duckdb_store import DuckDBDataStore

__all__ = ["DataStoreRegistry", "register_store", "get_store"]

_REGISTRY: dict[str, type[DataStore]] = {
    "duckdb": DuckDBDataStore,
}

# Process-wide instance cache so we don't re-init DuckDB on every call.
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

    - ``name=None`` (default) returns ``"duckdb"`` (the only registered
      default; matches the production wiring).
    - ``name="duckdb"`` etc. looks up the registry.
    - Unknown names raise ``KeyError`` with the available list, so
      typos surface immediately.
    """
    if name is None:
        name = "duckdb"
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
