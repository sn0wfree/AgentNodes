"""Unit tests for core.registry — generic typed Registry."""
from __future__ import annotations

from strategy_research.foundation.registry import Registry


class TestKeysContract:
    def test_keys_returns_names_not_pairs(self):
        r = Registry[int]()
        r.register("alpha", 1)
        r.register("beta", 2)
        keys = r.keys()
        assert keys == ["alpha", "beta"]
        assert all(isinstance(k, str) for k in keys)

    def test_keys_empty(self):
        assert Registry[int]().keys() == []

    def test_keys_preserves_insertion_order(self):
        r = Registry[int]()
        for name in ("z", "a", "m"):
            r.register(name, 1)
        assert r.keys() == ["z", "a", "m"]

    def test_keys_not_equal_items(self):
        r = Registry[int]()
        r.register("a", 1)
        assert r.keys() != list(r.items())


class TestRegisterGetRemove:
    def test_register_overwrites(self):
        r = Registry[str]()
        r.register("k", "v1")
        r.register("k", "v2")
        assert r.get("k") == "v2"
        assert len(r) == 1

    def test_get_missing_is_none(self):
        assert Registry[int]().get("nope") is None

    def test_remove_returns_value(self):
        r = Registry[int]()
        r.register("a", 1)
        assert r.remove("a") == 1
        assert r.get("a") is None

    def test_remove_missing_is_none(self):
        assert Registry[int]().remove("x") is None

    def test_all_items_values_only(self):
        r = Registry[int]()
        r.register("a", 1)
        r.register("b", 2)
        assert r.all_items() == [1, 2]


class TestIterationAndFilter:
    def test_items_iterator(self):
        r = Registry[int]()
        r.register("a", 1)
        r.register("b", 2)
        assert dict(r.items()) == {"a": 1, "b": 2}

    def test_iter_yields_keys(self):
        r = Registry[str]()
        r.register("a", "x")
        r.register("b", "y")
        assert list(r) == ["a", "b"]

    def test_contains(self):
        r = Registry[str]()
        r.register("a", "x")
        assert "a" in r
        assert "b" not in r

    def test_filter(self):
        r = Registry[int]()
        r.register("a", 1)
        r.register("b", 2)
        r.register("c", 3)
        assert r.filter(lambda k, v: v > 1) == {"b": 2, "c": 3}


class TestRestricted:
    def test_deny_removes_key(self):
        r = Registry[str]()
        r.register("a", "x")
        r.register("b", "y")
        r2 = r.restricted(deny={"a"})
        assert r2.keys() == ["b"]

    def test_allow_keeps_only_listed(self):
        r = Registry[str]()
        r.register("a", "x")
        r.register("b", "y")
        r2 = r.restricted(allow={"a"})
        assert r2.keys() == ["a"]

    def test_does_not_mutate_original(self):
        r = Registry[str]()
        r.register("a", "x")
        r.restricted(deny={"a"})
        assert r.keys() == ["a"]
