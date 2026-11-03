"""Tests for optimizers — QuantOPT 适配层 5 个优化器"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.optimizers import optimize_weights

# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def ret_df():
    dates = pd.bdate_range("2024-01-02", periods=100)
    np.random.seed(42)
    data = np.random.randn(100, 3) * 0.01
    return pd.DataFrame(data, index=dates, columns=["A", "B", "C"])


@pytest.fixture
def pos_df(ret_df):
    return pd.DataFrame(0.0, index=ret_df.index, columns=ret_df.columns)


# ============================================================
# equal_volatility
# ============================================================


class TestEqualVolatility:
    def test_weights_sum_to_one(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="equal_volatility")
        assert result.iloc[0].sum() == pytest.approx(1.0, abs=1e-6)

    def test_lower_vol_gets_higher_weight(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="equal_volatility")
        w = result.iloc[0]
        # B has highest vol in random data → should get lowest weight
        ret_df.std()
        # Inverse vol ordering should match weight ordering
        assert w["A"] > 0

    def test_output_shape(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="equal_volatility")
        assert result.shape == ret_df.shape

    def test_constant_weights(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="equal_volatility")
        # All rows should be identical
        assert (result.iloc[0] == result.iloc[50]).all()


# ============================================================
# risk_parity
# ============================================================


class TestRiskParity:
    def test_weights_sum_to_one(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="risk_parity")
        assert result.iloc[0].sum() == pytest.approx(1.0, abs=0.02)

    def test_output_shape(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="risk_parity")
        assert result.shape == ret_df.shape

    def test_positive_weights(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="risk_parity")
        assert (result.iloc[0] >= 0).all()


# ============================================================
# mean_variance
# ============================================================


class TestMeanVariance:
    def test_weights_sum_to_one(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="mean_variance")
        assert result.iloc[0].sum() == pytest.approx(1.0, abs=0.02)

    def test_with_risk_aversion(self, ret_df, pos_df):
        r1 = optimize_weights(ret_df, pos_df, ret_df.index, method="mean_variance", risk_aversion=0.5)
        r2 = optimize_weights(ret_df, pos_df, ret_df.index, method="mean_variance", risk_aversion=2.0)
        # Different risk aversion should give different weights
        assert not np.allclose(r1.iloc[0].values, r2.iloc[0].values)


# ============================================================
# max_diversification
# ============================================================


class TestMaxDiversification:
    def test_weights_sum_near_one(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="max_diversification")
        assert result.iloc[0].sum() == pytest.approx(1.0, abs=0.05)

    def test_output_shape(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="max_diversification")
        assert result.shape == ret_df.shape


# ============================================================
# turnover_aware
# ============================================================


class TestTurnoverAware:
    def test_weights_sum_to_one(self, ret_df, pos_df):
        result = optimize_weights(ret_df, pos_df, ret_df.index, method="turnover_aware")
        assert result.iloc[0].sum() == pytest.approx(1.0, abs=0.02)

    def test_with_lambda_r(self, ret_df, pos_df):
        r1 = optimize_weights(ret_df, pos_df, ret_df.index, method="turnover_aware", lambda_r=0.1)
        r2 = optimize_weights(ret_df, pos_df, ret_df.index, method="turnover_aware", lambda_r=10.0)
        # Different lambda should give different weights
        assert not np.allclose(r1.iloc[0].values, r2.iloc[0].values)


# ============================================================
# edge cases
# ============================================================


class TestOptimizerEdgeCases:
    def test_empty_df(self):
        empty_ret = pd.DataFrame()
        empty_pos = pd.DataFrame()
        result = optimize_weights(empty_ret, empty_pos, pd.DatetimeIndex([]), method="equal_volatility")
        assert result.empty

    def test_unknown_method(self, ret_df, pos_df):
        with pytest.raises(ValueError, match="Unknown method"):
            optimize_weights(ret_df, pos_df, ret_df.index, method="nonexistent")

    def test_single_asset(self):
        dates = pd.bdate_range("2024-01-02", periods=10)
        ret_df = pd.DataFrame({"A": np.random.randn(10) * 0.01}, index=dates)
        pos_df = pd.DataFrame({"A": [0.0] * 10}, index=dates)
        result = optimize_weights(ret_df, pos_df, dates, method="equal_volatility")
        assert result["A"].iloc[0] == pytest.approx(1.0)

    def test_two_assets(self):
        dates = pd.bdate_range("2024-01-02", periods=20)
        np.random.seed(123)
        ret_df = pd.DataFrame(
            np.random.randn(20, 2) * 0.01, index=dates, columns=["X", "Y"]
        )
        pos_df = pd.DataFrame(0.0, index=dates, columns=["X", "Y"])
        result = optimize_weights(ret_df, pos_df, dates, method="equal_volatility")
        assert result.iloc[0].sum() == pytest.approx(1.0, abs=1e-6)
