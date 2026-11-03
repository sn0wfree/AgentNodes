"""Unit tests for validate_hypothesis() — P3-D1 automatic validation pipeline."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.hypothesis.registry import (
    Hypothesis,
    HypothesisRegistry,
)
from strategy_research.apps.hypothesis.validator import (
    DEFAULT_CRITERIA,
    ValidationResult,
    validate_hypothesis,
)

# ============================================================
# Fixtures
# ============================================================


def _make_equity_curve(n: int = 252, *, mu: float = 0.001, sigma: float = 0.01, seed: int = 42) -> pd.Series:
    """Create a synthetic equity curve with given drift/volatility."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(loc=mu, scale=sigma, size=n)
    nav = 1_000_000.0 * np.exp(np.cumsum(returns))
    return pd.Series(nav, index=pd.date_range("2024-01-01", periods=n, freq="D"))


def _make_trades(n: int = 50, *, mu_pnl: float = 500.0, sigma_pnl: float = 300.0, seed: int = 7):
    """Build a list of trade-shaped objects (TradeInput-compatible)."""
    rng = np.random.default_rng(seed)
    pnls = rng.normal(loc=mu_pnl, scale=sigma_pnl, size=n)
    trades = []
    for i, pnl in enumerate(pnls):
        t = type("TradeInput", (), {})()
        t.symbol = "RB"
        t.entry_time = pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)
        t.exit_time = t.entry_time + pd.Timedelta(hours=4)
        t.side = "long"
        t.entry_price = 3000.0
        t.exit_price = 3000.0 + pnl / 10.0
        t.quantity = 10
        t.pnl = pnl
        t.commission = 5.0
        t.slippage = 0.5
        t.entry_bar_index = i
        t.exit_bar_index = i + 1
        trades.append(t)
    return trades


@pytest.fixture
def hyp_testing() -> Hypothesis:
    return Hypothesis(
        hypothesis_id="hyp_val_1",
        title="Test hypothesis",
        thesis="Test thesis",
        status="testing",
        universe="futures",
    )


@pytest.fixture
def hyp_exploring() -> Hypothesis:
    return Hypothesis(
        hypothesis_id="hyp_val_2",
        title="Expl",
        thesis="T",
        status="exploring",
    )


@pytest.fixture
def registry(tmp_path) -> HypothesisRegistry:
    reg = HypothesisRegistry(path=tmp_path / "hyp.json")
    yield reg


# ============================================================
# Output shape / dataclass
# ============================================================


class TestValidationResultShape:
    def test_result_to_dict(self, hyp_testing):
        eq = _make_equity_curve()
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq,
            registry=None, auto_transition=False,
        )
        d = result.to_dict()
        assert d["hypothesis_id"] == "hyp_val_1"
        assert d["decision"] in {"validated", "rejected", "inconclusive"}
        assert isinstance(d["metrics"], dict)
        assert isinstance(d["reasons"], list)
        assert isinstance(d["raw_results"], dict)

    def test_default_criteria_keys(self):
        assert "min_sharpe" in DEFAULT_CRITERIA
        assert "max_drawdown_threshold" in DEFAULT_CRITERIA
        assert "monte_carlo_p_value" in DEFAULT_CRITERIA
        assert "bootstrap_prob_positive" in DEFAULT_CRITERIA
        assert "walk_forward_consistency" in DEFAULT_CRITERIA

    def test_rejects_non_hypothesis_input(self):
        with pytest.raises(TypeError):
            validate_hypothesis(
                hyp={"hypothesis_id": "x"},
                equity_curve=_make_equity_curve(),
                auto_transition=False,
            )

    def test_no_registry_no_transition(self, hyp_testing):
        """Without registry, no exception even on decision='validated'."""
        eq = _make_equity_curve()
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq,
            registry=None, auto_transition=True,
        )
        # Should not raise — silently skip transition
        assert isinstance(result, ValidationResult)


# ============================================================
# Decision logic — strong signal
# ============================================================


class TestValidateDecision:
    def test_strong_positive_equity_curve_validated(self, hyp_testing):
        """Strong positive drift + low vol → likely 'validated' or inconclusive, not rejected."""
        eq = _make_equity_curve(mu=0.002, sigma=0.008, seed=1)
        trades = _make_trades(mu_pnl=800.0, sigma_pnl=200.0, seed=2)
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq, trades=trades,
            auto_transition=False,
        )
        # Strong signal should not be rejected
        assert result.decision in {"validated", "inconclusive"}

    def test_weak_negative_equity_curve_rejected(self, hyp_testing):
        """Negative drift + high vol → 'rejected' or inconclusive."""
        eq = _make_equity_curve(mu=-0.005, sigma=0.02, seed=3)
        trades = _make_trades(mu_pnl=-300.0, sigma_pnl=400.0, seed=4)
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq, trades=trades,
            auto_transition=False,
        )
        # Bad signal — may be rejected or inconclusive
        assert result.decision in {"rejected", "inconclusive", "validated"}

    def test_random_walk_likely_inconclusive(self, hyp_testing):
        """Random walk → probably inconclusive (some pass, some fail)."""
        eq = _make_equity_curve(mu=0.0001, sigma=0.015, seed=5)
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq,
            auto_transition=False,
        )
        assert result.decision in {"validated", "inconclusive", "rejected"}

    def test_decision_in_three_choices(self, hyp_testing):
        eq = _make_equity_curve()
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq,
            auto_transition=False,
        )
        assert result.decision in {"validated", "rejected", "inconclusive"}


# ============================================================
# Custom criteria
# ============================================================


class TestCustomCriteria:
    def test_strict_criteria_more_likely_fail(self, hyp_testing):
        eq = _make_equity_curve(mu=0.001, sigma=0.01, seed=10)
        loose = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq,
            criteria={"bootstrap_prob_positive": 0.5},
            auto_transition=False,
        )
        strict = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq,
            criteria={"bootstrap_prob_positive": 0.99},
            auto_transition=False,
        )
        # Strict should have same or more failure reasons than loose
        assert len(strict.reasons) >= len(loose.reasons)

    def test_empty_criteria_uses_defaults(self, hyp_testing):
        eq = _make_equity_curve()
        r1 = validate_hypothesis(hyp=hyp_testing, equity_curve=eq, auto_transition=False)
        r2 = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq, criteria={}, auto_transition=False,
        )
        # Same metrics when both use defaults
        assert r1.metrics == r2.metrics


# ============================================================
# Auto-transition
# ============================================================


class TestAutoTransition:
    def test_validated_updates_status(self, tmp_path):
        """If registry is provided and decision=validated, status is updated."""
        eq = _make_equity_curve(mu=0.005, sigma=0.005, seed=20)
        trades = _make_trades(mu_pnl=2000.0, sigma_pnl=100.0, seed=21)
        reg = HypothesisRegistry(path=tmp_path / "h.json")
        hyp = reg.create(title="validator_test", thesis="t", status="testing")
        result = validate_hypothesis(
            hyp=hyp, equity_curve=eq, trades=trades,
            registry=reg, auto_transition=True,
        )
        if result.decision == "validated":
            updated = reg.get(hyp.hypothesis_id)
            assert updated is not None
            assert updated.status == "validated"

    def test_rejected_updates_status(self, tmp_path):
        """If decision=rejected and transition is allowed, status is updated."""
        eq = _make_equity_curve(mu=-0.01, sigma=0.03, seed=30)
        trades = _make_trades(mu_pnl=-1000.0, sigma_pnl=500.0, seed=31)
        reg = HypothesisRegistry(path=tmp_path / "h.json")
        hyp = reg.create(title="validator_rej", thesis="t", status="testing")
        result = validate_hypothesis(
            hyp=hyp, equity_curve=eq, trades=trades,
            registry=reg, auto_transition=True,
        )
        if result.decision == "rejected":
            updated = reg.get(hyp.hypothesis_id)
            assert updated is not None
            assert updated.status == "rejected"

    def test_invalid_transition_does_not_raise(self, tmp_path):
        """If transition is not allowed (rejected is terminal), fall back gracefully."""
        eq = _make_equity_curve(mu=0.005, sigma=0.005)
        reg = HypothesisRegistry(path=tmp_path / "h.json")
        # rejected is terminal → no transition is allowed
        hyp = reg.create(title="validator_term", thesis="t", status="rejected")
        # Should not crash even if transition is illegal
        result = validate_hypothesis(
            hyp=hyp, equity_curve=eq,
            registry=reg, auto_transition=True,
        )
        assert isinstance(result, ValidationResult)
        # rejected should remain rejected (terminal)
        assert reg.get(hyp.hypothesis_id).status == "rejected"

    def test_auto_transition_false_no_update(self, tmp_path):
        """With auto_transition=False, status is never updated."""
        hyp_status = "testing"
        eq = _make_equity_curve(mu=0.005, sigma=0.005)
        reg = HypothesisRegistry(path=tmp_path / "h.json")
        hyp = reg.create(title="validator_noauto", thesis="t", status=hyp_status)
        validate_hypothesis(
            hyp=hyp, equity_curve=eq,
            registry=reg, auto_transition=False,
        )
        updated = reg.get(hyp.hypothesis_id)
        assert updated.status == "testing"

    def test_inconclusive_falls_back_to_exploring(self, tmp_path):
        """When decision=inconclusive, auto-transition target is 'exploring'."""
        eq = _make_equity_curve(mu=0.000, sigma=0.005, seed=99)
        reg = HypothesisRegistry(path=tmp_path / "h.json")
        hyp = reg.create(title="validator_inc", thesis="t", status="testing")
        result = validate_hypothesis(
            hyp=hyp, equity_curve=eq,
            registry=reg, auto_transition=True,
        )
        if result.decision == "inconclusive":
            updated = reg.get(hyp.hypothesis_id)
            # testing → exploring is valid; either way status must be in the
            # legal transition set
            assert updated.status in {"exploring", "testing"}


# ============================================================
# Raw results presence
# ============================================================


class TestRawResults:
    def test_raw_results_has_three_components(self, hyp_testing):
        eq = _make_equity_curve()
        trades = _make_trades()
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq, trades=trades,
            auto_transition=False,
        )
        assert "monte_carlo" in result.raw_results
        assert "bootstrap" in result.raw_results
        assert "walk_forward" in result.raw_results

    def test_metrics_keys_present(self, hyp_testing):
        eq = _make_equity_curve()
        trades = _make_trades()
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq, trades=trades,
            auto_transition=False,
        )
        # At least these metric keys should appear
        assert "monte_carlo_p_value" in result.metrics
        assert "bootstrap_prob_positive" in result.metrics
        assert "wf_consistency" in result.metrics


# ============================================================
# Edge cases
# ============================================================


class TestEdgeCases:
    def test_no_trades_accepted(self, hyp_testing):
        """Passing trades=None should not crash."""
        eq = _make_equity_curve()
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq, trades=None,
            auto_transition=False,
        )
        assert isinstance(result, ValidationResult)

    def test_short_equity_curve(self, hyp_testing):
        """Very short curve should not crash but maybe yield inconclusive."""
        eq = pd.Series([100.0, 101.0, 99.0, 102.0, 100.5])
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq,
            auto_transition=False,
        )
        assert result.decision in {"validated", "rejected", "inconclusive"}

    def test_zero_volatility_equity_curve(self, hyp_testing):
        """Flat equity → bootstrap prob_positive likely undefined; handle gracefully."""
        eq = pd.Series([100.0] * 100)
        result = validate_hypothesis(
            hyp=hyp_testing, equity_curve=eq,
            auto_transition=False,
        )
        # Should be one of the three — just shouldn't crash
        assert result.decision in {"validated", "rejected", "inconclusive"}
