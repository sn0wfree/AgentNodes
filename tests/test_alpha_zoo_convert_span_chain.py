"""H 档补测（P1-1c）—— `alpha_zoo_convert.py` 中段：span 换算与重赋值链。

两处语义容易静默出错：

1. ``_extract_span`` / ``_span_from_alpha``：alpha → span 的换算是
   ``span = 2/alpha - 1``。算错就是**不同的平滑窗口**，回测结果偏移但
   不报错。文件里还有非数值 alpha、除零、非 dict 三类兜底。
2. ``_post_process_refs``：同一个变量被赋值三次时，转换器会链化成
   ``x`` / ``x_2`` / ``x_3``，并保证每一步引用的是**上一步**而不是自己。
   链错会让因子变成自引用或跳步，同样不报错。
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from strategy_research.core import alpha_zoo_convert as az


def _val(v):
    return {"_type": "value", "value": v}


def _kw(name, v):
    return {"_type": "kwarg", "name": name, "value": v}


def _analyzer(src: str) -> az.ComputeFunctionAnalyzer:
    fn = az.ComputeFunctionAnalyzer()
    fn.visit(ast.parse(textwrap.dedent(src)))
    return fn


# ── _extract_span 的每个分支 ───────────────────────────────


def test_extract_span_from_alpha_kwarg():
    """ewm(alpha=0.1) → span 19。"""
    assert az._extract_span([_kw("alpha", _val(0.1))]) == {"value": 19}


def test_extract_span_from_alpha_div_kwarg():
    """ewm(alpha=2/20) → span 19。"""
    div = {"_type": "call", "func": "div", "args": [_val(2), _val(20)]}
    assert az._extract_span([_kw("alpha", div)]) == {"value": 19}


def test_extract_span_prefers_last_positional_value():
    """位置值会覆盖默认值——取最后一个。"""
    assert az._extract_span([_val(10), _val(60)]) == _val(60)


def test_extract_span_earlier_kwarg_does_not_win_over_later_value():
    """kwarg 后跟位置值时，位置值生效（代码按顺序赋 span）。"""
    assert az._extract_span([_kw("span", _val(30)), _val(45)]) == _val(45)


# ── _span_from_alpha 边缘形态 ──────────────────────────────


def test_span_from_alpha_div_with_non_numeric_operand_falls_back():
    """div 的操作数不是字面值时无法换算——保留默认 span 20。"""
    alpha = {"_type": "call", "func": "div", "args": [
        {"_type": "ref", "name": "x"}, _val(20)]}
    assert az._span_from_alpha(alpha) == {"value": 20}


def test_span_from_alpha_div_with_wrong_arity_falls_back():
    alpha = {"_type": "call", "func": "div", "args": [_val(2)]}
    assert az._span_from_alpha(alpha) == {"value": 20}


def test_span_from_alpha_negative_div_result_falls_back():
    """负 alpha 换算出非法 span——必须回落，不能产出负数窗口。"""
    alpha = {"_type": "call", "func": "div", "args": [_val(-2), _val(20)]}
    assert az._span_from_alpha(alpha) == {"value": 20}


def test_span_from_alpha_other_call_func_falls_back():
    alpha = {"_type": "call", "func": "mul", "args": [_val(2), _val(20)]}
    assert az._span_from_alpha(alpha) == {"value": 20}


def test_span_from_alpha_string_value_falls_back():
    """非数值 alpha（如变量名）不能换算。"""
    assert az._span_from_alpha(_val("x")) == {"value": 20}


# ── 重赋值链化 ────────────────────────────────────────────


def test_reassignment_creates_chain_names():
    fn = _analyzer("""
        def compute(panel):
            close = panel["close"]
            close = close.shift(1)
            close = close.shift(1)
            return close
    """)
    assert "close" in fn.assignments
    assert "close_2" in fn.assignments
    assert "close_3" in fn.assignments


def test_reassignment_chain_does_not_self_reference():
    """close_3 必须引用 close_2——直接自引用会让 YAML 循环。"""
    fn = _analyzer("""
        def compute(panel):
            close = panel["close"]
            close = close.shift(1)
            close = close.shift(1)
            return close
    """)
    fn._post_process_refs()
    names = [a.get("name") for a in _walk_refs(fn.assignments["close_3"])]
    assert "close_3" not in names
    assert "close_2" in names


def test_first_reassignment_still_references_base():
    fn = _analyzer("""
        def compute(panel):
            close = panel["close"]
            close = close.shift(1)
            return close
    """)
    fn._post_process_refs()
    names = [a.get("name") for a in _walk_refs(fn.assignments["close_2"])]
    assert "close" in names


def test_deep_chain_each_links_to_previous():
    fn = _analyzer("""
        def compute(panel):
            x = panel["close"]
            x = x.shift(1)
            x = x.shift(2)
            x = x.shift(3)
            return x
    """)
    fn._post_process_refs()
    for depth, expect in ((2, "x"), (3, "x_2"), (4, "x_3")):
        names = [a.get("name") for a in _walk_refs(fn.assignments[f"x_{depth}"])]
        assert expect in names, f"x_{depth} should reference {expect}, got {names}"


def test_multi_target_assign_is_ignored():
    """a = b = expr 不是简化形式，分析器跳过（保持既有行为）。"""
    fn = _analyzer("""
        def compute(panel):
            a = b = 1
            return a
    """)
    assert fn.assignments == {}


# ── visit_Return ──────────────────────────────────────────


def test_return_without_value_leaves_expr_none():
    fn = _analyzer("""
        def compute(panel):
            return
    """)
    assert fn.return_expr is None


# ── _update_refs_in_expr 的容器遍历 ───────────────────────


def test_update_refs_walks_nested_lists():
    fn = az.ComputeFunctionAnalyzer()
    expr = {"_type": "call", "args": [{"_type": "ref", "name": "old"},
                                      [{"_type": "ref", "name": "old"}]]}
    fn._update_refs_in_expr(expr, "old", "new")
    assert expr["args"][0] == {"_type": "ref", "name": "new"}
    assert expr["args"][1][0] == {"_type": "ref", "name": "new"}


def test_update_refs_ignores_non_dict():
    fn = az.ComputeFunctionAnalyzer()
    fn._update_refs_in_expr("not a dict", "old", "new")  # 不应抛


# ── panel_keys 只从赋值收集（澄清既有约定） ────────────────


def test_direct_panel_return_is_not_convertible(tmp_path):
    """``return panel["close"]`` 无法转换：panel_keys 只从赋值语句收集。

    这是既有约定。锁住它，避免有人把「直接返回」误当成 bug 去"修"，
    反而破坏所有现存因子的转换路径。
    """
    p = tmp_path / "f.py"
    p.write_text(textwrap.dedent('''
        """doc"""
        def compute(panel):
            """doc"""
            return panel["close"]
    '''), encoding="utf-8")
    assert az.convert_py_to_yaml(p) is None


def test_assigned_panel_ref_is_convertible(tmp_path):
    p = tmp_path / "f.py"
    p.write_text(textwrap.dedent('''
        """doc"""
        def compute(panel):
            """doc"""
            close = panel["close"]
            return close.pct_change()
    '''), encoding="utf-8")
    cfg = az.convert_py_to_yaml(p)
    assert cfg is not None
    assert cfg["steps"][0] == {"name": "close", "expr": {"column": "close"}}
    assert cfg["final"] == {"op": "pct_change", "args": [{"ref": "close"}]}


# ── 辅助 ──────────────────────────────────────────────────


def _walk_refs(node):
    """收集表达式里所有 ref 节点。"""
    out = []
    if isinstance(node, dict):
        if node.get("_type") == "ref":
            out.append(node)
        for v in node.values():
            out.extend(_walk_refs(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_refs(item))
    return out
