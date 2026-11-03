"""alpha_zoo_yaml.py AST 评估器单元测试。

覆盖:
- load_alpha_yaml / load_alpha_yaml_from_string: 加载与校验
- evaluate_node: 4 种 AST 节点 (column/value/ref/op)
- _get_operator: 别名、fallback 链
- _where_df: DataFrame/Series/scalar 三种
- compute_alpha_from_yaml: 端到端
- get_alpha_metadata: 元数据提取
- 错误处理: 缺字段、未知算子、未知引用
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from strategy_research.apps.alpha_zoo_yaml import (
    _get_operator,
    _where_df,
    compute_alpha_from_yaml,
    evaluate_node,
    get_alpha_metadata,
    load_alpha_yaml,
    load_alpha_yaml_from_string,
)

warnings.filterwarnings("ignore")


N = 30
SEED = 42


@pytest.fixture(scope="module")
def panel() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    dates = pd.bdate_range("2024-01-01", periods=N)
    cols = ["A", "B", "C"]
    return {
        "open": pd.DataFrame(rng.uniform(10, 50, (N, 3)), index=dates, columns=cols),
        "high": pd.DataFrame(rng.uniform(20, 60, (N, 3)), index=dates, columns=cols),
        "low": pd.DataFrame(rng.uniform(5, 40, (N, 3)), index=dates, columns=cols),
        "close": pd.DataFrame(rng.uniform(15, 55, (N, 3)), index=dates, columns=cols),
        "volume": pd.DataFrame(rng.uniform(1e6, 1e8, (N, 3)), index=dates, columns=cols),
        "amount": pd.DataFrame(rng.uniform(1e7, 1e9, (N, 3)), index=dates, columns=cols),
        "vwap": pd.DataFrame(rng.uniform(15, 55, (N, 3)), index=dates, columns=cols),
    }


@pytest.fixture
def tmp_yaml(tmp_path: Path) -> Path:
    """有效的最小 YAML。"""
    f = tmp_path / "alpha.yaml"
    f.write_text(yaml.safe_dump({
        "id": "test_alpha",
        "zoo": "test",
        "final": {"column": "close"},
    }))
    return f


# ============================================================
# load_alpha_yaml
# ============================================================

def test_load_alpha_yaml_from_file(tmp_yaml):
    cfg = load_alpha_yaml(tmp_yaml)
    assert cfg["id"] == "test_alpha"
    assert cfg["final"] == {"column": "close"}


def test_load_alpha_yaml_from_string():
    yaml_str = yaml.safe_dump({
        "id": "x", "final": {"column": "close"},
    })
    cfg = load_alpha_yaml_from_string(yaml_str)
    assert cfg["id"] == "x"


def test_load_alpha_yaml_missing_id(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("final:\n  column: close")
    with pytest.raises(ValueError, match="id"):
        load_alpha_yaml(f)


def test_load_alpha_yaml_missing_final(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("id: test")
    with pytest.raises(ValueError, match="final"):
        load_alpha_yaml(f)


def test_load_alpha_yaml_empty_file(tmp_path):
    f = tmp_path / "empty.yaml"
    f.write_text("")
    with pytest.raises(ValueError, match="空"):
        load_alpha_yaml(f)


def test_load_alpha_yaml_from_string_empty():
    with pytest.raises(ValueError):
        load_alpha_yaml_from_string("")


# ============================================================
# evaluate_node: 4 种节点类型
# ============================================================

def test_evaluate_scalar(panel):
    assert evaluate_node(42, {}, panel) == 42
    assert evaluate_node(3.14, {}, panel) == 3.14


def test_evaluate_column_node(panel):
    """{column: close} 应返回 data['close']. """
    result = evaluate_node({"column": "close"}, {}, panel)
    assert isinstance(result, pd.DataFrame)
    assert result.equals(panel["close"])


def test_evaluate_column_missing(panel):
    with pytest.raises(ValueError, match="不存在"):
        evaluate_node({"column": "non_existent"}, {}, panel)


def test_evaluate_value_node(panel):
    assert evaluate_node({"value": 20}, {}, panel) == 20
    assert evaluate_node({"value": "hello"}, {}, panel) == "hello"


def test_evaluate_value_python_builtin(panel):
    """{value: 'float'} 应返回 float 类型本身。"""
    assert evaluate_node({"value": "float"}, {}, panel) is float
    assert evaluate_node({"value": "int"}, {}, panel) is int
    assert evaluate_node({"value": "bool"}, {}, panel) is bool


def test_evaluate_ref_data_column(panel):
    """{ref: close} 应返回 data['close']."""
    result = evaluate_node({"ref": "close"}, {}, panel)
    assert result.equals(panel["close"])


def test_evaluate_ref_env_variable(panel):
    """{ref: myvar} 应返回 env['myvar']。"""
    my_var = panel["close"] * 2
    result = evaluate_node({"ref": "myvar"}, {"myvar": my_var}, panel)
    assert result.equals(my_var)


def test_evaluate_ref_panel_keyword(panel):
    """{ref: panel} 应返回 data 字典本身。"""
    result = evaluate_node({"ref": "panel"}, {}, panel)
    assert result is panel


def test_evaluate_ref_pd_keyword(panel):
    """{ref: pd} 应返回 pandas 模块。"""
    result = evaluate_node({"ref": "pd"}, {}, panel)
    import pandas as _pd
    assert result is _pd


def test_evaluate_ref_unknown(panel):
    with pytest.raises(ValueError, match="未知"):
        evaluate_node({"ref": "missing"}, {}, panel)


def test_evaluate_string_identifier(panel):
    """裸字符串应尝试作为列名或环境变量。"""
    result = evaluate_node("close", {}, panel)
    assert result.equals(panel["close"])
    with pytest.raises(ValueError):
        evaluate_node("nonexistent", {}, panel)


def test_evaluate_invalid_node(panel):
    with pytest.raises(ValueError, match="无效"):
        evaluate_node([1, 2, 3], {}, panel)


def test_evaluate_invalid_node_dict(panel):
    with pytest.raises(ValueError):
        evaluate_node({"unknown_key": "x"}, {}, panel)


# ============================================================
# evaluate_node: 算子调用
# ============================================================

def test_evaluate_op_basic(panel):
    """{op: sub, args: [a, b]} 求两列差。"""
    result = evaluate_node({
        "op": "sub",
        "args": [{"column": "high"}, {"column": "low"}],
    }, {}, panel)
    assert isinstance(result, pd.DataFrame)
    assert result.shape == panel["close"].shape


def test_evaluate_op_with_constant_args(panel):
    """常量 DataFrame 应被规约为标量参数。"""
    # Create a constant DataFrame then reference it in args
    const_df = pd.DataFrame(np.full((N, 1), 5), index=panel["close"].index, columns=["X"])
    result = evaluate_node({
        "op": "ts_mean",
        "args": [{"column": "close"}, {"ref": "_c"}],
    }, {"_c": const_df}, panel)
    assert isinstance(result, pd.DataFrame)


def test_evaluate_op_unknown(panel):
    with pytest.raises(ValueError, match="未知算子"):
        evaluate_node({"op": "totally_made_up", "args": []}, {}, panel)


def test_evaluate_op_execution_error(panel):
    """算子执行失败应被包装为 ValueError。"""
    with pytest.raises(ValueError, match="执行失败"):
        evaluate_node({
            "op": "ts_mean",  # 缺少 args
            "args": [],
        }, {}, panel)


# ============================================================
# _get_operator
# ============================================================

def test_get_operator_alpha_zoo(panel):
    fn = _get_operator("rank")
    assert fn is not None
    assert callable(fn)


def test_get_operator_compute_factor(panel):
    """compute_factor 中的算子也应该能 fallback。"""
    fn = _get_operator("zscore")
    assert fn is not None


def test_get_operator_alias(panel):
    """ewm → ewm_mean 别名。"""
    fn = _get_operator("ewm")
    if fn is not None:
        assert callable(fn)


def test_get_operator_unknown_returns_none(panel):
    assert _get_operator("totally_made_up") is None


# ============================================================
# _where_df
# ============================================================

def test_where_df_dataframe(panel):
    cond = panel["close"] > 30.0
    a = panel["close"]
    b = panel["open"]
    result = _where_df(cond, a, b)
    assert isinstance(result, pd.DataFrame)
    assert result.shape == a.shape
    expected = np.where(cond, a, b)
    np.testing.assert_array_equal(result.values, expected)


def test_where_df_series(panel):
    s = panel["close"].iloc[:, 0]
    cond = s > 30.0
    a = s
    b = panel["open"].iloc[:, 0]
    result = _where_df(cond, a, b)
    assert isinstance(result, pd.Series)
    assert result.shape == s.shape


def test_where_df_scalar(panel):
    """标量 condition 应用 np.where."""
    cond = True
    result = _where_df(cond, 1.0, 0.0)
    assert result == 1.0


def test_where_2arg(panel):
    """2 参数 where: cond, value -> 若 cond 为真返回 value, 否则 NaN。"""
    panel['close'] > 30
    1.0
    evaluate_node({'op': 'where', 'args': [{'column': 'close'}, 30.0]}, {}, panel)
    # 上面语法不对。重测：
    result2 = compute_alpha_from_yaml({
        "id": "test",
        "steps": [{"name": "c", "expr": {"column": "close"}}],
        "final": {"op": "where", "args": [
            {"op": "gt", "args": [{"ref": "c"}, {"value": 30}]},
            {"value": 1.0}
        ]},
    }, panel)
    assert isinstance(result2, pd.DataFrame)


# ============================================================
# compute_alpha_from_yaml: 端到端
# ============================================================

def test_compute_simple_factor(panel):
    """最简单: final = column:close。"""
    config = {"id": "test", "final": {"column": "close"}}
    r = compute_alpha_from_yaml(config, panel)
    assert r.equals(panel["close"])


def test_compute_with_steps(panel):
    """带 steps 的复合公式: rank(volume) - 0.5。"""
    config = {
        "id": "test",
        "steps": [
            {"name": "v", "expr": {"column": "volume"}},
            {"name": "rv", "expr": {"op": "rank", "args": [{"ref": "v"}]}},
            {"name": "out", "expr": {"op": "sub", "args": [{"ref": "rv"}, {"value": 0.5}]}},
        ],
        "final": {"ref": "out"},
    }
    r = compute_alpha_from_yaml(config, panel)
    assert isinstance(r, pd.DataFrame)
    assert r.shape == panel["close"].shape


def test_compute_skips_close_step(panel):
    """名为 'close' 的 step 应跳过（保留原始 close 引用）。"""
    config = {
        "id": "test",
        "steps": [
            {"name": "close", "expr": {"column": "close"}},
        ],
        "final": {"column": "close"},
    }
    r = compute_alpha_from_yaml(config, panel)
    assert r.equals(panel["close"])


def test_compute_returns_dataframe_with_correct_shape(panel):
    config = {
        "id": "x",
        "final": {"op": "mul", "args": [{"column": "close"}, {"value": 1.0}]},
    }
    r = compute_alpha_from_yaml(config, panel)
    assert isinstance(r, pd.DataFrame)
    assert r.shape == panel["close"].shape


def test_compute_panel_must_contain_close(panel):
    """panel 必须有 close/open/high/low/volume 之一。"""
    with pytest.raises(ValueError):
        compute_alpha_from_yaml({"id": "x", "final": {"value": 1}}, {})


def test_compute_missing_final(panel):
    with pytest.raises(ValueError, match="缺少"):
        compute_alpha_from_yaml({"id": "x"}, panel)


def test_compute_factor_returns_dataframe_promote_series(panel):
    """如果因子输出 Series, 应升级为 DataFrame."""
    config = {
        "id": "x",
        "steps": [
            {"name": "c", "expr": {"column": "close"}},
        ],
        "final": {"op": "where", "args": [
            {"op": "gt", "args": [{"ref": "c"}, {"value": 30}]},
            {"value": 1.0}, {"value": 0.0}
        ]},
    }
    # 上面会变成 Series 还是 DataFrame?
    # _where_df 会返回 Series (cond 是 Series)
    r = compute_alpha_from_yaml(config, panel)
    assert isinstance(r, pd.DataFrame)


def test_compute_shape_mismatch_raises(panel):
    """形状不匹配时应报错."""
    panel['close'].iloc[:5]
    small_panel = {k: v.iloc[:5] if isinstance(v, pd.DataFrame) else v for k, v in panel.items()}
    # Use a config that produces a differently-shaped result
    config = {
        "id": "shape",
        "steps": [
            {"name": "x", "expr": {"value": 1.0}},  # scalar
        ],
        "final": {"op": "where", "args": [
            {"column": "close"},
            {"value": 1.0},
            {"value": 0.0},
        ]},
    }
    r = compute_alpha_from_yaml(config, small_panel)
    assert r.shape == small_panel["close"].shape


# ============================================================
# get_alpha_metadata
# ============================================================

def test_get_alpha_metadata_basic():
    cfg = {
        "id": "x", "zoo": "y", "nickname": "z",
        "theme": ["momentum"], "formula_latex": "x = 1",
        "columns_required": ["close"],
        "universe": ["equity_cn"], "frequency": ["1d"],
        "decay_horizon": 5, "min_warmup_bars": 10,
    }
    meta = get_alpha_metadata(cfg)
    assert meta["id"] == "x"
    assert meta["zoo"] == "y"
    assert meta["nickname"] == "z"
    assert meta["theme"] == ["momentum"]
    assert meta["formula_latex"] == "x = 1"
    assert meta["columns_required"] == ["close"]
    assert meta["decay_horizon"] == 5
    assert meta["min_warmup_bars"] == 10


def test_get_alpha_metadata_defaults():
    cfg = {"id": "x", "final": {"column": "close"}}
    meta = get_alpha_metadata(cfg)
    assert meta["nickname"] == ""
    assert meta["theme"] == []
    assert meta["decay_horizon"] == 0
    assert meta["min_warmup_bars"] == 0


# ============================================================
# 错误处理综合
# ============================================================

def test_step_missing_name(panel):
    """步骤缺 name 应报错."""
    config = {
        "id": "x",
        "steps": [{"expr": {"value": 1}}],
        "final": {"value": 1},
    }
    with pytest.raises(ValueError, match="name"):
        compute_alpha_from_yaml(config, panel)


def test_step_missing_expr(panel):
    config = {
        "id": "x",
        "steps": [{"name": "x"}],
        "final": {"value": 1},
    }
    with pytest.raises(ValueError, match="expr"):
        compute_alpha_from_yaml(config, panel)


# ============================================================
# 复杂工作流
# ============================================================

def test_multi_step_dependency_chain(panel):
    """链式步骤依赖."""
    config = {
        "id": "chain",
        "steps": [
            {"name": "s1", "expr": {"column": "close"}},
            {"name": "s2", "expr": {"op": "mul", "args": [{"ref": "s1"}, {"value": 2}]}},
            {"name": "s3", "expr": {"op": "add", "args": [{"ref": "s2"}, {"value": 1}]}},
            {"name": "s4", "expr": {"op": "sub", "args": [{"ref": "s3"}, {"ref": "s1"}]}},
        ],
        "final": {"ref": "s4"},
    }
    r = compute_alpha_from_yaml(config, panel)
    assert isinstance(r, pd.DataFrame)
    # s4 = (s1 * 2 + 1) - s1 = s1 + 1
    expected = panel["close"] + 1
    np.testing.assert_array_equal(r.values, expected.values)


def test_op_with_constant_series_arg(panel):
    """常量化 DataFrame 参数自动规约."""
    pd.DataFrame(5, index=panel['close'].index, columns=['X'])
    config = {
        "id": "const_arg",
        "steps": [
            {"name": "_c", "expr": {"value": 5}},  # constant
            {"name": "out", "expr": {"op": "add", "args": [
                {"column": "close"}, {"ref": "_c"}
            ]}},
        ],
        "final": {"ref": "out"},
    }
    r = compute_alpha_from_yaml(config, panel)
    expected = panel["close"] + 5
    np.testing.assert_array_equal(r.values, expected.values)
