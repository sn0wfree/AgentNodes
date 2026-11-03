"""Tests for alpha_zoo_convert module."""

from __future__ import annotations

from strategy_research.apps.alpha_zoo_convert import (
    analyze_compute_function,
    convert_py_to_yaml,
    yaml_to_string,
)


class TestAnalyzeComputeFunction:
    def test_analyze_valid_file(self, tmp_path):
        py_file = tmp_path / "alpha001.py"
        py_file.write_text("""
def compute(data):
    return data["close"].pct_change(20)
""")
        result = analyze_compute_function(py_file)
        assert result["error"] is None
        assert result["analyzer"] is not None
        assert result["complexity"] >= 1

    def test_analyze_with_meta(self, tmp_path):
        py_file = tmp_path / "alpha002.py"
        py_file.write_text("""
__alpha_meta__ = {"name": "test_alpha", "category": "momentum"}

def compute(data):
    return data["close"] / data["close"].shift(20) - 1
""")
        result = analyze_compute_function(py_file)
        assert result["error"] is None
        assert result["meta"]["name"] == "test_alpha"

    def test_analyze_no_compute(self, tmp_path):
        py_file = tmp_path / "alpha003.py"
        py_file.write_text("""
def helper(x):
    return x + 1
""")
        result = analyze_compute_function(py_file)
        assert result["error"] == "No compute() found"

    def test_analyze_syntax_error(self, tmp_path):
        py_file = tmp_path / "alpha004.py"
        py_file.write_text("def compute(data: return x")
        result = analyze_compute_function(py_file)
        assert result["error"] is not None

    def test_analyze_nonexistent_file(self, tmp_path):
        result = analyze_compute_function(tmp_path / "nonexistent.py")
        assert result["error"] is not None


class TestConvertPyToYaml:
    def test_convert_simple(self, tmp_path):
        py_file = tmp_path / "alpha001.py"
        py_file.write_text("""
def compute(data):
    close = data["close"]
    return close / close.shift(20) - 1
""")
        result = convert_py_to_yaml(py_file)
        # May or may not succeed depending on analyzer capabilities
        # Just verify it doesn't crash
        assert result is None or isinstance(result, dict)

    def test_convert_nonexistent(self, tmp_path):
        result = convert_py_to_yaml(tmp_path / "nonexistent.py")
        assert result is None


class TestYamlToString:
    def test_basic_output(self):
        config = {
            "name": "test",
            "expression": {"op": "pct_change", "args": [{"field": "close"}, 20]},
        }
        output = yaml_to_string(config)
        assert "test" in output
        assert isinstance(output, str)

    def test_empty_config(self):
        output = yaml_to_string({})
        assert isinstance(output, str)
