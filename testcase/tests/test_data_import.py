"""Tests for data_import module."""

from __future__ import annotations

import pandas as pd

from strategy_research.apps.data_import import (
    generate_sample_data,
    generate_sample_ohlcv_data,
)


class TestGenerateSampleData:
    def test_default_shape(self):
        df = generate_sample_data()
        assert df.shape == (504, 10)

    def test_custom_shape(self):
        df = generate_sample_data(n_assets=5, n_days=100)
        assert df.shape == (100, 5)

    def test_column_names(self):
        df = generate_sample_data(n_assets=3)
        assert list(df.columns) == ["asset_000", "asset_001", "asset_002"]

    def test_date_index(self):
        df = generate_sample_data(n_days=10, start_date="2023-01-01")
        assert df.index[0] == pd.Timestamp("2023-01-01")
        assert len(df) == 10

    def test_positive_prices(self):
        df = generate_sample_data()
        assert (df > 0).all().all()

    def test_deterministic(self):
        df1 = generate_sample_data(n_assets=3, n_days=10)
        df2 = generate_sample_data(n_assets=3, n_days=10)
        pd.testing.assert_frame_equal(df1, df2)


class TestGenerateSampleOHLCVData:
    def test_returns_dict(self):
        result = generate_sample_ohlcv_data()
        assert isinstance(result, dict)
        assert len(result) == 10

    def test_each_asset_has_ohlcv(self):
        result = generate_sample_ohlcv_data(n_assets=2)
        for asset, df in result.items():
            assert "open" in df.columns
            assert "high" in df.columns
            assert "low" in df.columns
            assert "close" in df.columns
            assert "volume" in df.columns

    def test_ohlcv_invariants(self):
        result = generate_sample_ohlcv_data(n_assets=3, n_days=50)
        for asset, df in result.items():
            # high >= max(open, close)
            assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
            # low <= min(open, close)
            assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()
            # All positive
            assert (df[["open", "high", "low", "close"]] > 0).all().all()
            assert (df["volume"] > 0).all()

    def test_custom_params(self):
        result = generate_sample_ohlcv_data(n_assets=2, n_days=30, start_date="2023-06-01")
        assert len(result) == 2
        for df in result.values():
            assert len(df) == 30
            assert df.index[0] == pd.Timestamp("2023-06-01")

    def test_deterministic(self):
        r1 = generate_sample_ohlcv_data(n_assets=2, n_days=10)
        r2 = generate_sample_ohlcv_data(n_assets=2, n_days=10)
        for asset in r1:
            pd.testing.assert_frame_equal(r1[asset], r2[asset])

    def test_date_index_name(self):
        result = generate_sample_ohlcv_data(n_assets=1)
        for df in result.values():
            assert df.index.name == "date"
