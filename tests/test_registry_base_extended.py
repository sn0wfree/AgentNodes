"""P3-A extended: Registry[T] edge cases + complex types tests."""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.foundation.registry import Registry


@dataclass
class _Config:
    name: str
    value: int
    enabled: bool = True


class TestRegistryExtended:
    def test_empty_registry(self):
        r = Registry[str]()
        assert len(r) == 0
        assert list(r) == []
        assert r.all_items() == []
        assert r.get("anything") is None
        assert "anything" not in r

    def test_single_item(self):
        r = Registry[int]()
        r.register("only", 42)
        assert len(r) == 1
        assert r.get("only") == 42
        assert list(r) == ["only"]

    def test_register_overwrites(self):
        r = Registry[str]()
        r.register("key", "old")
        r.register("key", "new")
        assert r.get("key") == "new"
        assert len(r) == 1

    def test_remove_last_item(self):
        r = Registry[str]()
        r.register("only", "x")
        r.remove("only")
        assert len(r) == 0

    def test_filter_no_match(self):
        r = Registry[int]()
        r.register("a", 1)
        result = r.filter(lambda k, v: v > 100)
        assert result == {}

    def test_filter_all_match(self):
        r = Registry[int]()
        r.register("a", 1)
        r.register("b", 2)
        result = r.filter(lambda k, v: True)
        assert result == {"a": 1, "b": 2}

    def test_filter_by_key(self):
        r = Registry[str]()
        r.register("alpha", "a")
        r.register("beta", "b")
        r.register("gamma", "g")
        result = r.filter(lambda k, v: k.startswith("a"))
        assert result == {"alpha": "a"}

    def test_restricted_empty_registry(self):
        r = Registry[str]()
        r2 = r.restricted(deny={"anything"})
        assert len(r2) == 0

    def test_restricted_no_changes(self):
        r = Registry[str]()
        r.register("a", "x")
        r2 = r.restricted()
        assert len(r2) == 1
        assert r2.get("a") == "x"

    def test_restricted_deny_nonexistent(self):
        r = Registry[str]()
        r.register("a", "x")
        r2 = r.restricted(deny={"nonexistent"})
        assert len(r2) == 1

    def test_restricted_allow_nonexistent(self):
        r = Registry[str]()
        r.register("a", "x")
        r2 = r.restricted(allow={"nonexistent"})
        assert len(r2) == 0

    def test_registry_with_complex_values(self):
        """Registry works with complex dataclass values."""
        r = Registry[_Config]()
        r.register("prod", _Config("prod", 100, True))
        r.register("dev", _Config("dev", 10, False))
        prod = r.get("prod")
        assert prod is not None
        assert prod.name == "prod"
        assert prod.value == 100
        assert prod.enabled is True

    def test_registry_with_optional_values(self):
        """Registry works with Optional values."""
        r = Registry[Optional[int]]()
        r.register("a", None)
        r.register("b", 42)
        assert r.get("a") is None
        assert r.get("b") == 42
        assert "a" in r  # exists even though value is None

    def test_registry_preserves_insertion_order(self):
        """Items returned in insertion order."""
        r = Registry[str]()
        for letter in "zyxwvutsrqponmlkjihgfedcba":
            r.register(letter, letter.upper())
        keys = list(r)
        assert keys == list("zyxwvutsrqponmlkjihgfedcba")

    def test_repr(self):
        r = Registry[int]()
        assert "0 items" in repr(r)
        r.register("a", 1)
        assert "1 items" in repr(r)
