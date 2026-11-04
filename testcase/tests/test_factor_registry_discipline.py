"""因子入库纪律测试 (QuantaAlpha 三道门的落地)。

覆盖:
- analyze_complexity: DSL 表达式的算子数/自由参数数/特征数静态分析
- register_factor: 复杂度自动登记 + info_channel/parent_hypothesis_id 血统字段
- 旧库迁移: 老_schema factor_registry 补列后新旧行共存
- fail-closed: 无法分词的表达式拒绝注册
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from strategy_research.apps import db
from strategy_research.apps.compute_factor import (
    COMPLEXITY_WEIGHTS,
    analyze_complexity,
)


# ============================================================
# analyze_complexity
# ============================================================

def test_complexity_simple_expression():
    c = analyze_complexity("ts_return(close, 20)")
    assert c["operator_count"] == 1
    assert c["param_count"] == 1
    assert c["feature_count"] == 1
    assert c["features"] == ["close"]
    assert c["expr_length"] == len("ts_return(close, 20)")


def test_complexity_binary_ops_counted():
    # 3 个算子调用 (ts_return, ts_std) + 1 个除法 = 3
    c = analyze_complexity("ts_return(close, 20) / ts_std(close, 20)")
    assert c["operator_count"] == 3
    assert c["param_count"] == 2


def test_complexity_unary_minus_not_counted():
    # 开头的一元负号不算运算; -1 是 power 的参数字面量
    c = analyze_complexity("power(ts_mean(close, 5) / ts_mean(close, 60), -1)")
    assert c["operator_count"] == 4  # power + 2*ts_mean + 1 除法
    assert c["param_count"] == 3  # 5, 60, -1


def test_complexity_distinct_features():
    c = analyze_complexity("ts_corr(close, volume, 10)")
    assert c["feature_count"] == 2
    assert c["features"] == ["close", "volume"]


def test_complexity_score_formula():
    c = analyze_complexity("ts_return(close, 20)")
    expected = (
        COMPLEXITY_WEIGHTS["operator"] * 1
        + COMPLEXITY_WEIGHTS["param"] * 1
        + COMPLEXITY_WEIGHTS["feature"] * 1.0  # log2(2) = 1
    )
    assert abs(c["complexity_score"] - expected) < 1e-9


def test_complexity_unparseable_raises():
    with pytest.raises(ValueError):
        analyze_complexity("ts_mean(close, 20) @#$")


def test_complexity_tolerates_pandas_style_formula():
    """DataStore 层 formula 字段允许 pandas 写法, 统计不炸。"""
    c = analyze_complexity("close.pct_change(20)")
    assert c["operator_count"] == 1
    assert c["param_count"] == 1
    assert c["features"] == ["close"]


def test_complexity_decimal_literal_not_split():
    c = analyze_complexity("signed_power(close, 0.5)")
    assert c["param_count"] == 1  # 0.5 是一个参数, 不是 0 和 5


# ============================================================
# register_factor / get_factors
# ============================================================

@pytest.fixture
def ws(tmp_path) -> Path:
    assert db.init_db(tmp_path)
    return tmp_path


def test_register_factor_autocomputes_complexity(ws: Path):
    ok = db.register_factor(
        ws, "strat_a", "mom_20",
        "rank(ts_return(close, 20))",
        info_channel="momentum",
        parent_hypothesis_id="hyp-001",
    )
    assert ok is True

    factors = db.get_factors(ws, "strat_a")
    assert len(factors) == 1
    f = factors[0]
    assert f["factor_name"] == "mom_20"
    assert f["operator_count"] == 2
    assert f["param_count"] == 1
    assert f["feature_count"] == 1
    assert f["complexity_score"] is not None and f["complexity_score"] > 0
    assert f["info_channel"] == "momentum"
    assert f["parent_hypothesis_id"] == "hyp-001"


def test_register_factor_explicit_complexity_override(ws: Path):
    ok = db.register_factor(
        ws, "strat_a", "f_manual", "close",
        complexity={"operator_count": 0, "param_count": 0,
                    "feature_count": 1, "complexity_score": 3.0},
    )
    assert ok is True
    f = db.get_factors(ws, "strat_a")[0]
    assert f["operator_count"] == 0
    assert f["complexity_score"] == 3.0
    # 未提供的血统字段为空 (与 category/source 的默认行为一致)
    assert not f["info_channel"]
    assert not f["parent_hypothesis_id"]


def test_register_factor_fail_closed_on_bad_expression(ws: Path):
    """无法分词的表达式拒绝注册 (fail-closed), 不留半登记行。"""
    ok = db.register_factor(ws, "strat_a", "bad_f", "ts_mean(close, ) $")
    assert ok is False
    assert db.get_factors(ws, "strat_a") == []


def test_register_factor_replaces_same_name(ws: Path):
    assert db.register_factor(ws, "strat_a", "f", "ts_mean(close, 20)")
    assert db.register_factor(ws, "strat_a", "f", "ts_std(close, 60)")
    factors = db.get_factors(ws, "strat_a")
    assert len(factors) == 1
    assert factors[0]["param_count"] == 1
    assert factors[0]["factor_code"] == "ts_std(close, 60)"


# ============================================================
# 旧库迁移
# ============================================================

_OLD_SCHEMA_SQL = """
CREATE TABLE factor_registry (
    factor_name VARCHAR NOT NULL,
    factor_code VARCHAR NOT NULL,
    factor_type VARCHAR NOT NULL,
    category VARCHAR,
    source VARCHAR,
    lookback_window INTEGER,
    strategy_name VARCHAR NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_by VARCHAR,
    PRIMARY KEY (strategy_name, factor_name)
);
"""


@pytest.fixture
def old_ws(tmp_path) -> Path:
    """模拟升级前创建的旧库 (factor_registry 无新列, 已有一条遗留因子)。"""
    conn = duckdb.connect(str(tmp_path / "data.duckdb"))
    conn.execute(_OLD_SCHEMA_SQL)
    conn.execute(
        "INSERT INTO factor_registry (factor_name, factor_code, factor_type, "
        "category, source, lookback_window, strategy_name) "
        "VALUES ('legacy_f', 'ts_mean(close, 20)', 'asset_ts', 'trend', "
        "'legacy', 20, 'strat_old')"
    )
    conn.close()
    return tmp_path


def test_migration_old_db_register_and_read(old_ws: Path):
    """旧库: register_factor 自动补列, get_factors 新旧行共存。"""
    ok = db.register_factor(
        old_ws, "strat_old", "rev_5",
        "-ts_return(close, 5)",
        info_channel="reversal",
    )
    assert ok is True

    factors = db.get_factors(old_ws, "strat_old")
    assert len(factors) == 2

    by_name = {f["factor_name"]: f for f in factors}
    legacy = by_name["legacy_f"]
    assert legacy["factor_code"] == "ts_mean(close, 20)"
    # 遗留行没有复杂度/血统信息 → None (不编造)
    assert legacy["operator_count"] is None
    assert legacy["complexity_score"] is None
    assert legacy["info_channel"] is None

    new = by_name["rev_5"]
    assert new["operator_count"] == 1
    assert new["param_count"] == 1
    assert new["info_channel"] == "reversal"


def test_migration_old_db_read_only(old_ws: Path):
    """旧库只读查询不炸: 新列缺失时返回 None 而不是报错。"""
    factors = db.get_factors(old_ws, "strat_old")
    assert len(factors) == 1
    assert factors[0]["factor_name"] == "legacy_f"
    assert factors[0]["operator_count"] is None
    assert factors[0]["parent_hypothesis_id"] is None
