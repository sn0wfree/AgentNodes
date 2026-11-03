"""H 档补测 —— `apps/alpha_zoo_convert.py` 的转换规则单元测试。

这个模块（549 行 / 53%）是 Alpha Zoo 的 ``.py → .yaml`` 一次性迁移工具。
已有两份测试覆盖了**端到端**（analyze → convert → convert_zoo）；缺的是
中间那批**纯 dict→dict 转换规则**：rolling/ewm 链合并、np./pd. 前缀调用、
ref 解析、span-from-alpha 换算。

这些都是无 IO 的纯函数，用一个 identity 转换回调就能逐条断言。
它们承载的语义值得锁死：迁移脚本一旦把 ``close.rolling(20).mean()``
错译成别的东西，转换出的 YAML 因子与原 .py 就**静默不等价**了。
"""
from __future__ import annotations

import pytest

from strategy_research.apps import alpha_zoo_convert as az


def ident(node):
    """Trivial converter: return the node unchanged (used as `convert`)."""
    return node


def _val(v):
    return {"_type": "value", "value": v}


def _kw(name, v):
    return {"_type": "kwarg", "name": name, "value": v}


def _ref(name):
    return {"_type": "ref", "name": name}


# ── _span_from_alpha ────────────────────────────────────────


def test_span_from_alpha_scalar():
    """span = 2/alpha - 1：alpha=0.1 → 19（round 后）。"""
    assert az._span_from_alpha(_val(0.1)) == {"value": 19}


def test_span_from_alpha_div_form():
    """alpha = 2/20 = 0.1 → span 19（div 形式）。"""
    alpha = {"_type": "call", "func": "div", "args": [_val(2), _val(20)]}
    assert az._span_from_alpha(alpha) == {"value": 19}


def test_span_from_alpha_non_positive_falls_back():
    assert az._span_from_alpha(_val(0)) == {"value": 20}
    assert az._span_from_alpha(_val(-1)) == {"value": 20}


def test_span_from_alpha_non_dict_falls_back():
    assert az._span_from_alpha("nonsense") == {"value": 20}


def test_span_from_alpha_div_by_zero_falls_back():
    alpha = {"_type": "call", "func": "div", "args": [_val(2), _val(0)]}
    assert az._span_from_alpha(alpha) == {"value": 20}


# ── _extract_window / _extract_span ─────────────────────────


def test_extract_window_default():
    assert az._extract_window([]) == {"value": 20}


def test_extract_window_from_kwarg():
    assert az._extract_window([_kw("window", _val(60))]) == _val(60)


def test_extract_window_from_positional_value():
    assert az._extract_window([_val(30)]) == _val(30)


def test_extract_span_default():
    assert az._extract_span([]) == {"value": 20}


def test_extract_span_from_span_kwarg():
    assert az._extract_span([_kw("span", _val(10))]) == _val(10)


def test_extract_span_from_alpha_kwarg():
    assert az._extract_span([_kw("alpha", _val(0.1))]) == {"value": 19}


# ── _convert_ref ────────────────────────────────────────────


def test_convert_ref_np_is_none():
    """np 前缀在 ref 位置不产生值（由 prefixed-call 分支处理）。"""
    assert az._convert_ref("np", set(), {}) is None


def test_convert_ref_builtin_type_is_value():
    assert az._convert_ref("float", set(), {}) == {"value": "float"}


def test_convert_ref_panel_key_becomes_ref():
    assert az._convert_ref("close", {"close"}, {}) == {"ref": "close"}


def test_convert_ref_helper_maps_to_operator():
    """局部 helper → 对应算子名（_rolling_sum → ts_sum）。"""
    out = az._convert_ref("my_helper", set(), {"my_helper": "_rolling_sum"})
    assert out == {"op": "ts_sum"}


def test_convert_ref_unknown_falls_back_to_ref():
    assert az._convert_ref("something_else", set(), {}) == {"ref": "something_else"}


# ── _convert_rolling / _convert_ewm ─────────────────────────


def test_convert_rolling_builds_placeholder():
    out = az._convert_rolling({"obj": _ref("close"), "window": _val(20)}, ident)
    assert out["_type"] == "rolling"
    assert out["obj"] == _ref("close")
    assert out["window"] == _val(20)


def test_convert_rolling_empty_obj_is_none():
    assert az._convert_rolling({}, ident) is None


def test_convert_ewm_builds_ewm_mean():
    out = az._convert_ewm({"obj": _ref("close"), "span": _val(10)}, ident)
    assert out["op"] == "ewm_mean"
    assert out["args"] == [_ref("close"), _val(10)]


def test_convert_ewm_empty_obj_is_none():
    assert az._convert_ewm({}, ident) is None


# ── _handle_rolling_chain ──────────────────────────────────


def test_rolling_chain_close_rolling_mean():
    """close.rolling(20).mean() → ts_mean(close, 20)（迁移的核心语义）。"""
    rolling_obj = {"_type": "rolling", "obj": _ref("close"), "window": _val(20)}
    out = az._handle_rolling_chain(rolling_obj, "mean", [], ident)
    assert out == {"op": "ts_mean", "args": [_ref("close"), _val(20)]}


@pytest.mark.parametrize("method,op", [
    ("std", "ts_std"), ("sum", "ts_sum"), ("min", "ts_min"),
    ("max", "ts_max"), ("rank", "ts_rank"), ("median", "ts_median"),
])
def test_rolling_chain_method_map(method, op):
    rolling_obj = {"_type": "rolling", "obj": _ref("close"), "window": _val(5)}
    out = az._handle_rolling_chain(rolling_obj, method, [], ident)
    assert out["op"] == op


def test_rolling_chain_unknown_method_returns_sentinel():
    rolling_obj = {"_type": "rolling", "obj": _ref("close"), "window": _val(5)}
    assert az._handle_rolling_chain(rolling_obj, "not_a_method", [], ident) \
        is az._NO_EARLY_RETURN


def test_ewm_chain_method_map():
    ewm_obj = {"op": "ewm_mean", "args": [_ref("close"), _val(10)]}
    out = az._handle_rolling_chain(ewm_obj, "std", [], ident)
    assert out["op"] == "ewm_std"


def test_plain_object_has_no_rolling_chain():
    assert az._handle_rolling_chain(_ref("close"), "mean", [], ident) \
        is az._NO_EARLY_RETURN


# ── _convert_method_call ───────────────────────────────────


def test_method_call_rolling_builds_window_node():
    expr = {"_type": "method_call", "obj": _ref("close"),
            "method": "rolling", "args": [_val(20)]}
    out = az._convert_method_call(expr, {"close"}, {}, ident)
    assert out == {"_type": "rolling", "obj": _ref("close"), "window": _val(20)}


def test_method_call_ewm_converts_obj_and_span():
    expr = {"_type": "method_call", "obj": _ref("close"),
            "method": "ewm", "args": [_kw("span", _val(10))]}
    out = az._convert_method_call(expr, {"close"}, {}, ident)
    assert out == {"op": "ewm_mean", "args": [_ref("close"), _val(10)]}


def test_method_call_generic_becomes_op():
    expr = {"_type": "method_call", "obj": _ref("close"),
            "method": "abs", "args": []}
    out = az._convert_method_call(expr, {"close"}, {}, ident)
    assert out == {"op": "abs", "args": [_ref("close")]}


def test_method_call_skips_kwargs_in_generic_path():
    expr = {"_type": "method_call", "obj": _ref("close"),
            "method": "clip", "args": [_val(0), _kw("upper", _val(1))]}
    out = az._convert_method_call(expr, {"close"}, {}, ident)
    assert out["args"] == [_ref("close"), _val(0)]


# ── _convert_prefixed_call ─────────────────────────────────


def test_prefixed_np_call_becomes_op():
    out = az._convert_prefixed_call(_ref("np"), "sqrt", [_ref("close")], ident)
    assert out == {"op": "sqrt", "args": [_ref("close")]}


def test_prefixed_np_ones_like_tolerates_unconvertible_arg():
    """ones_like/full_like 允许参数转换失败（跳过而非整体失败）。"""
    out = az._convert_prefixed_call(_ref("np"), "ones_like", [{"_type": "drop"}],
                                    lambda n: None)
    assert out == {"op": "ones_like", "args": []}


def test_prefixed_np_strict_for_other_funcs():
    out = az._convert_prefixed_call(_ref("np"), "sqrt", [{"_type": "drop"}],
                                    lambda n: None)
    assert out is None


def test_prefixed_pd_dataframe_becomes_to_df():
    """pd.DataFrame(...) 在 YAML 侧是 to_df 算子（不是同名 DataFrame）。"""
    out = az._convert_prefixed_call(_ref("pd"), "DataFrame", [_ref("close")], ident)
    assert out == {"op": "to_df", "args": [_ref("close")]}


def test_prefixed_non_ref_returns_sentinel():
    out = az._convert_prefixed_call(_val(1), "sqrt", [], ident)
    assert out is az._NO_EARLY_RETURN


def test_prefixed_pd_non_dataframe_returns_sentinel():
    out = az._convert_prefixed_call(_ref("pd"), "Series", [], ident)
    assert out is az._NO_EARLY_RETURN


# ── _convert_call ──────────────────────────────────────────


def test_convert_call_plain_op():
    expr = {"_type": "call", "func": "ts_mean", "args": [_ref("close"), _val(20)]}
    out = az._convert_call(expr, {"close"}, {}, ident)
    assert out == {"op": "ts_mean", "args": [_ref("close"), _val(20)]}


def test_convert_call_helper_ref_maps_and_renames():
    """func 是局部 helper 引用 → 映射到 helper op，再经 rename。"""
    expr = {"_type": "call", "func": "f", "args": [_ref("close"), _val(20)]}
    out = az._convert_call(expr, {"close"}, {"f": "_rolling_mean"}, ident)
    assert out["op"] == "ts_mean"


def test_convert_call_skip_helper_returns_first_arg():
    """_ind_neutralize / _bench_close 标记 __SKIP__ → 取其首个参数。"""
    expr = {"_type": "call", "func": "f", "args": [_kw("x", _val(1)), _ref("close")]}
    out = az._convert_call(expr, {"close"}, {"f": "_bench_close"}, ident)
    assert out == _ref("close")


def test_convert_call_skip_helper_no_args_is_none():
    expr = {"_type": "call", "func": "f", "args": []}
    assert az._convert_call(expr, set(), {"f": "_bench_close"}, ident) is None


def test_convert_call_skips_kwarg_args():
    expr = {"_type": "call", "func": "ts_mean",
            "args": [_ref("close"), _val(5), _kw("min_periods", _val(1))]}
    out = az._convert_call(expr, {"close"}, {}, ident)
    assert out["args"] == [_ref("close"), _val(5)]


# ── Python AST → YAML 端到端（单表达式） ───────────────────


def test_analyze_compute_function_reports_structure(tmp_path):
    """返回 {meta, analyzer, complexity, error}——参数名在 analyzer 里。"""
    src = tmp_path / "f.py"
    src.write_text(
        '"""factor doc"""\n'
        "def compute(close, volume):\n"
        "    return close.rolling(20).mean()\n",
        encoding="utf-8",
    )
    info = az.analyze_compute_function(src)
    assert set(info) == {"meta", "analyzer", "complexity", "error"}
    assert info["error"] is None
    assert info["analyzer"] is not None
    assert isinstance(info["meta"], dict)


def test_analyze_missing_compute_reports_error(tmp_path):
    """没有 compute() → error 字段非空（而不是抛）。"""
    src = tmp_path / "f.py"
    src.write_text("def other():\n    return 1\n", encoding="utf-8")
    info = az.analyze_compute_function(src)
    assert info["error"]


def test_convert_py_to_yaml_rolling_chain(tmp_path):
    """端到端：.py 里的 rolling().mean() 落到 YAML 的 ts_mean。"""
    src = tmp_path / "f.py"
    src.write_text(
        '"""momentum"""\n'
        "def compute(close):\n"
        "    return close.rolling(20).mean()\n",
        encoding="utf-8",
    )
    cfg = az.convert_py_to_yaml(src)
    assert cfg is not None
    text = az.yaml_to_string(cfg)
    assert "ts_mean" in text


def test_yaml_to_string_roundtrip_parses():
    """输出必须是合法 YAML（能被 yaml.safe_load 读回）。"""
    import yaml as _yaml

    cfg = {"id": "f1", "steps": [{"op": "ts_mean"}], "final": {"ref": "close"}}
    text = az.yaml_to_string(cfg)
    parsed = _yaml.safe_load(text)
    assert parsed["id"] == "f1"
