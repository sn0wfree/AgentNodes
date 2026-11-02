"""H 档补测（P1-7）—— `config_runner.py` 的 FactorStrategy 与 run_from_yaml。

已有测试覆盖了 YAML 装配与 load_data 编排；这里补**策略本体**：

- ``_load_long_ohlcv``：缺 workspace/strategy_name 必须 fail-fast（不能
  静默返回空）；按 config.codes 过滤幽灵资产。
- ``compute_weights``：无效分数（NaN/inf）绝不能当「最优」被选中——
  0 > 负数会让幽灵资产混进权重，这是 docs 里点名的毒化路径。
- ``_compute_scores`` / ``_compute_weights_for_selected`` 的边界。
- ``run_from_yaml``：空数据必须抛 ValueError，而不是拿空 panel 跑引擎。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_research.core import config_runner as cr


def _factor_strategy(tmp_path=None, factors=None, params=None, codes=None):
    return cr.FactorStrategy(
        factors if factors is not None else [{"name": "f"}],
        params or {},
        workspace_path=tmp_path,
        strategy_name="s1" if tmp_path else None,
        codes=codes,
    )


def _panel(cols=("A", "B", "C"), n=80) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.01, (n, len(cols))), axis=0),
        index=idx, columns=list(cols),
    )


# ── _load_long_ohlcv 的 fail-fast ────────────────────────


def test_load_long_ohlcv_requires_workspace():
    s = cr.FactorStrategy([{"name": "f"}], {})
    with pytest.raises(RuntimeError, match="workspace_path and strategy_name"):
        s._load_long_ohlcv(pd.Timestamp("2024-06-01"))


def test_load_long_ohlcv_requires_openable_db(tmp_path):
    s = _factor_strategy(tmp_path)
    with pytest.raises(RuntimeError, match="Cannot open DuckDB"):
        s._load_long_ohlcv(pd.Timestamp("2024-06-01"))


def test_load_long_ohlcv_filters_to_configured_codes(tmp_path):
    from strategy_research.core.db import init_db, save_ohlcv_to_db

    assert init_db(tmp_path) is True
    idx = pd.bdate_range("2024-01-01", periods=10)
    frames = {
        code: pd.DataFrame(
            {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
            index=pd.DatetimeIndex(idx, name="trade_date"))
        for code in ("A", "B", "GHOST")
    }
    save_ohlcv_to_db(tmp_path, frames, "s1")

    s = _factor_strategy(tmp_path, codes=["A", "B"])
    df = s._load_long_ohlcv(pd.Timestamp("2024-12-31"))
    assert set(df["asset_code"]) == {"A", "B"}


def test_load_long_ohlcv_respects_date_cutoff(tmp_path):
    from strategy_research.core.db import init_db, save_ohlcv_to_db

    assert init_db(tmp_path) is True
    idx = pd.bdate_range("2024-01-01", periods=20)
    frame = {"A": pd.DataFrame(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
        index=pd.DatetimeIndex(idx, name="trade_date"))}
    save_ohlcv_to_db(tmp_path, frame, "s1")

    s = _factor_strategy(tmp_path)
    cutoff = idx[4]
    df = s._load_long_ohlcv(cutoff)
    assert pd.to_datetime(df["date"]).max() <= cutoff


# ── compute_weights：无效分数不得被选中 ─────────────────


def test_compute_weights_excludes_nan_scores():
    """幽灵资产（NaN 分数）绝不能被 nlargest 选中。"""
    s = _factor_strategy()
    panel = _panel()
    scores = pd.Series({"A": -0.5, "B": -0.9, "C": np.nan})
    s._compute_scores = lambda *a, **k: scores

    w = s.compute_weights(pd.Timestamp("2024-03-01"), panel, pd.Series(dtype=float))
    assert "C" not in w
    assert set(w) <= {"A", "B"}


def test_compute_weights_returns_empty_when_all_scores_invalid():
    """全部无效时返回空权重（引擎净值不动），绝不产出 NaN。"""
    s = _factor_strategy()
    s._compute_scores = lambda *a, **k: pd.Series(
        {"A": np.nan, "B": np.nan}, dtype=float)
    w = s.compute_weights(pd.Timestamp("2024-03-01"), _panel(), pd.Series(dtype=float))
    assert w == {}


def test_compute_weights_replaces_infinities():
    s = _factor_strategy()
    scores = pd.Series({"A": np.inf, "B": 1.0, "C": -np.inf})
    s._compute_scores = lambda *a, **k: scores
    w = s.compute_weights(pd.Timestamp("2024-03-01"), _panel(), pd.Series(dtype=float))
    assert set(w) <= {"B"}


def test_top_n_limits_selection():
    s = _factor_strategy(params={"top_n": 2})
    scores = pd.Series({"A": 3.0, "B": 2.0, "C": 1.0})
    s._compute_scores = lambda *a, **k: scores
    w = s.compute_weights(pd.Timestamp("2024-03-01"), _panel(), pd.Series(dtype=float))
    assert set(w) == {"A", "B"}


# ── _compute_scores ──────────────────────────────────────


def test_compute_scores_blends_with_weights():
    s = _factor_strategy(factors=[
        {"name": "a", "weight": 2.0},
        {"name": "b", "weight": 1.0},
    ])
    panel = _panel(cols=("A", "B"))
    date = panel.index[-1]
    fv = {
        "a": pd.Series({"A": 1.0, "B": 2.0}),
        "b": pd.Series({"A": 3.0, "B": 0.0}),
    }
    scores = s._compute_scores(date, panel, fv)
    assert scores["A"] == pytest.approx(2.0 * 1.0 + 3.0)
    assert scores["B"] == pytest.approx(2.0 * 2.0 + 0.0)


def test_compute_scores_skips_missing_factor():
    s = _factor_strategy(factors=[{"name": "a"}, {"name": "missing"}])
    panel = _panel(cols=("A", "B"))
    scores = s._compute_scores(
        panel.index[-1], panel, {"a": pd.Series({"A": 1.0, "B": 1.0})})
    assert scores["A"] == pytest.approx(1.0)


def test_compute_scores_uses_last_row_when_date_missing():
    s = _factor_strategy(factors=[{"name": "a"}])
    panel = _panel(cols=("A", "B"))
    fv_df = pd.DataFrame({"A": [1.0, 5.0], "B": [1.0, 7.0]},
                         index=pd.to_datetime(["2000-01-01", "2000-01-02"]))
    scores = s._compute_scores(
        pd.Timestamp("2024-01-01"), panel, {"a": fv_df})
    assert scores["A"] == pytest.approx(5.0)


def test_compute_scores_aligns_missing_columns_to_zero():
    s = _factor_strategy(factors=[{"name": "a"}])
    panel = _panel(cols=("A", "B", "C"))
    scores = s._compute_scores(
        panel.index[-1], panel, {"a": pd.Series({"A": 1.0})})
    assert scores["B"] == 0.0 and scores["C"] == 0.0


# ── _compute_weights_for_selected ────────────────────────


def test_inverse_vol_weights_sum_to_one():
    s = _factor_strategy(params={"weight_method": "inverse_vol"})
    panel = _panel(cols=("A", "B"))
    w = s._compute_weights_for_selected(panel.index[-1], panel, ["A", "B"])
    assert sum(w.values()) == pytest.approx(1.0)
    assert set(w) == {"A", "B"}


def test_equal_weight_fallback_for_unknown_method():
    s = _factor_strategy(params={"weight_method": "mystery"})
    panel = _panel(cols=("A", "B"))
    w = s._compute_weights_for_selected(panel.index[-1], panel, ["A", "B"])
    assert w == {"A": 0.5, "B": 0.5}


def test_inverse_vol_degenerates_to_equal_weight_without_history():
    """历史不足（全 NaN 波动）→ 等权，而不是除零。"""
    s = _factor_strategy(params={"weight_method": "inverse_vol"})
    panel = pd.DataFrame({"A": [np.nan, np.nan], "B": [np.nan, np.nan]})
    w = s._compute_weights_for_selected(panel.index[-1], panel, ["A", "B"])
    assert w == {"A": 0.5, "B": 0.5}


# ── on_risk_check ───────────────────────────────────────


def test_on_risk_check_caps_max_weight():
    s = _factor_strategy(params={"max_weight": 0.4})
    w = {"A": 0.9, "B": 0.05, "C": 0.05}
    out = s.on_risk_check(w, pd.Series(dtype=float), pd.Timestamp("2024-01-01"))
    assert out["A"] <= 0.4 + 1e-9
    assert sum(out.values()) == pytest.approx(1.0)


# ── run_from_yaml ────────────────────────────────────────


def _write_cfg(tmp_path: Path) -> Path:
    p = tmp_path / "c.yaml"
    p.write_text(
        "strategy:\n  name: s1\n"
        "data:\n  source: duckdb\n  codes: ['A']\n"
        "  start_date: '2024-01-01'\n  end_date: '2024-12-31'\n"
        "factors:\n  - name: f\n    code: 'close'\n",
        encoding="utf-8")
    return p


def test_run_from_yaml_raises_on_empty_data(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    monkeypatch.setattr(cr, "load_data", lambda *a, **k: pd.DataFrame())
    with pytest.raises(ValueError, match="数据为空"):
        cr.run_from_yaml(cfg, tmp_path)


def test_run_from_yaml_wires_strategy_and_engine(tmp_path, monkeypatch):
    """跑通装配链：YAML → strategy → engine → result，并回填 factor_failures。"""
    cfg = _write_cfg(tmp_path)
    panel = _panel(cols=("A",))
    monkeypatch.setattr(cr, "load_data", lambda *a, **k: panel)

    class FakeEngine:
        def run(self, **kwargs):
            class R:
                nav_daily = pd.Series([1.0, 1.01])
                weights_history: dict = {}
                metrics: dict = {"calmar": 1.0}
            assert "price_panel" in kwargs
            return R()

    monkeypatch.setattr(cr, "create_engine", lambda cfg_: FakeEngine())
    result = cr.run_from_yaml(cfg, tmp_path)
    assert result.metrics["calmar"] == 1.0
    assert result.factor_failures == []


def test_run_from_yaml_normalises_per_column(tmp_path, monkeypatch):
    """归一化必须按列的首个有效值——不能用 iloc[0]（前导 NaN 会污染整列）。"""
    cfg = _write_cfg(tmp_path)
    idx = pd.bdate_range("2024-01-01", periods=3)
    panel = pd.DataFrame(
        {"A": [100.0, 110.0, 121.0], "B": [np.nan, 50.0, 55.0]},
        index=idx)
    monkeypatch.setattr(cr, "load_data", lambda *a, **k: panel)
    seen = {}

    class FakeEngine:
        def run(self, **kwargs):
            seen["panel"] = kwargs["price_panel"]

            class R:
                nav_daily = pd.Series(dtype=float)
                weights_history: dict = {}
                metrics: dict = {}
            return R()

    monkeypatch.setattr(cr, "create_engine", lambda cfg_: FakeEngine())
    cr.run_from_yaml(cfg, tmp_path)
    cols = seen["panel"]
    assert cols["A"].iloc[0] == pytest.approx(1.0)
    # bfill 只影响**除数**：B 的除数是首个有效值 50，所以 B 第 2 行是 1.0；
    # 第 1 行本身仍是 NaN / 50 = NaN（原始缺失不被伪造）。
    assert cols["B"].iloc[1] == pytest.approx(1.0)
    assert np.isnan(cols["B"].iloc[0])
