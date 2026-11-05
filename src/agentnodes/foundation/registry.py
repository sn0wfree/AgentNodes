"""Generic typed Registry — unified base for all registries.

DSH-inspired: replaces 8+ ad-hoc ``dict[str, T]`` registries with a
single generic base class providing lookup, iteration, filtering,
and size queries.

Usage::

    from agentnodes.foundation.registry import Registry

    class ToolRegistry(Registry[BaseTool]):
        def register(self, tool):
            super().register(tool.name, tool)
"""

from __future__ import annotations

from typing import Callable, Generic, Iterator, Optional, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """Generic typed registry with lookup, iteration, and filtering.

    Subclass and override ``register()`` for custom registration logic
    (e.g. collecting briefs, validating names).
    """

    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    def register(self, key: str, item: T) -> None:
        """Register an item under the given key."""
        self._items[key] = item

    def get(self, key: str) -> Optional[T]:
        """Retrieve an item by key."""
        return self._items.get(key)

    def remove(self, key: str) -> Optional[T]:
        """Remove and return an item by key."""
        return self._items.pop(key, None)

    def all_items(self) -> list[T]:
        """All registered items (stable order)."""
        return list(self._items.values())

    def keys(self) -> list[str]:
        """All registered keys (stable order)."""
        return list(self._items.keys())

    def items(self) -> Iterator[tuple[str, T]]:
        """Iterate over (key, item) pairs."""
        return iter(self._items.items())

    def filter(self, predicate: Callable[[str, T], bool]) -> dict[str, T]:
        """Return items matching the predicate."""
        return {k: v for k, v in self._items.items() if predicate(k, v)}

    def restricted(
        self,
        *,
        deny: set[str] | None = None,
        allow: set[str] | None = None,
    ) -> Registry[T]:
        """Return a new registry with items filtered (immutable variant)."""
        new = Registry()
        new._items = dict(self._items)
        if deny:
            for k in deny:
                new._items.pop(k, None)
        if allow:
            allowed = set(allow)
            new._items = {k: v for k, v in new._items.items() if k in allowed}
        return new

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({len(self._items)} items)"


__all__ = ["Registry"]
