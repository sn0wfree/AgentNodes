"""H 档补测（P1-1b）—— `alpha_zoo_convert.py` 的 where 谓词与 rolling 链。

`_handle_where_method` 用正则从 `ast.dump()` 文本里挖比较语义。这条路径
只在**两种输入**下可达（已实测确认）：

1. ``x.where(lambda v: v > 3, other)`` —— 谓词是 lambda，分析器给 complex；
2. ``x.where((a > 1) | (b > 2), 0)`` —— 位或组合，同样是 complex。

普通写法 ``x.where(x > 3, 0)`` 走 call 节点，**不会**进这个函数；而
``~(cond)`` / ``not (cond)`` 走 ``not_`` call，同样不进。因此本文件只锁
真实可达的两条路径，不虚构输入。

语义重要性：谓词方向译反，因子回测结果整体翻转，而且不会有任何报错。
"""
from __future__ import annotations

import ast

import pytest

from strategy_research.apps import alpha_zoo_convert as az


def _val(v):
    return {"_type": "value", "value": v}


def _complex_of(src: str) -> dict:
    """把一段表达式编译成分析器会产生的 complex 节点。"""
    node = ast.parse(src, mode="eval").body
    return {"_type": "complex", "source": ast.dump(node)}


def _lambda_complex(body_expr: str) -> dict:
    return _complex_of(f"lambda v: {body_expr}")


def ident(node):
    return node


# ── 早退分支（输入不满足前提时交回通用路径） ──────────────


def test_where_without_args_is_not_early_return():
    assert az._handle_where_method({"op": "x"}, [], ident) is az._NO_EARLY_RETURN


def test_where_with_plain_value_arg_is_not_early_return():
    """参数不是 complex 节点 → 不是谓词，交回通用路径。"""
    assert az._handle_where_method({"op": "x"}, [_val(5)], ident) is az._NO_EARLY_RETURN


def test_where_with_unrecognised_complex_returns_not_early_return():
    """complex 但正则一个都匹配不上 → 不猜，交回通用路径。"""
    node = _complex_of("foo(x)")
    assert az._handle_where_method({"op": "x"}, [node], ident) is az._NO_EARLY_RETURN


# ── 路径 1：lambda 谓词里的比较运算符 ──────────────────────


@pytest.mark.parametrize(
    "body,expected_op",
    [
        ("v > 3", "gt"),
        ("v < 3", "lt"),
        ("v >= 3", "gte"),
        ("v <= 3", "lte"),
        ("v == 3", "eq"),
        ("v != 3", "neq"),
    ],
)
def test_lambda_predicate_operator_mapping(body, expected_op):
    """六个比较运算符逐一映射——映射反了，因子语义整体翻转。"""
    out = az._handle_where_method({"op": "self"}, [_lambda_complex(body)], ident)
    assert out["op"] == "where"
    assert out["args"][1]["op"] == expected_op
    assert out["args"][1]["args"][1] == {"value": 3.0}


def test_lambda_predicate_keeps_float_threshold():
    out = az._handle_where_method({"op": "self"}, [_lambda_complex("v > 2.5")], ident)
    assert out["args"][1]["args"][1] == {"value": 2.5}


def test_where_compares_the_called_object_with_itself():
    """比较左侧是「被调用的那个对象」，不是谓词里的字面 ref。"""
    marker = {"op": "self_marker"}
    out = az._handle_where_method(marker, [_lambda_complex("v > 3")], ident)
    assert out["args"][0] is marker
    assert out["args"][1]["args"][0] is marker


def test_lambda_where_ignores_extra_args():
    """两条分支都不消费 args[1] —— where 只产出「保真 + 条件」两个元素。"""
    out = az._handle_where_method({"op": "self"}, [_lambda_complex("v > 3"), _val(0)], ident)
    assert len(out["args"]) == 2


# ── 路径 2：位或组合条件（纯 Name，无比较） ────────────────


def test_bit_or_of_two_names_becomes_logical_or():
    """真实用例形态：``(c - ref).where(up | dn, 0.0)``。

    注意顺序：m1/m3 都是 ``re.search``，但 m1 要求 ``ops=[Op()]`` 且紧随
    ``comparators=[Constant(...)]``。``BinOp(left=Name, op=BitOr, right=Name)``
    不含 Compare，故 m1 不匹配，落到 m3。
    """
    node = _complex_of("up | dn")
    out = az._handle_where_method({"op": "self"}, [node], ident)
    assert out["op"] == "where"
    assert out["args"][1]["op"] == "or_"
    assert out["args"][1]["args"] == [{"ref": "up"}, {"ref": "dn"}]


def test_bit_or_uses_converted_fill_value():
    """位或路径消费 args[1] 作为 fill（与 lambda 路径不同）。"""
    node = _complex_of("up | dn")
    fill = {"op": "fill_marker"}
    out = az._handle_where_method({"op": "self"}, [node, fill], ident)
    assert out["args"][2] is fill


def test_bit_or_defaults_fill_to_converted_obj():
    node = _complex_of("up | dn")
    marker = {"op": "self"}
    out = az._handle_where_method(marker, [node], ident)
    assert out["args"][2] is marker


def test_bit_or_of_comparisons_is_refused_not_mis_translated():
    """``(a > 1) | (b > 2)`` 拒绝转换，而不是静默丢掉第二个分支。

    修复前：单条件正则先命中第一个 ``ops=[Gt()]``，产出 ``gt``，or 结构和
    fill 全部丢失——迁移出的 YAML 与原 .py 不等价且无任何报错。

    修复后：只要 source 里出现 ``op=BitOr()`` 就走 or 分支；操作数不是纯
    Name 时返回 ``_NO_EARLY_RETURN``，交回通用路径。通用路径把 complex
    转成 None，于是整个文件记 skipped（不猜、不产出错误 YAML）。

    宁可漏转换一个文件，也不能静默转换错——这是本模块的取舍。
    """
    node = _complex_of("(a > 1) | (b > 2)")
    out = az._handle_where_method({"op": "self"}, [node], ident)
    assert out is az._NO_EARLY_RETURN
    # 通用路径确实会把 complex 变成 None（最终使 convert 返回 None）
    assert az.python_ast_to_yaml_ast(node, {"a", "b"}) is None


# ── rolling / ewm 链 ─────────────────────────────────────


def test_rolling_corr_appends_second_series():
    """close.rolling(20).corr(volume) —— 第二个序列必须进 args。"""
    converted = {"_type": "rolling", "obj": {"ref": "close"}, "window": _val(20)}
    out = az._handle_rolling_chain(converted, "corr", [{"_type": "ref", "name": "volume"}], ident)
    assert out["op"] == "ts_corr"
    assert out["args"] == [{"ref": "close"}, {"_type": "ref", "name": "volume"}, _val(20)]


def test_rolling_corr_with_unconvertible_arg_returns_none():
    converted = {"_type": "rolling", "obj": {"ref": "close"}, "window": _val(20)}
    out = az._handle_rolling_chain(
        converted, "corr", [{"_type": "complex", "source": "x"}], lambda n: None)
    assert out is None


def test_rolling_without_inner_object_returns_none():
    converted = {"_type": "rolling", "obj": None, "window": _val(20)}
    assert az._handle_rolling_chain(converted, "mean", [], ident) is None


def test_rolling_quantile_becomes_ts_rank():
    converted = {"_type": "rolling", "obj": {"ref": "close"}, "window": _val(20)}
    out = az._handle_rolling_chain(converted, "quantile", [], ident)
    assert out == {"op": "ts_rank", "args": [{"ref": "close"}, _val(20)]}


def test_rolling_apply_returns_none():
    converted = {"_type": "rolling", "obj": {"ref": "close"}, "window": _val(20)}
    assert az._handle_rolling_chain(converted, "apply", [], ident) is None


def test_rolling_unknown_method_falls_through():
    converted = {"_type": "rolling", "obj": {"ref": "close"}, "window": _val(20)}
    assert az._handle_rolling_chain(converted, "mystery", [], ident) is az._NO_EARLY_RETURN


def test_ewm_chain_uses_ewm_map():
    """ewm 链经 ``op == "ewm_mean"`` 分支识别，用另一张映射表。"""
    converted = {"op": "ewm_mean", "args": [{"ref": "close"}, _val(20)]}
    out = az._handle_rolling_chain(converted, "mean", [], ident)
    assert out["op"] == "ewm_mean"
    assert out["args"][0] == {"ref": "close"}


def test_ewm_chain_without_inner_object_falls_through():
    """``args`` 为空时 rolling_type 保持 None → 落到通用路径（非 None）。"""
    converted = {"op": "ewm_mean", "args": []}
    assert az._handle_rolling_chain(converted, "mean", [], ident) is az._NO_EARLY_RETURN


# ── 真实输入的可达性（防止测试自欺） ──────────────────────


def test_lambda_where_really_reaches_the_complex_branch():
    """端到端确认：lambda 谓词在分析后确实是 complex 节点。

    如果哪天分析器改成把 lambda 内联成 call，这个测试会红——那时
    _handle_where_method 的 complex 分支就真的成了死代码，应当删除。
    """
    fn = az.ComputeFunctionAnalyzer()
    analyzed = fn._analyze_expr(ast.parse("x.where(lambda v: v > 3, 0)", mode="eval").body)
    assert analyzed["method"] == "where"
    assert analyzed["args"][0]["_type"] == "complex"


def test_plain_where_does_not_reach_the_complex_branch():
    """反面对照：普通比较谓词是 call 节点，不走这个函数。"""
    fn = az.ComputeFunctionAnalyzer()
    analyzed = fn._analyze_expr(ast.parse("x.where(x > 3, 0)", mode="eval").body)
    assert analyzed["args"][0]["_type"] == "call"


def test_negated_predicate_is_a_not_call_not_complex():
    """`~(cond)` 走 not_ call——不经过 complex 分支（因此也不会被误译）。"""
    fn = az.ComputeFunctionAnalyzer()
    analyzed = fn._analyze_expr(ast.parse("~(ret < 0)", mode="eval").body)
    assert analyzed["_type"] == "call"
    assert analyzed["func"] == "not_"
    assert analyzed["args"][0]["func"] == "lt"
