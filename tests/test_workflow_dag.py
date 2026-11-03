import pytest

from strategy_research.kernel.workflow.dag import topological_layers, validate_dag


class TestValidateDag:
    def test_empty_dag(self):
        validate_dag({})

    def test_single_node(self):
        validate_dag({"a": []})

    def test_linear_chain(self):
        # deps: b depends on a, c depends on b
        validate_dag({"a": [], "b": ["a"], "c": ["b"]})

    def test_diamond(self):
        # deps: b depends on a, c depends on a, d depends on b and c
        validate_dag({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})

    def test_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"a": ["b"], "b": ["a"]})

    def test_self_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"a": ["a"]})

    def test_indirect_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"a": ["b"], "b": ["c"], "c": ["a"]})

    def test_disconnected_components(self):
        validate_dag({"a": [], "b": ["a"], "c": [], "d": ["c"]})


class TestTopologicalLayers:
    def test_empty(self):
        assert topological_layers({}) == []

    def test_single_node(self):
        result = topological_layers({"a": []})
        assert result == [["a"]]

    def test_linear_chain(self):
        # deps: b depends on a, c depends on b
        result = topological_layers({"a": [], "b": ["a"], "c": ["b"]})
        assert result == [["a"], ["b"], ["c"]]

    def test_diamond(self):
        # deps: b depends on a, c depends on a, d depends on b and c
        result = topological_layers({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})
        assert len(result) == 3
        assert result[0] == ["a"]
        assert sorted(result[1]) == ["b", "c"]
        assert result[2] == ["d"]

    def test_parallel_agents_same_layer(self):
        # deps: c depends on both a and b
        result = topological_layers({"a": [], "b": [], "c": ["a", "b"]})
        assert len(result) == 2
        assert sorted(result[0]) == ["a", "b"]
        assert result[1] == ["c"]

    def test_complex_dag(self):
        # deps convention
        adj = {
            "researcher": [],
            "data_quality": ["researcher"],
            "factor_analyst": ["researcher"],
            "strategist": ["data_quality", "factor_analyst"],
            "portfolio_construction": ["strategist"],
        }
        result = topological_layers(adj)
        assert len(result) == 4
        assert result[0] == ["researcher"]
        assert sorted(result[1]) == ["data_quality", "factor_analyst"]
        assert result[2] == ["strategist"]
        assert result[3] == ["portfolio_construction"]

    def test_result_is_sorted(self):
        # deps: a depends on z, m depends on a, b depends on m
        result = topological_layers({"z": [], "a": ["z"], "m": ["a"], "b": ["m"]})
        for layer in result:
            assert layer == sorted(layer)
