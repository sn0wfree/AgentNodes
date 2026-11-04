"""H 档补测（P1-1）—— `alpha_zoo_convert.py` 的 AST 分析分支与批量转换。

前两份测试（`test_alpha_zoo_convert.py` / `test_alpha_zoo_convert_rules.py`）
覆盖了 dict→dict 的转换规则与端到端流程，但 AST 分析器内部一批分支没有
断言：``_analyze_call`` 的 float()/int() 折叠与 kwarg 过滤、``_analyze_subscript``
的 panel 下标两种 AST 形态、``_normalize_typed`` 的每个 _type 分支、以及
``convert_zoo`` / ``convert_all_zoos`` 的跳过与失败隔离。

这些分支错了不会崩——只会让迁移出的 YAML 与原 .py 静默不等价，或让
批量转换把某个 zoo 目录**整目录重复转换**（_yaml 套 _yaml）。所以逐条锁。
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from strategy_research.apps import alpha_zoo_convert as az


# ── 辅助 ────────────────────────────────────────────────────

def _analyze(src: str) -> dict:
    """把一段表达式源码交给分析器（取出第一条 return 的 value）。"""
    tree = ast.parse(textwrap.dedent(src))
    ret = next(n for n in ast.walk(tree) if isinstance(n, ast.Return))
    return az.ComputeFunctionAnalyzer()._analyze_expr(ret.value)


def _write_factor(path: Path, expr: str, name: str = "f1") -> Path:
    """写一个最小可转换的因子 .py。

    注意：``panel`` 键集合由**赋值语句**收集（见 convert_py_to_yaml 的
    ``panel_keys``），所以模板必须先 ``close = panel["close"]`` 再返回，
    直接 ``return panel["close"]`` 会因 panel_keys 为空而转换失败。
    这是分析器的既有约定，不是缺陷。
    """
    path.write_text(
        textwrap.dedent(f'''
        """{name}"""
        import numpy as np
        import pandas as pd


        def compute(panel):
            """docstring"""
            close = panel["close"]
            return {expr}
        '''),
        encoding="utf-8",
    )
    return path


# ── _analyze_call：float() / int() 类型转换 ─────────────────


def test_float_of_constant_is_folded():
    """float(0.5) 是常量 → 直接折叠成 value 0.5，不留 call 节点。"""
    node = _analyze("return float(0.5)")
    assert node == {"_type": "value", "value": 0.5}


def test_int_of_constant_is_folded():
    node = _analyze("return int(3)")
    assert node == {"_type": "value", "value": 3}
    assert isinstance(node["value"], int)


def test_int_of_string_constant_is_actually_converted():
    """int('7') 在 Python 里合法——分析器必须真的转，而不是原样透传。"""
    node = _analyze("return int('7')")
    assert node == {"_type": "value", "value": 7}


def test_float_of_non_constant_returns_argument():
    """float(panel["close"]) 不必包一层——类型转换在 YAML 里无意义。"""
    node = _analyze('return float(panel["close"])')
    assert node == {"_type": "panel_ref", "key": "close"}


def test_float_with_wrong_arity_falls_through_to_complex():
    """float(1, 2) 不匹配 arity-1 折叠分支，落到 complex（而非 None）。

    complex 节点在 python_ast_to_yaml_ast 里返回 None，最终使整个文件不可
    转换——这是有意的「认不出来就放弃」，不是静默猜一个。
    """
    node = _analyze("return float(1, 2)")
    assert node["_type"] == "complex"
    assert az.python_ast_to_yaml_ast(node, {"close"}) is None


def test_unknown_bare_function_call_is_a_call_node():
    """任意裸名调用都会成为 call 节点——是否可用由转换阶段判定。"""
    node = az.ComputeFunctionAnalyzer()._analyze_call(
        ast.parse("some_unknown(1)").body[0].value
    )
    assert node == {
        "_type": "call",
        "func": "some_unknown",
        "args": [{"_type": "value", "value": 1}],
    }


# ── _analyze_call：关键字参数处理 ──────────────────────────


def test_method_call_records_kwargs():
    node = _analyze('return close.rolling(window=20)')
    assert node["_type"] == "method_call"
    assert node["method"] == "rolling"
    assert {"_type": "kwarg", "name": "window", "value": {"_type": "value", "value": 20}} in node["args"]


def test_method_call_skips_fill_method_kwarg():
    """pct_change(fill_method=None) 的 kwarg 必须被丢弃——它不是因子语义。"""
    node = _analyze('return close.pct_change(fill_method=None)')
    names = [a.get("name") for a in node["args"] if isinstance(a, dict)]
    assert "fill_method" not in names


def test_function_call_skips_fill_method_kwarg():
    node = _analyze('return ts_mean(close, 20, fill_method=None)')
    assert node["_type"] == "call"
    names = [a.get("name") for a in node["args"] if isinstance(a, dict)]
    assert "fill_method" not in names


def test_function_call_keeps_other_kwargs():
    node = _analyze('return ts_corr(close, volume, window=10)')
    assert {"_type": "kwarg", "name": "window", "value": {"_type": "value", "value": 10}} in node["args"]


# ── _analyze_subscript：panel 下标两种 AST 形态 ────────────


def test_panel_subscript_constant_slice():
    node = _analyze('return panel["close"]')
    assert node == {"_type": "panel_ref", "key": "close"}


def test_non_panel_subscript_returns_none():
    """只有名叫 panel 的变量才被认作数据面板。"""
    node = az.ComputeFunctionAnalyzer()._analyze_subscript(
        ast.parse('foo["close"]').body[0].value
    )
    assert node is None


# ── _get_func_name ─────────────────────────────────────────


def test_get_func_name_bare_name():
    fn = az.ComputeFunctionAnalyzer()
    assert fn._get_func_name(ast.parse("ts_mean(x)").body[0].value) == "ts_mean"


def test_get_func_name_np_prefix():
    """np.nanmean → nanmean（前缀剥离）。"""
    fn = az.ComputeFunctionAnalyzer()
    assert fn._get_func_name(ast.parse("np.nanmean(x)").body[0].value) == "nanmean"


def test_get_func_name_pd_prefix():
    fn = az.ComputeFunctionAnalyzer()
    assert fn._get_func_name(ast.parse("pd.DataFrame(x)").body[0].value) == "DataFrame"


def test_get_func_name_non_call_func_returns_none():
    fn = az.ComputeFunctionAnalyzer()
    # Subscript 形式的 func 不是 Name/Attribute
    assert fn._get_func_name(ast.parse("fs[0](x)").body[0].value) is None


# ── _normalize_typed：每个 _type 分支 ─────────────────────


def test_normalize_value():
    assert az._normalize_typed({"_type": "value", "value": 3}) == 3


def test_normalize_value_missing_defaults_zero():
    assert az._normalize_typed({"_type": "value"}) == 0


def test_normalize_column():
    assert az._normalize_typed({"_type": "column", "key": "close"}) == {"column": "close"}


def test_normalize_panel_ref():
    assert az._normalize_typed({"_type": "panel_ref", "key": "open"}) == {"column": "open"}


def test_normalize_ref():
    assert az._normalize_typed({"_type": "ref", "name": "x"}) == {"ref": "x"}


def test_normalize_call():
    node = {"_type": "call", "func": "ts_mean", "args": [{"_type": "value", "value": 2}]}
    assert az._normalize_typed(node) == {"op": "ts_mean", "args": [2]}


def test_normalize_method_call_prepends_object():
    node = {
        "_type": "method_call",
        "method": "pct_change",
        "obj": {"_type": "panel_ref", "key": "close"},
        "args": [],
    }
    assert az._normalize_typed(node) == {
        "op": "pct_change",
        "args": [{"column": "close"}],
    }


def test_normalize_kwarg_unwraps_value():
    assert az._normalize_typed({"_type": "kwarg", "name": "w", "value": {"_type": "value", "value": 5}}) == 5


@pytest.mark.parametrize("kind", ["rolling", "ewm", "complex"])
def test_normalize_placeholder_types_become_none(kind):
    """这三种是中间态，规范化时必须消失（否则 YAML 里会漏 _type）。"""
    assert az._normalize_typed({"_type": kind}) is None


def test_normalize_unknown_type_strips_marker_only():
    node = {"_type": "mystery", "keep": 1}
    assert az._normalize_typed(node) == {"keep": 1}


def test_normalize_recurses_into_lists_and_dicts():
    """list 里的值节点也会被规范化。

    注意：``_normalize_typed`` 的 value 分支返回的是 ``node.get("value")``
    原样——它不递归进 value 内部。所以 value 里再套 value 不会被展开，
    这是既有行为；这里锁的是 args 列表的元素被逐个规范化。
    """
    node = {"op": "x", "args": [{"_type": "value", "value": 1}, {"_type": "value", "value": 2}]}
    assert az._normalize_yaml(node) == {"op": "x", "args": [1, 2]}


# ── convert_zoo：跳过 / 隔离失败 ───────────────────────────


def test_convert_zoo_skips_underscore_files(tmp_path):
    zoo = tmp_path / "zoo"
    zoo.mkdir()
    _write_factor(zoo / "_private.py", "panel['close']")
    stats = az.convert_zoo(zoo)
    assert stats["success"] == 0 and stats["skipped"] == 0


def test_convert_zoo_counts_success_and_writes_yaml(tmp_path):
    zoo = tmp_path / "zoo"
    zoo.mkdir()
    _write_factor(zoo / "alpha_1.py", "panel['close']")
    stats = az.convert_zoo(zoo)
    assert stats["success"] == 1
    out = tmp_path / "zoo_yaml"
    assert (out / "alpha_1.yaml").exists()


def test_convert_zoo_treats_unparseable_file_as_skipped(tmp_path):
    """语法错误的文件被 analyze 吞成 error → convert 返回 None → 记 skipped。

    这是有意的分级：无法解析 ≠ 转换失败，不该算 failed（否则一个坏文件
    会把批量结果染红）。
    """
    zoo = tmp_path / "zoo"
    zoo.mkdir()
    _write_factor(zoo / "good.py", "close.pct_change()")
    (zoo / "bad.py").write_text("def compute(:\n", encoding="utf-8")

    stats = az.convert_zoo(zoo)
    assert stats["success"] == 1
    assert stats["skipped"] == 1
    assert stats["failed"] == 0
    by_file = {d["file"]: d["status"] for d in stats["details"]}
    assert by_file["bad.py"] == "skipped"
    assert by_file["good.py"] == "success"


def test_convert_zoo_isolates_a_raising_file(tmp_path, monkeypatch):
    """真正抛异常的文件记为 failed，且不影响同批其它文件。"""
    zoo = tmp_path / "zoo"
    zoo.mkdir()
    _write_factor(zoo / "good.py", "close.pct_change()")
    _write_factor(zoo / "boom.py", "close.pct_change()")

    real = az.convert_py_to_yaml

    def flaky(path):
        if path.name == "boom.py":
            raise RuntimeError("simulated converter crash")
        return real(path)

    monkeypatch.setattr(az, "convert_py_to_yaml", flaky)
    stats = az.convert_zoo(zoo)

    assert stats["success"] == 1
    assert stats["failed"] == 1
    failed = next(d for d in stats["details"] if d["status"] == "failed")
    assert failed["file"] == "boom.py"
    assert "simulated converter crash" in failed["error"]


def test_convert_zoo_returns_skipped_when_not_convertible(tmp_path, monkeypatch):
    zoo = tmp_path / "zoo"
    zoo.mkdir()
    _write_factor(zoo / "x.py", "panel['close']")
    monkeypatch.setattr(az, "convert_py_to_yaml", lambda p: None)
    stats = az.convert_zoo(zoo)
    assert stats["skipped"] == 1
    assert stats["details"][0]["status"] == "skipped"


def test_convert_zoo_default_output_dir(tmp_path):
    zoo = tmp_path / "mylib"
    zoo.mkdir()
    _write_factor(zoo / "a.py", "panel['close']")
    az.convert_zoo(zoo)
    assert (tmp_path / "mylib_yaml").is_dir()


# ── convert_all_zoos：递归防护栏 ───────────────────────────


def test_convert_all_zoos_skips_underscore_dirs(tmp_path):
    (tmp_path / "_hidden").mkdir()
    (tmp_path / "real").mkdir()
    _write_factor(tmp_path / "real" / "a.py", "panel['close']")
    stats = az.convert_all_zoos(tmp_path)
    assert "_hidden" not in stats
    assert stats["real"]["success"] == 1


def test_convert_all_zoos_does_not_recurse_into_yaml_output(tmp_path, capsys):
    """核心防护栏：已转换目录若被当输入，会指数增殖 alpha_yaml_yaml_yaml。"""
    (tmp_path / "alpha").mkdir()
    _write_factor(tmp_path / "alpha" / "a.py", "panel['close']")
    first = az.convert_all_zoos(tmp_path)
    assert "alpha" in first

    second = az.convert_all_zoos(tmp_path)
    # alpha_yaml 出现在输出里，但不得被当作输入重新转换
    assert "alpha_yaml" not in second
    assert second.get("__skipped_yaml_dirs__") == ["alpha_yaml"]
    assert not (tmp_path / "alpha_yaml_yaml").exists()


def test_convert_all_zoos_ignores_files(tmp_path):
    (tmp_path / "loose.py").write_text("x = 1", encoding="utf-8")
    stats = az.convert_all_zoos(tmp_path)
    assert "loose.py" not in stats
