"""Tests for study/attribution.py — AEGIS attribution classifier."""
from strategy_research.core.study.method.attribution import (
    AttributionOutcome,
    classify_attribution,
    compute_precision,
)


class TestClassifyAttribution:
    def test_flipped_f_to_t(self):
        result = classify_attribution(["calmar"], set(), {"calmar"})
        assert result["calmar"] == AttributionOutcome.FLIPPED

    def test_regressed_t_to_f(self):
        result = classify_attribution(["calmar"], {"calmar"}, set())
        assert result["calmar"] == AttributionOutcome.REGRESSED

    def test_still_t(self):
        result = classify_attribution(["calmar"], {"calmar"}, {"calmar"})
        assert result["calmar"] == AttributionOutcome.STILL_T

    def test_still_f(self):
        result = classify_attribution(["calmar"], set(), set())
        assert result["calmar"] == AttributionOutcome.STILL_F

    def test_multiple_tasks(self):
        result = classify_attribution(
            ["calmar", "sharpe", "max_dd"],
            {"calmar", "sharpe"},
            {"calmar", "max_dd"},
        )
        assert result["calmar"] == AttributionOutcome.STILL_T
        assert result["sharpe"] == AttributionOutcome.REGRESSED
        assert result["max_dd"] == AttributionOutcome.FLIPPED

    def test_empty_predicted(self):
        result = classify_attribution([], set(), {"calmar"})
        assert result == {}

    def test_absent_not_in_now(self):
        # predicted task not in passed_now but was in passed_before = STILL_T (already met)
        result = classify_attribution(["calmar"], {"calmar"}, {"calmar"})
        assert result["calmar"] == AttributionOutcome.STILL_T


class TestComputePrecision:
    def test_all_flipped(self):
        attr = {"calmar": "flipped", "sharpe": "flipped"}
        p, h, t = compute_precision(attr)
        assert p == 1.0
        assert h == 2
        assert t == 2

    def test_all_regressed(self):
        attr = {"calmar": "regressed", "sharpe": "regressed"}
        p, h, t = compute_precision(attr)
        assert p == 0.0
        assert h == 0
        assert t == 4  # 2 attributed + 2 side_effects

    def test_mixed(self):
        attr = {"calmar": "flipped", "sharpe": "still_F", "max_dd": "regressed"}
        p, h, t = compute_precision(attr)
        assert p == 0.25  # 1 hit / (3 attributed + 1 side_effect)
        assert h == 1
        assert t == 4

    def test_absent_excluded(self):
        attr = {"calmar": "flipped", "sharpe": "absent"}
        p, h, t = compute_precision(attr)
        assert p == 1.0
        assert h == 1
        assert t == 1

    def test_empty(self):
        p, h, t = compute_precision({})
        assert p == 0.0
        assert h == 0
        assert t == 0

    def test_still_t_no_effect(self):
        attr = {"calmar": "still_T"}
        p, h, t = compute_precision(attr)
        assert p == 0.0  # no hits, no side effects
        assert h == 0
        assert t == 1
