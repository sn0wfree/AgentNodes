"""Unit tests for workflow.dag traversal helpers (find_upstream / find_downstream)."""
from __future__ import annotations

import pytest

from agentnodes.kernel.workflow.dag import (
    find_downstream,
    find_upstream,
    topological_layers,
    validate_dag,
)

# deps convention: {node: [upstream_deps]}
DIAMOND = {
    "a": [],
    "b": ["a"],
    "c": ["a"],
    "d": ["b", "c"],
}


class TestFindUpstream:
    def test_root_has_no_upstream(self):
        assert find_upstream(DIAMOND, "a") == []

    def test_direct_deps(self):
        assert find_upstream(DIAMOND, "b") == ["a"]

    def test_transitive_deps(self):
        assert find_upstream(DIAMOND, "d") == ["a", "b", "c"]

    def test_unknown_node(self):
        assert find_upstream(DIAMOND, "zzz") == []

    def test_excludes_self(self):
        assert "d" not in find_upstream(DIAMOND, "d")

    def test_result_sorted(self):
        assert find_upstream(DIAMOND, "d") == sorted(find_upstream(DIAMOND, "d"))


class TestFindDownstream:
    def test_leaf_has_no_downstream(self):
        assert find_downstream(DIAMOND, "d") == []

    def test_root_sees_all(self):
        assert find_downstream(DIAMOND, "a") == ["b", "c", "d"]

    def test_mid_level(self):
        assert find_downstream(DIAMOND, "b") == ["d"]

    def test_unknown_node(self):
        assert find_downstream(DIAMOND, "zzz") == []

    def test_excludes_self(self):
        assert "a" not in find_downstream(DIAMOND, "a")

    def test_linear_chain(self):
        adj = {"a": [], "b": ["a"], "c": ["b"], "d": ["c"]}
        assert find_downstream(adj, "a") == ["b", "c", "d"]
        assert find_upstream(adj, "d") == ["a", "b", "c"]


class TestValidateAndLayers:
    def test_diamond_valid(self):
        validate_dag(DIAMOND)
        layers = topological_layers(DIAMOND)
        assert layers[0] == ["a"]
        assert set(layers[1]) == {"b", "c"}
        assert layers[2] == ["d"]

    def test_cycle_raises(self):
        with pytest.raises(Exception):
            validate_dag({"a": ["b"], "b": ["a"]})

    def test_empty_layers(self):
        assert topological_layers({}) == []
