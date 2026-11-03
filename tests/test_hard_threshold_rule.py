"""Unit tests for strategy_acceptance.rules.HardThresholdRule boundaries."""
from __future__ import annotations

from types import SimpleNamespace

from strategy_research.apps.strategy_acceptance.rules import HardThresholdRule, RuleResult


def _cfg(**overrides):
    base = dict(
        hard_calmar_min=0.5,
        hard_sharpe_min=0.3,
        hard_max_dd_min=-0.15,
        hard_ann_return_min=0.0,
        hard_trades_min=30,
        require_all_hard=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _metrics(**overrides):
    base = dict(
        calmar=1.0,
        sharpe=1.0,
        max_dd=-0.10,
        ann_return=0.20,
        trades=50,
    )
    base.update(overrides)
    return base


class TestRuleResultContract:
    def test_defaults(self):
        r = RuleResult(passed=True)
        assert r.passed is True
        assert r.detail == {}
        assert r.notes == ""

    def test_with_detail(self):
        r = RuleResult(passed=False, detail={"sharpe": False}, notes="failed")
        assert r.passed is False
        assert r.detail == {"sharpe": False}
        assert r.notes == "failed"

    def test_rule_name(self):
        assert HardThresholdRule.name == "hard_threshold"

    def test_returns_rule_result(self):
        res = HardThresholdRule().check(_metrics(), _cfg())
        assert isinstance(res, RuleResult)
        assert res.passed is True
        assert set(res.detail) == {"calmar", "sharpe", "max_dd", "ann_return", "trades"}


class TestBoundaryEquality:
    def test_calmar_exact_threshold_passes(self):
        res = HardThresholdRule().check(_metrics(calmar=0.5), _cfg())
        assert res.detail["calmar"] is True

    def test_sharpe_exact_threshold_passes(self):
        res = HardThresholdRule().check(_metrics(sharpe=0.3), _cfg())
        assert res.detail["sharpe"] is True

    def test_max_dd_exact_threshold_passes(self):
        res = HardThresholdRule().check(_metrics(max_dd=-0.15), _cfg())
        assert res.detail["max_dd"] is True

    def test_trades_exact_threshold_passes(self):
        res = HardThresholdRule().check(_metrics(trades=30), _cfg())
        assert res.detail["trades"] is True

    def test_just_below_threshold_fails(self):
        res = HardThresholdRule().check(_metrics(sharpe=0.2999), _cfg())
        assert res.detail["sharpe"] is False


class TestMaxDrawdownSign:
    def test_less_negative_passes(self):
        # -0.05 is better than -0.15
        res = HardThresholdRule().check(_metrics(max_dd=-0.05), _cfg())
        assert res.detail["max_dd"] is True

    def test_more_negative_fails(self):
        res = HardThresholdRule().check(_metrics(max_dd=-0.20), _cfg())
        assert res.detail["max_dd"] is False

    def test_positive_dd_passes(self):
        res = HardThresholdRule().check(_metrics(max_dd=0.0), _cfg())
        assert res.detail["max_dd"] is True


class TestAnnReturnDisabled:
    def test_zero_threshold_disables_check(self):
        res = HardThresholdRule().check(
            _metrics(ann_return=-99.0), _cfg(hard_ann_return_min=0.0)
        )
        assert res.detail["ann_return"] is True

    def test_nonzero_threshold_enforced(self):
        res = HardThresholdRule().check(
            _metrics(ann_return=0.05), _cfg(hard_ann_return_min=0.10)
        )
        assert res.detail["ann_return"] is False


class TestMissingMetricsDefaultZero:
    def test_missing_fields_fail_closed_under_all_mode(self):
        res = HardThresholdRule().check({}, _cfg())
        assert res.passed is False
        assert res.detail["trades"] is False  # 0 < 30
        assert res.detail["sharpe"] is False


class TestRequireAllVsAny:
    def test_all_mode_requires_every_check(self):
        res = HardThresholdRule().check(_metrics(sharpe=-1.0), _cfg(require_all_hard=True))
        assert res.passed is False

    def test_any_mode_passes_if_one_ok(self):
        res = HardThresholdRule().check(
            _metrics(calmar=10.0, sharpe=-1.0, max_dd=-0.99, trades=0),
            _cfg(require_all_hard=False),
        )
        assert res.passed is True
        assert res.detail["calmar"] is True
        assert res.detail["sharpe"] is False

    def test_any_mode_fails_if_all_bad(self):
        res = HardThresholdRule().check(
            _metrics(calmar=-1.0, sharpe=-1.0, max_dd=-0.99, trades=0, ann_return=-1.0),
            _cfg(require_all_hard=False, hard_ann_return_min=0.5),
        )
        assert res.passed is False

    def test_notes_mention_mode(self):
        r_all = HardThresholdRule().check(_metrics(), _cfg(require_all_hard=True))
        r_any = HardThresholdRule().check(_metrics(), _cfg(require_all_hard=False))
        assert "all" in r_all.notes
        assert "any" in r_any.notes
