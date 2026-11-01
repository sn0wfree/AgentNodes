"""H 档补测 —— `core/config_runner.py` 的配置装配与数据加载编排。

此前 63.8%，缺口是三块**纯逻辑**：
1. risk 子配置（vol_targeting / trend_filter / stop_loss）的启用分支
   ——YAML dict → 三个 dataclass 的映射；
2. `_is_data_fresh`（DuckDB 数据新鲜度：7 天窗口、空库、字符串日期）；
3. `load_data` 的编排路径（cache 命中 / online 拉取 / orphan 过滤）。

不碰真实网络：online 路径 monkeypatch `resolve_loader`，断言「拉取 → 落库
→ 读回 → 按 codes 过滤」这条管线本身。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from strategy_research.core import config_runner as cr
from strategy_research.core import db


@pytest.fixture
def ws(tmp_path) -> Path:
    assert db.init_db(tmp_path) is True
    return tmp_path


# ── load_yaml_config ────────────────────────────────────────


def test_load_yaml_config_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="配置文件不存在"):
        cr.load_yaml_config(tmp_path / "nope.yaml")


def test_load_yaml_config_roundtrip(tmp_path):
    y = tmp_path / "c.yaml"
    y.write_text("strategy:\n  name: s1\ndata:\n  codes: [A]\n",
                 encoding="utf-8")
    assert cr.load_yaml_config(y) == {"strategy": {"name": "s1"},
                                      "data": {"codes": ["A"]}}


# ── risk 子配置 ─────────────────────────────────────────────


def _engine_with(risk: dict):
    return cr.create_engine({"risk": risk})


def test_engine_no_risk_sections_disables_all():
    eng = _engine_with({})
    assert eng.vol_targeting is None
    assert eng.trend_filter is None
    assert eng.stop_loss is None


def test_engine_vol_targeting_enabled():
    eng = _engine_with({"vol_targeting": {"enabled": True,
                                          "target_vol": 0.2, "lookback": 40}})
    vt = eng.vol_targeting
    assert vt is not None
    assert vt.enabled is True
    assert vt.target_vol == 0.2
    assert vt.lookback == 40


def test_engine_vol_targeting_disabled_is_none():
    assert _engine_with({"vol_targeting": {"enabled": False}}).vol_targeting is None


def test_engine_trend_filter_enabled():
    eng = _engine_with({"trend_filter": {"enabled": True, "ma_window": 100}})
    tf = eng.trend_filter
    assert tf is not None
    assert tf.ma_window == 100


def test_engine_stop_loss_enabled():
    eng = _engine_with({"stop_loss": {"enabled": True, "threshold": -0.2}})
    sl = eng.stop_loss
    assert sl is not None
    assert sl.threshold == -0.2


def test_cost_config_defaults_and_overrides():
    d = cr.create_cost_config({})
    assert d.enabled is False
    assert d.commission_bp == 5

    o = cr.create_cost_config({"cost": {"enabled": True,
                                        "commission_bp": 3,
                                        "flat_cost_bps": 1}})
    assert o.enabled is True
    assert o.commission_bp == 3
    assert o.flat_cost_bps == 1


def test_backtest_config_from_yaml():
    bc = cr.create_backtest_config({
        "backtest": {"start_date": "2023-01-01", "end_date": "2023-06-30"},
    })
    assert bc is not None


# ── _is_data_fresh ──────────────────────────────────────────


def test_fresh_false_when_db_missing(tmp_path):
    assert cr._is_data_fresh(tmp_path, "s1", "2024-06-30") is False


def test_fresh_true_within_seven_days(ws):
    n = 30
    idx = pd.bdate_range("2024-06-01", periods=n)
    frame = pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 1.0},
        index=pd.DatetimeIndex(idx, name="trade_date"),
    )
    db.save_ohlcv_to_db(ws, {"A": frame}, "s1")
    # 数据最后一天 2024-07-12 附近；end_date 在 7 天内 → fresh
    last = str(idx[-1].date())
    assert cr._is_data_fresh(ws, "s1", last) is True


def test_fresh_false_when_stale(ws):
    idx = pd.bdate_range("2024-01-01", periods=10)
    frame = pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 1.0},
        index=pd.DatetimeIndex(idx, name="trade_date"),
    )
    db.save_ohlcv_to_db(ws, {"A": frame}, "s1")
    assert cr._is_data_fresh(ws, "s1", "2024-06-30") is False


def test_fresh_false_for_unknown_strategy(ws):
    assert cr._is_data_fresh(ws, "ghost", "2024-06-30") is False


# ── load_data 编排 ──────────────────────────────────────────


def _seed_prices(ws, strategy="s1", codes=("A", "B"), days=250):
    idx = pd.bdate_range("2023-06-01", periods=days)
    for code in codes:
        frame = pd.DataFrame(
            {"open": 10.0, "high": 11.0, "low": 9.0,
             "close": 10.0 + len(code), "volume": 1.0},
            index=pd.DatetimeIndex(idx, name="trade_date"),
        )
        db.save_ohlcv_to_db(ws, {code: frame}, strategy)


CFG_DUCKDB = {
    "strategy": {"name": "s1"},
    "data": {"source": "duckdb", "codes": ["A", "B"],
             "start_date": "2023-06-01", "end_date": "2024-06-30"},
}


def test_load_data_duckdb_source_returns_panel(ws):
    _seed_prices(ws)
    panel = cr.load_data(CFG_DUCKDB, ws)
    assert not panel.empty
    assert set(panel.columns) == {"A", "B"}


def test_load_data_filters_orphan_assets(ws):
    """DuckDB 里有配置外的孤儿资产 → 必须被过滤（data gate 契约）。"""
    _seed_prices(ws, codes=("A", "B", "ORPHAN_X"))
    panel = cr.load_data(CFG_DUCKDB, ws)
    assert set(panel.columns) == {"A", "B"}
    assert "ORPHAN_X" not in panel.columns


def test_load_data_codes_subset_keeps_only_configured(ws):
    _seed_prices(ws)
    cfg = {**CFG_DUCKDB, "data": {**CFG_DUCKDB["data"], "codes": ["A"]}}
    panel = cr.load_data(cfg, ws)
    assert list(panel.columns) == ["A"]


def test_load_data_missing_all_codes_gives_empty_frame(ws):
    cfg = {**CFG_DUCKDB, "data": {**CFG_DUCKDB["data"], "codes": ["Z"]}}
    panel = cr.load_data(cfg, ws)
    assert panel.empty


def test_load_data_auto_source_uses_loader_and_caches(ws, monkeypatch):
    """auto 路径：resolve_loader.fetch → save → 读回，全程不碰真网络。"""
    n = 20
    idx = pd.bdate_range("2024-01-01", periods=n)
    frame = pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 1.0},
        index=pd.DatetimeIndex(idx, name="trade_date"),
    )

    class FakeLoader:
        name = "fake"

        def fetch(self, codes, start, end):
            return {c: frame for c in codes}

    import strategy_research.core.data_source.registry as reg
    monkeypatch.setattr(reg, "resolve_loader", lambda market: FakeLoader())
    monkeypatch.setattr("strategy_research.core.data_source.utils.detect_market",
                        lambda code: "a_share")

    cfg = {**CFG_DUCKDB, "data": {**CFG_DUCKDB["data"],
                                  "source": "auto", "codes": ["A"]}}
    panel = cr.load_data(cfg, ws)
    assert not panel.empty
    assert list(panel.columns) == ["A"]
    # 已落 DuckDB 缓存
    info = db.get_price_data_info(ws, "s1")
    assert info


def test_load_data_auto_failure_returns_empty(ws, monkeypatch):
    """在线拉取失败 → 返回空 DataFrame（不抛，降级路径）。"""

    class BoomLoader:
        name = "boom"

        def fetch(self, codes, start, end):
            raise RuntimeError("network down")

    import strategy_research.core.data_source.registry as reg
    monkeypatch.setattr(reg, "resolve_loader", lambda market: BoomLoader())
    monkeypatch.setattr("strategy_research.core.data_source.utils.detect_market",
                        lambda code: "a_share")

    cfg = {**CFG_DUCKDB, "data": {**CFG_DUCKDB["data"],
                                  "source": "auto", "codes": ["A"]}}
    panel = cr.load_data(cfg, ws)
    assert panel.empty
