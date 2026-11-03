"""P3-A: Registry[T] unified base class tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.registry import Registry


class TestRegistryBase:
    def test_register_and_get(self):
        r = Registry[str]()
        r.register("key1", "value1")
        assert r.get("key1") == "value1"

    def test_get_missing_returns_none(self):
        r = Registry[str]()
        assert r.get("missing") is None

    def test_remove(self):
        r = Registry[str]()
        r.register("key1", "value1")
        removed = r.remove("key1")
        assert removed == "value1"
        assert r.get("key1") is None

    def test_remove_missing_returns_none(self):
        r = Registry[str]()
        assert r.remove("missing") is None

    def test_all_items(self):
        r = Registry[int]()
        r.register("a", 1)
        r.register("b", 2)
        r.register("c", 3)
        items = r.all_items()
        assert items == [1, 2, 3]

    def test_keys(self):
        r = Registry[int]()
        r.register("a", 1)
        r.register("b", 2)
        assert r.keys() == ["a", "b"]

    def test_items_iterator(self):
        r = Registry[int]()
        r.register("a", 1)
        r.register("b", 2)
        items = dict(r.items())
        assert items == {"a": 1, "b": 2}

    def test_filter(self):
        r = Registry[int]()
        r.register("a", 1)
        r.register("b", 2)
        r.register("c", 3)
        result = r.filter(lambda k, v: v > 1)
        assert result == {"b": 2, "c": 3}

    def test_len(self):
        r = Registry[str]()
        assert len(r) == 0
        r.register("a", "x")
        assert len(r) == 1
        r.register("b", "y")
        assert len(r) == 2

    def test_contains(self):
        r = Registry[str]()
        r.register("a", "x")
        assert "a" in r
        assert "b" not in r

    def test_iter(self):
        r = Registry[str]()
        r.register("a", "x")
        r.register("b", "y")
        assert list(r) == ["a", "b"]

    def test_restricted_deny(self):
        r = Registry[str]()
        r.register("a", "x")
        r.register("b", "y")
        r2 = r.restricted(deny={"a"})
        assert len(r2) == 1
        assert r2.get("b") == "y"
        # Original unchanged
        assert len(r) == 2

    def test_restricted_allow(self):
        r = Registry[str]()
        r.register("a", "x")
        r.register("b", "y")
        r2 = r.restricted(allow={"a"})
        assert len(r2) == 1
        assert r2.get("a") == "x"

    def test_restricted_combined(self):
        r = Registry[str]()
        r.register("a", "x")
        r.register("b", "y")
        r.register("c", "z")
        r2 = r.restricted(deny={"b"}, allow={"a", "c"})
        assert len(r2) == 2
        assert r2.get("a") == "x"
        assert r2.get("c") == "z"

    def test_repr(self):
        r = Registry[str]()
        r.register("a", "x")
        assert "1 items" in repr(r)
