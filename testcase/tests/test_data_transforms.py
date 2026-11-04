"""Tests for core.tools.data_transforms (tool set #2).

Covers:
- Each of the 7 helpers: happy path + edge case
- fail-fast: wrong input → ValueError
- round-trip: wide ↔ long preserves data
- is_wide_close_format detection accuracy
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.tools.data_transforms import (
    is_wide_close_format,
    long_to_single_asset_wide,
    long_to_wide_close,
    long_to_wide_ohlcv_per_asset,
    wide_close_to_long,
    wide_factor_to_long,
    wide_to_long_ohlcv,
)

# ============================================================
# Fixtures
# ============================================================


def _make_long_ohlcv(n_dates: int = 5, n_assets: int = 3) -> pd.DataFrame:
    """Build a small long OHLCV frame with 5 dates × 3 assets."""
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    assets = [f"{600000 + i:06d}.SH" for i in range(n_assets)]
    rows = []
    for d in dates:
        for i, a in enumerate(assets):
            base = 10 + i
            rows.append(
                {
                    "date": d,
                    "asset": a,
                    "open": base + 0.1,
                    "high": base + 0.5,
                    "low": base - 0.2,
                    "close": base + 0.2,
                    "volume": 1_000_000 + i * 1000,
                }
            )
    return pd.DataFrame(rows)


def _make_wide_close(n_dates: int = 5, n_assets: int = 3) -> pd.DataFrame:
    """Build a wide close-only panel (date index, asset columns)."""
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    assets = [f"{600000 + i:06d}.SH" for i in range(n_assets)]
    data = np.arange(n_dates * n_assets, dtype=float).reshape(n_dates, n_assets) + 10
    return pd.DataFrame(data, index=dates, columns=assets)


# ============================================================
# long_to_single_asset_wide
# ============================================================


class TestLongToSingleAssetWide:
    def test_ohlcv_default(self):
        long = _make_long_ohlcv(n_dates=5, n_assets=3)
        out = long_to_single_asset_wide(long, asset="600000.SH")
        assert list(out.columns) == ["open", "high", "low", "close", "volume"]
        assert len(out) == 5
        assert isinstance(out.index, pd.DatetimeIndex)

    def test_close_only(self):
        long = _make_long_ohlcv()
        out = long_to_single_asset_wide(long, asset="600000.SH", value_cols="close")
        assert list(out.columns) == ["close"]

    def test_explicit_cols(self):
        long = _make_long_ohlcv()
        out = long_to_single_asset_wide(
            long, asset="600000.SH", value_cols=["close", "volume"]
        )
        assert list(out.columns) == ["close", "volume"]

    def test_dedup_keeps_last(self):
        long = _make_long_ohlcv(n_dates=3, n_assets=2)
        # Duplicate first row
        dup = pd.concat([long.iloc[:1], long], ignore_index=True)
        out = long_to_single_asset_wide(dup, asset="600000.SH")
        assert len(out) == 3  # duplicate dropped

    def test_missing_asset_raises(self):
        long = _make_long_ohlcv()
        with pytest.raises(ValueError, match="not found"):
            long_to_single_asset_wide(long, asset="999999.SH")

    def test_not_long_format_raises(self):
        wide = _make_wide_close()
        with pytest.raises(ValueError, match="long format requires"):
            long_to_single_asset_wide(wide, asset="600000.SH")

    def test_missing_value_cols_raises(self):
        long = _make_long_ohlcv().drop(columns=["open", "high", "low", "close", "volume"])
        with pytest.raises(ValueError, match="none of requested"):
            long_to_single_asset_wide(long, asset="600000.SH")


# ============================================================
# long_to_wide_close
# ============================================================


class TestLongToWideClose:
    def test_basic_pivot(self):
        long = _make_long_ohlcv(n_dates=5, n_assets=3)
        panel = long_to_wide_close(long)
        assert panel.shape == (5, 3)
        assert list(panel.columns) == [f"{600000 + i:06d}.SH" for i in range(3)]
        assert isinstance(panel.index, pd.DatetimeIndex)

    def test_custom_column_names(self):
        long = _make_long_ohlcv().rename(
            columns={"date": "trade_date", "asset": "ticker"}
        )
        panel = long_to_wide_close(long, date_col="trade_date", asset_col="ticker")
        assert panel.shape == (5, 3)

    def test_not_long_raises(self):
        wide = _make_wide_close()
        with pytest.raises(ValueError, match="long format requires"):
            long_to_wide_close(wide)

    def test_missing_value_col_raises(self):
        long = _make_long_ohlcv().drop(columns=["close"])
        with pytest.raises(ValueError, match="not in df columns"):
            long_to_wide_close(long, value_col="close")


# ============================================================
# long_to_wide_ohlcv_per_asset
# ============================================================


class TestLongToWideOhlcvPerAsset:
    def test_returns_dict(self):
        long = _make_long_ohlcv(n_dates=5, n_assets=3)
        panels = long_to_wide_ohlcv_per_asset(long)
        assert isinstance(panels, dict)
        assert set(panels.keys()) == {f"{600000 + i:06d}.SH" for i in range(3)}
        for asset, df in panels.items():
            assert list(df.columns) == ["open", "high", "low", "close", "volume"]
            assert len(df) == 5

    def test_subset_ohlcv_columns(self):
        long = _make_long_ohlcv().drop(columns=["volume"])
        panels = long_to_wide_ohlcv_per_asset(long)
        for df in panels.values():
            assert "volume" not in df.columns
            assert "close" in df.columns

    def test_not_long_raises(self):
        wide = _make_wide_close()
        with pytest.raises(ValueError, match="long format requires"):
            long_to_wide_ohlcv_per_asset(wide)

    def test_no_ohlcv_columns_raises(self):
        long = pd.DataFrame(
            {"date": pd.date_range("2024-01-01", periods=3), "asset": ["X", "Y", "X"]}
        )
        with pytest.raises(ValueError, match="no ohlcv columns"):
            long_to_wide_ohlcv_per_asset(long)


# ============================================================
# wide_close_to_long
# ============================================================


class TestWideCloseToLong:
    def test_basic_melt(self):
        panel = _make_wide_close(n_dates=5, n_assets=3)
        long = wide_close_to_long(panel)
        assert list(long.columns) == ["date", "asset", "close"]
        assert len(long) == 5 * 3
        assert isinstance(long["date"].iloc[0], pd.Timestamp)

    def test_already_long_raises(self):
        long = _make_long_ohlcv()[["date", "asset", "close"]]
        with pytest.raises(ValueError, match="already looks like long"):
            wide_close_to_long(long)

    def test_non_dataframe_raises(self):
        with pytest.raises(ValueError, match="expected DataFrame"):
            wide_close_to_long([1, 2, 3])  # type: ignore[arg-type]


# ============================================================
# wide_factor_to_long
# ============================================================


class TestWideFactorToLong:
    def test_basic(self):
        panel = _make_wide_close()
        long = wide_factor_to_long(panel, factor_name="momentum")
        assert list(long.columns) == ["date", "asset", "momentum"]
        assert len(long) == 15

    def test_empty_factor_name_raises(self):
        panel = _make_wide_close()
        with pytest.raises(ValueError, match="non-empty"):
            wide_factor_to_long(panel, factor_name="")

    def test_already_long_raises(self):
        long = _make_long_ohlcv()[["date", "asset"]].copy()
        long["momentum"] = 0.0
        with pytest.raises(ValueError, match="already looks like long"):
            wide_factor_to_long(long, factor_name="momentum")


# ============================================================
# wide_to_long_ohlcv
# ============================================================


class TestWideToLongOhlcv:
    def test_concat(self):
        long = _make_long_ohlcv(n_dates=4, n_assets=2)
        panels = long_to_wide_ohlcv_per_asset(long)
        out = wide_to_long_ohlcv(panels)
        assert {"date", "asset", "open", "high", "low", "close", "volume"}.issubset(
            out.columns
        )
        assert len(out) == 4 * 2

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError, match="empty"):
            wide_to_long_ohlcv({})

    def test_all_empty_panels_raises(self):
        with pytest.raises(ValueError, match="all panels are empty"):
            wide_to_long_ohlcv({"X": pd.DataFrame()})

    def test_non_dataframe_value_raises(self):
        with pytest.raises(ValueError, match="not a DataFrame"):
            wide_to_long_ohlcv({"X": [1, 2, 3]})  # type: ignore[arg-type]


# ============================================================
# is_wide_close_format
# ============================================================


class TestIsWideCloseFormat:
    def test_wide_close_panel(self):
        panel = _make_wide_close()
        assert is_wide_close_format(panel) is True

    def test_wide_ohlcv_panel(self):
        dates = pd.date_range("2024-01-01", periods=3)
        df = pd.DataFrame(
            {
                "open": [1.0, 2.0, 3.0],
                "high": [1.1, 2.1, 3.1],
                "low": [0.9, 1.9, 2.9],
                "close": [1.0, 2.0, 3.0],
            },
            index=dates,
        )
        assert is_wide_close_format(df) is False  # has ohlcv cols

    def test_single_asset_close(self):
        dates = pd.date_range("2024-01-01", periods=3)
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=dates)
        assert is_wide_close_format(df) is False  # has 'close' col

    def test_long_format_dataframe(self):
        long = _make_long_ohlcv()
        assert is_wide_close_format(long) is False  # has 'date' col

    def test_empty_dataframe(self):
        assert is_wide_close_format(pd.DataFrame()) is False

    def test_non_dataframe(self):
        assert is_wide_close_format([1, 2, 3]) is False  # type: ignore[arg-type]

    def test_mixed_columns_partial(self):
        dates = pd.date_range("2024-01-01", periods=3)
        df = pd.DataFrame(
            [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0], [9.0, 10.0, 11.0, 12.0]],
            index=dates,
            columns=["600000.SH", "000001.SZ", "300750.SZ", "other"],
        )
        # 3/4 = 0.75 asset-code-shaped, above 1/2 threshold → wide close
        assert is_wide_close_format(df) is True


# ============================================================
# Round-trip
# ============================================================


class TestRoundTrips:
    def test_long_to_wide_close_and_back(self):
        long = _make_long_ohlcv(n_dates=5, n_assets=3)
        panel = long_to_wide_close(long)
        back = wide_close_to_long(panel)
        # Each (date, asset) cell should round-trip
        merged = long.merge(back, on=["date", "asset"], how="outer")
        np.testing.assert_allclose(
            merged["close_x"].fillna(0), merged["close_y"].fillna(0)
        )

    def test_long_to_wide_ohlcv_per_asset_and_back(self):
        long = _make_long_ohlcv(n_dates=4, n_assets=2)
        panels = long_to_wide_ohlcv_per_asset(long)
        out = wide_to_long_ohlcv(panels)
        # Same number of rows
        assert len(out) == len(long)
        # Per-asset close values should match
        for asset in long["asset"].unique():
            src = long[long["asset"] == asset].set_index("date")["close"].sort_index()
            back = out[out["asset"] == asset].set_index("date")["close"].sort_index()
            pd.testing.assert_series_equal(src, back, check_names=False)
