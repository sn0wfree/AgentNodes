"""H 档补测（P1-1d）—— `alpha_zoo_convert.py` 的函数调用转换分支。

覆盖 ``_apply_call_special`` 的每个特例与 ``_convert_prefixed_call`` 的
np./pd. 前缀路径。这些分支决定 ``np.where`` / ``pd.DataFrame`` /
``_sma`` 这类写法能否正确迁移——认错就产出语义不同的 YAML。
"""
from __future__ import annotations

import pytest

from strategy_research.apps import alpha_zoo_convert as az


def _val(v):
    return {"_type": "value", "value": v}


def _call(func, *args):
    return {"_type": "call", "func": func, "args": list(args)}


def _ref(name):
    return {"_type": "ref", "name": name}


def ident(node):
    return node


def _convert(expr, panel_keys=None, func_refs=None):
    return az.python_ast_to_yaml_ast(expr, panel_keys or set(), func_refs or {})


# ── _apply_call_special ────────────────────────────────────


def test_sma_drops_third_argument():
    """_sma(x, n, m) → ts_mean(x, n)：m 不参与语义，必须丢掉。"""
    func, out = az._apply_call_special("_sma", [_val(1), _val(20), _val(3)])
    assert func == "ts_mean"
    assert out is az._NO_EARLY_RETURN


def test_sma_with_two_args_is_unchanged():
    func, out = az._apply_call_special("_sma", [_val(1), _val(20)])
    assert func == "ts_mean"
    assert out is az._NO_EARLY_RETURN


def test_sma_with_one_arg_is_unchanged():
    func, _ = az._apply_call_special("_sma", [_val(1)])
    assert func == "ts_mean"


@pytest.mark.parametrize("func", ["_ind_neutralize", "cumprod", "replace", "to_numpy"])
def test_first_argument_passthrough(func):
    """这四个函数只取第一个参数（其余是 pandas 细节，不影响因子）。"""
    out_func, result = az._apply_call_special(func, [_ref("x"), _val(1)])
    assert out_func == func
    assert result == _ref("x")


def test_first_argument_passthrough_with_no_args_returns_none():
    _func, result = az._apply_call_special("cumprod", [])
    assert result is None


def test_bench_close_becomes_close_column():
    """_bench_close() 无参数——直接映射到 close 列。"""
    _func, result = az._apply_call_special("_bench_close", [])
    assert result == {"column": "close"}


def test_vwap_becomes_safe_div():
    """vwap → amount / volume，用 safe_div 防除零。"""
    _func, result = az._apply_call_special("vwap", [])
    assert result == {
        "op": "safe_div",
        "args": [{"column": "amount"}, {"column": "volume"}],
    }


def test_ones_like_becomes_constant_one():
    _func, result = az._apply_call_special("ones_like", [])
    assert result == {"value": 1.0}


def test_full_like_uses_second_arg_as_fill():
    """full_like(x, v) → v（第一个参数只是形状参考）。"""
    _func, result = az._apply_call_special("full_like", [_ref("x"), _val(7)])
    assert result == _val(7)


def test_full_like_with_single_arg_defaults_to_one():
    _func, result = az._apply_call_special("full_like", [_ref("x")])
    assert result == {"value": 1.0}


def test_full_like_without_args_defaults_to_one():
    _func, result = az._apply_call_special("full_like", [])
    assert result == {"value": 1.0}


def test_unknown_function_is_untouched():
    func, out = az._apply_call_special("ts_mean", [_val(1), _val(2)])
    assert func == "ts_mean"
    assert out is az._NO_EARLY_RETURN


# ── _convert_prefixed_call ────────────────────────────────


def test_np_prefix_call_maps_to_op():
    out = az._convert_prefixed_call(_ref("np"), "isfinite", [_ref("x")], ident)
    assert out == {"op": "isfinite", "args": [_ref("x")]}


def test_np_prefix_skips_kwargs():
    kw = {"_type": "kwarg", "name": "axis", "value": _val(0)}
    out = az._convert_prefixed_call(_ref("np"), "log", [_ref("x"), kw], ident)
    assert out == {"op": "log", "args": [_ref("x")]}


def test_np_ones_like_tolerates_unconvertible_arg():
    """np.ones_like 的参数转不出来也不致命——形状参数对因子无意义。"""
    out = az._convert_prefixed_call(_ref("np"), "ones_like", [_ref("x")], lambda n: None)
    assert out == {"op": "ones_like", "args": []}


def test_np_full_like_tolerates_unconvertible_arg():
    out = az._convert_prefixed_call(_ref("np"), "full_like", [_ref("x")], lambda n: None)
    assert out == {"op": "full_like", "args": []}


def test_np_other_func_with_unconvertible_arg_returns_none():
    """除 ones_like/full_like 外，参数转不出来就整体放弃。"""
    out = az._convert_prefixed_call(_ref("np"), "isfinite", [_ref("x")], lambda n: None)
    assert out is None


def test_pd_dataframe_becomes_to_df():
    out = az._convert_prefixed_call(_ref("pd"), "DataFrame", [_ref("x")], ident)
    assert out == {"op": "to_df", "args": [_ref("x")]}


def test_pd_dataframe_with_unconvertible_arg_returns_none():
    out = az._convert_prefixed_call(_ref("pd"), "DataFrame", [_ref("x")], lambda n: None)
    assert out is None


def test_pd_other_method_falls_through():
    out = az._convert_prefixed_call(_ref("pd"), "Series", [_ref("x")], ident)
    assert out is az._NO_EARLY_RETURN


def test_non_ref_object_falls_through():
    out = az._convert_prefixed_call({"op": "x"}, "anything", [], ident)
    assert out is az._NO_EARLY_RETURN


# ── 端到端：这些特例真的可转换 ────────────────────────────


def _factor(tmp_path, body: str, name="f"):
    p = tmp_path / f"{name}.py"
    p.write_text(
        '"""doc"""\nimport numpy as np\nimport pandas as pd\n\n\n'
        "def compute(panel):\n"
        '    """doc"""\n'
        '    close = panel["close"]\n'
        "    amount = panel[\"amount\"]\n"
        '    volume = panel["volume"]\n'
        + "".join(f"    {ln}\n" for ln in body.splitlines())
        if "\n" in body else
        '"""doc"""\nimport numpy as np\nimport pandas as pd\n\n\n'
        "def compute(panel):\n"
        '    """doc"""\n'
        '    close = panel["close"]\n'
        + f"    {body}\n",
        encoding="utf-8",
    )
    return p


def test_vwap_end_to_end(tmp_path):
    p = _factor(tmp_path, "return vwap()")
    cfg = az.convert_py_to_yaml(p)
    assert cfg is not None
    assert cfg["final"] == {
        "op": "safe_div",
        "args": [{"column": "amount"}, {"column": "volume"}],
    }


def test_np_isfinite_end_to_end(tmp_path):
    p = _factor(tmp_path, "return np.isfinite(close)")
    cfg = az.convert_py_to_yaml(p)
    assert cfg is not None
    assert cfg["final"]["op"] == "isfinite"
