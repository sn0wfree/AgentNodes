"""H 档补测（P1-6c）—— `data_readiness.py` 的 C5/C7 检查与降级路径。

C5（数据质量）与 C7（行级质量）此前覆盖稀疏，尤其 C7 整块空白。它们决定
pre-flight 是否放行回测：

- C5：codes 内某资产**全部** OHLC 为 NaN → fail（该资产是残留脏数据）。
  这条 SQL 若把「部分 NaN」也判成 fail，正常资产会被误杀。
- C7：复用 clean_data(dry_run=True) 统计重复/缺失/异常值；任何异常都
  降级为 warn 而不是阻断——注释里写明了「可忽略」。

用真实 DuckDB 种子数据驱动（与既有测试同款），不 mock SQL。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps import data_readiness as dr


def _seed(workspace: Path, strategy: str = "s1", codes=("A", "B"),
          periods: int = 120, clean: bool = True) -> None:
    from strategy_research.apps.db import save_ohlcv_to_db

    dates = pd.bdate_range("2023-01-02", periods=periods)
    rng = np.random.default_rng(11)
    data_map = {}
    for code in codes:
        close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.02, len(dates)))
        df = pd.DataFrame(
            {"open": close * 0.99, "high": close * 1.02,
             "low": close * 0.98, "close": close, "volume": 1_000_000},
            index=pd.DatetimeIndex(dates, name="trade_date"),
        )
        if not clean:
            # 造重复行 + 缺失
            df = pd.concat([df, df.iloc[:3]])
            df.iloc[10, df.columns.get_loc("close")] = np.nan
        data_map[code] = df
    save_ohlcv_to_db(workspace, data_map, strategy)


# ── C5 数据质量 ──────────────────────────────────────────


def test_c5_skips_when_no_codes(tmp_path):
    assert dr._check_quality(tmp_path, "s1", None) == []
    assert dr._check_quality(tmp_path, "s1", []) == []


def test_c5_skips_when_db_missing(tmp_path):
    """空 workspace 没有库 → 不报错，返回空（C0 会先报数据不可用）。"""
    assert dr._check_quality(tmp_path, "s1", ["A"]) == []


def test_c5_ok_for_clean_assets(tmp_path):
    _seed(tmp_path)
    checks = dr._check_quality(tmp_path, "s1", ["A", "B"])
    assert len(checks) == 1
    assert checks[0].status == "ok"


def test_c5_fails_when_asset_is_all_nan(tmp_path):
    """某资产每条记录的 OHLC 都是 NaN → fail（残留脏数据）。"""
    from strategy_research.apps.db import save_ohlcv_to_db

    _seed(tmp_path, codes=("A",))
    ghost_dates = pd.bdate_range("2023-01-02", periods=5)
    ghost = pd.DataFrame(
        {"open": [np.nan] * 5, "high": [np.nan] * 5, "low": [np.nan] * 5,
         "close": [np.nan] * 5, "volume": [0.0] * 5},
        index=pd.DatetimeIndex(ghost_dates, name="trade_date"),
    )
    save_ohlcv_to_db(tmp_path, {"GHOST": ghost}, "s1")

    checks = dr._check_quality(tmp_path, "s1", ["A", "GHOST"])
    assert checks[0].status == "fail"
    assert "GHOST" in checks[0].detail


def test_c5_partial_nan_is_not_a_failure(tmp_path):
    """只有少数行 NaN 是正常缺失，不能被判 fail。"""
    from strategy_research.apps.db import save_ohlcv_to_db

    _seed(tmp_path, codes=("A",))
    dates = pd.bdate_range("2023-06-01", periods=10)
    partial = pd.DataFrame(
        {"open": [1.0] * 10, "high": [1.0] * 10, "low": [1.0] * 10,
         "close": [1.0] * 10, "volume": [1.0] * 10},
        index=pd.DatetimeIndex(dates, name="trade_date"),
    )
    partial.iloc[0] = np.nan
    save_ohlcv_to_db(tmp_path, {"PART": partial}, "s1")

    checks = dr._check_quality(tmp_path, "s1", ["PART"])
    assert checks[0].status == "ok"


def test_c5_returns_empty_on_sql_error(tmp_path, monkeypatch):
    """SQL 层错误降级为 []（不阻断 pre-flight）。"""
    class BoomConn:
        def execute(self, *a, **k):
            raise RuntimeError("no such table")

        def close(self):
            pass

    monkeypatch.setattr(dr, "_db", lambda ws: BoomConn())
    assert dr._check_quality(tmp_path, "s1", ["A"]) == []


# ── C7 行级质量 ──────────────────────────────────────────


def test_c7_empty_when_db_missing(tmp_path):
    assert dr._check_cleaning(tmp_path, "s1") == []


def test_c7_empty_when_no_rows(tmp_path):
    _seed(tmp_path, codes=("A",))
    assert dr._check_cleaning(tmp_path, "other-strategy") == []


def test_c7_ok_for_clean_data(tmp_path):
    _seed(tmp_path, codes=("A", "B"))
    checks = dr._check_cleaning(tmp_path, "s1")
    assert len(checks) == 1
    assert checks[0].id == "C7"
    assert checks[0].status == "ok"


def test_c7_warns_on_dirty_data(tmp_path):
    _seed(tmp_path, codes=("A",), clean=False)
    checks = dr._check_cleaning(tmp_path, "s1")
    assert checks[0].id == "C7"
    assert checks[0].status == "warn"
    # dedup 先跑，重复行被删掉，剩下的问题是缺失填充
    assert ("缺失填充" in checks[0].detail) or ("重复行" in checks[0].detail)
    assert checks[0].fix_hint


def test_c7_warns_on_missing_values(tmp_path):
    """缺值也算问题——但不能升级成 fail 阻断。"""
    _seed(tmp_path, codes=("A",), clean=False)
    checks = dr._check_cleaning(tmp_path, "s1")
    assert checks[0].status == "warn"


def test_c7_degrades_to_warn_on_exception(tmp_path, monkeypatch):
    """clean_data 抛错时给 warn + 「可忽略」，绝不阻断。"""
    import strategy_research.apps.tools.data_clean as dc

    _seed(tmp_path, codes=("A",))

    def boom(*a, **k):
        raise RuntimeError("cleaner exploded")

    monkeypatch.setattr(dc, "clean_data", boom)
    checks = dr._check_cleaning(tmp_path, "s1")
    assert checks[0].status == "warn"
    assert "不可用" in checks[0].detail
    assert "可忽略" in checks[0].fix_hint


# ── 整体：check_data_readiness 汇总 ──────────────────────


def test_full_readiness_includes_c5_and_c7(tmp_path):
    """C7 默认关闭（include_cleaning=False，较重）；显式开启才进报表。"""
    _seed(tmp_path, codes=("A", "B"))
    cfg = {
        "strategy": {"name": "s1", "type": "rotation"},
        "data": {"source": "duckdb", "codes": ["A", "B"],
                 "start_date": "2023-01-02", "end_date": "2023-12-31"},
        "rebalance": {"freq": "M", "min_history": 60},
        "factors": [{"name": "mom", "code": "ts_return(close, 20)"}],
    }
    base = dr.check_data_readiness(tmp_path, "s1", cfg)
    assert "C5" in {c.id for c in base.checks}
    assert "C7" not in {c.id for c in base.checks}

    with_cleaning = dr.check_data_readiness(tmp_path, "s1", cfg, include_cleaning=True)
    assert "C7" in {c.id for c in with_cleaning.checks}
