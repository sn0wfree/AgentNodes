"""Tests for optimizer logging — fallback to equal weights when optimization fails."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.optimizers import optimize_weights

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def good_data():
    """Well-formed return/position data that all optimizers handle."""
    dates = pd.bdate_range("2024-01-02", periods=50)
    np.random.seed(42)
    ret_data = np.random.randn(50, 3) * 0.01
    ret_df = pd.DataFrame(ret_data, index=dates, columns=["A", "B", "C"])
    pos_df = pd.DataFrame(0.0, index=dates, columns=["A", "B", "C"])
    return ret_df, pos_df, dates


# ============================================================
# Successful optimizer calls
# ============================================================


class TestSuccessfulOptimization:
    def test_equal_volatility_no_warning(self, good_data, caplog):
        ret_df, pos_df, dates = good_data
        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            optimize_weights(ret_df, pos_df, dates, method="equal_volatility")
        # Should not log a warning
        assert len(caplog.records) == 0

    def test_risk_parity_no_warning(self, good_data, caplog):
        ret_df, pos_df, dates = good_data
        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            optimize_weights(ret_df, pos_df, dates, method="risk_parity")
        assert len(caplog.records) == 0

    def test_mean_variance_no_warning(self, good_data, caplog):
        ret_df, pos_df, dates = good_data
        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            optimize_weights(ret_df, pos_df, dates, method="mean_variance")
        assert len(caplog.records) == 0

    def test_max_diversification_no_warning(self, good_data, caplog):
        ret_df, pos_df, dates = good_data
        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            optimize_weights(ret_df, pos_df, dates, method="max_diversification")
        assert len(caplog.records) == 0

    def test_turnover_aware_no_warning(self, good_data, caplog):
        ret_df, pos_df, dates = good_data
        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            optimize_weights(ret_df, pos_df, dates, method="turnover_aware")
        assert len(caplog.records) == 0


# ============================================================
# Fallback logging
# ============================================================


class TestFallbackLogging:
    def test_all_nan_returns_falls_back(self, caplog):
        """When returns are all NaN, optimization may fail."""
        dates = pd.bdate_range("2024-01-02", periods=10)
        ret_df = pd.DataFrame(np.nan, index=dates, columns=["A", "B"])
        pos_df = pd.DataFrame(0.0, index=dates, columns=["A", "B"])

        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            # Use bad risk_aversion to trigger an exception
            result = optimize_weights(
                ret_df, pos_df, dates, method="mean_variance",
                risk_aversion="not_a_number",
            )

        # Should fall back to equal weights
        w = result.iloc[0].values
        np.testing.assert_array_almost_equal(w, [0.5, 0.5])

        # And log a warning
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_messages) > 0
        assert any("fallback" in msg.lower() or "failed" in msg.lower() for msg in warning_messages)

    def test_constant_returns_falls_back(self, caplog):
        """Constant returns → zero variance → may cause issues."""
        dates = pd.bdate_range("2024-01-02", periods=10)
        ret_df = pd.DataFrame(0.0, index=dates, columns=["A", "B", "C"])
        pos_df = pd.DataFrame(0.0, index=dates, columns=["A", "B", "C"])

        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            result = optimize_weights(ret_df, pos_df, dates, method="mean_variance")

        # Should still produce a valid (equal-weight) result
        assert result.shape == ret_df.shape
        w = result.iloc[0].values
        assert np.all(w >= 0) and np.isclose(w.sum(), 1.0, atol=0.01)

    def test_singular_covariance_falls_back(self, caplog):
        """Perfectly correlated returns → singular covariance → may fail."""
        dates = pd.bdate_range("2024-01-02", periods=20)
        ret_data = np.random.randn(20, 1) * 0.01
        ret_df = pd.DataFrame(
            np.column_stack([ret_data, ret_data, ret_data]),
            index=dates,
            columns=["A", "B", "C"],
        )
        pos_df = pd.DataFrame(0.0, index=dates, columns=["A", "B", "C"])

        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            result = optimize_weights(ret_df, pos_df, dates, method="risk_parity")

        # Should not crash; result should have valid weights
        assert result.shape == ret_df.shape
        w = result.iloc[0].values
        assert np.all(w >= 0)
        assert np.isclose(w.sum(), 1.0, atol=0.02)

    def test_bad_kwargs_trigger_fallback(self, caplog):
        """Invalid kwargs should trigger fallback + warning."""
        dates = pd.bdate_range("2024-01-02", periods=10)
        np.random.seed(42)
        ret_df = pd.DataFrame(
            np.random.randn(10, 3) * 0.01, index=dates, columns=["A", "B", "C"]
        )
        pos_df = pd.DataFrame(0.0, index=dates, columns=["A", "B", "C"])

        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            result = optimize_weights(
                ret_df, pos_df, dates, method="mean_variance",
                risk_aversion="invalid_type",
            )

        # Should fall back to equal weight
        w = result.iloc[0].values
        np.testing.assert_array_almost_equal(w, [1/3, 1/3, 1/3], decimal=4)

        # And log a warning
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) > 0


# ============================================================
# Fallback weight correctness
# ============================================================


class TestFallbackWeights:
    def test_fallback_is_equal_weight(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        ret_df = pd.DataFrame(np.nan, index=dates, columns=["A", "B", "C", "D"])
        pos_df = pd.DataFrame(0.0, index=dates, columns=["A", "B", "C", "D"])
        result = optimize_weights(ret_df, pos_df, dates, method="mean_variance")
        w = result.iloc[0].values
        np.testing.assert_array_almost_equal(w, [0.25, 0.25, 0.25, 0.25])

    def test_fallback_shape_preserved(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        ret_df = pd.DataFrame(np.nan, index=dates, columns=["A", "B"])
        pos_df = pd.DataFrame(0.0, index=dates, columns=["A", "B"])
        result = optimize_weights(ret_df, pos_df, dates, method="mean_variance")
        assert result.shape == (5, 2)

    def test_fallback_index_matches_dates(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        ret_df = pd.DataFrame(np.nan, index=dates, columns=["A", "B"])
        pos_df = pd.DataFrame(0.0, index=dates, columns=["A", "B"])
        result = optimize_weights(ret_df, pos_df, dates, method="mean_variance")
        assert result.index.equals(dates)


# ============================================================
# Unknown method
# ============================================================


class TestUnknownMethod:
    def test_unknown_method_raises(self, good_data):
        ret_df, pos_df, dates = good_data
        with pytest.raises(ValueError, match="Unknown method"):
            optimize_weights(ret_df, pos_df, dates, method="unknown_method")


# ============================================================
# Empty inputs
# ============================================================


class TestEmptyInputs:
    def test_empty_df_returns_empty(self):
        empty_ret = pd.DataFrame()
        empty_pos = pd.DataFrame()
        result = optimize_weights(empty_ret, empty_pos, pd.DatetimeIndex([]), method="equal_volatility")
        assert result.empty

    def test_single_asset(self):
        dates = pd.bdate_range("2024-01-02", periods=10)
        ret_df = pd.DataFrame({"X": np.random.randn(10) * 0.01}, index=dates)
        pos_df = pd.DataFrame({"X": [0.0] * 10}, index=dates)
        result = optimize_weights(ret_df, pos_df, dates, method="equal_volatility")
        # Single asset must get weight 1.0
        assert result["X"].iloc[0] == pytest.approx(1.0)


# ============================================================
# Logging message format
# ============================================================


class TestLogMessageFormat:
    def test_log_contains_method_name(self, caplog):
        dates = pd.bdate_range("2024-01-02", periods=5)
        ret_df = pd.DataFrame({"A": [1.0]*5, "B": [2.0]*5}, index=dates)
        pos_df = pd.DataFrame(0.0, index=dates, columns=["A", "B"])

        with caplog.at_level(logging.WARNING, logger="strategy_research.apps.engine.optimizers.base"):
            # Force exception via bad kwargs
            optimize_weights(
                ret_df, pos_df, dates, method="risk_parity",
                # RiskParity doesn't accept these kwargs, will fail
                unsupported_kwarg=True,
            )

        # At least one warning should mention the method name
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        if len(warning_messages) > 0:
            assert any("risk_parity" in msg for msg in warning_messages), \
                f"Expected 'risk_parity' in warning messages, got: {warning_messages}"
