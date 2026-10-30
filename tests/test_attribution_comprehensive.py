"""Attribution module comprehensive tests — classify, precision, edge cases."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.study.method.attribution import (
    AttributionOutcome,
    classify_attribution,
    compute_precision,
)


class TestClassifyAttribution:
    def test_all_flipped(self):
        """All metrics improved from F→T."""
        result = classify_attribution(
            predicted_tasks=["calmar", "sharpe"],
            passed_before=set(),
            passed_now={"calmar", "sharpe"},
        )
        assert result["calmar"] == AttributionOutcome.FLIPPED
        assert result["sharpe"] == AttributionOutcome.FLIPPED

    def test_all_regressed(self):
        """All metrics regressed from T→F."""
        result = classify_attribution(
            predicted_tasks=["calmar", "sharpe"],
            passed_before={"calmar", "sharpe"},
            passed_now=set(),
        )
        assert result["calmar"] == AttributionOutcome.REGRESSED
        assert result["sharpe"] == AttributionOutcome.REGRESSED

    def test_all_still_true(self):
        """All metrics still met."""
        result = classify_attribution(
            predicted_tasks=["calmar", "sharpe"],
            passed_before={"calmar", "sharpe"},
            passed_now={"calmar", "sharpe"},
        )
        assert result["calmar"] == AttributionOutcome.STILL_T
        assert result["sharpe"] == AttributionOutcome.STILL_T

    def test_all_still_false(self):
        """All metrics still not met."""
        result = classify_attribution(
            predicted_tasks=["calmar", "sharpe"],
            passed_before=set(),
            passed_now=set(),
        )
        assert result["calmar"] == AttributionOutcome.STILL_F
        assert result["sharpe"] == AttributionOutcome.STILL_F

    def test_mixed_outcomes(self):
        """Mix of all four outcomes."""
        result = classify_attribution(
            predicted_tasks=["calmar", "sharpe", "max_dd", "sortino"],
            passed_before={"calmar", "sharpe"},
            passed_now={"calmar", "max_dd"},
        )
        assert result["calmar"] == AttributionOutcome.STILL_T
        assert result["sharpe"] == AttributionOutcome.REGRESSED
        assert result["max_dd"] == AttributionOutcome.FLIPPED
        assert result["sortino"] == AttributionOutcome.STILL_F

    def test_empty_predicted_tasks(self):
        """No predicted tasks returns empty dict."""
        result = classify_attribution(
            predicted_tasks=[],
            passed_before={"calmar"},
            passed_now={"calmar"},
        )
        assert result == {}

    def test_single_metric_flipped(self):
        result = classify_attribution(
            predicted_tasks=["calmar"],
            passed_before=set(),
            passed_now={"calmar"},
        )
        assert result["calmar"] == AttributionOutcome.FLIPPED

    def test_single_metric_regressed(self):
        result = classify_attribution(
            predicted_tasks=["calmar"],
            passed_before={"calmar"},
            passed_now=set(),
        )
        assert result["calmar"] == AttributionOutcome.REGRESSED

    def test_predicted_task_not_in_either_set(self):
        """Predicted task not in before or now → STILL_F."""
        result = classify_attribution(
            predicted_tasks=["unknown_metric"],
            passed_before={"calmar"},
            passed_now={"calmar"},
        )
        assert result["unknown_metric"] == AttributionOutcome.STILL_F

    def test_duplicate_predicted_tasks(self):
        """Duplicate predicted tasks produce single entry."""
        result = classify_attribution(
            predicted_tasks=["calmar", "calmar"],
            passed_before=set(),
            passed_now={"calmar"},
        )
        assert len(result) == 1
        assert result["calmar"] == AttributionOutcome.FLIPPED


class TestComputePrecision:
    def test_all_flipped(self):
        """All FLIPPED → precision = 1.0."""
        attr = {"calmar": AttributionOutcome.FLIPPED, "sharpe": AttributionOutcome.FLIPPED}
        precision, hits, total = compute_precision(attr)
        assert precision == 1.0
        assert hits == 2
        assert total == 2

    def test_all_regressed(self):
        """All REGRESSED → precision = 0.0 (hits=0, total includes regressions)."""
        attr = {"calmar": AttributionOutcome.REGRESSED, "sharpe": AttributionOutcome.REGRESSED}
        precision, hits, total = compute_precision(attr)
        assert precision == 0.0
        assert hits == 0
        assert total == 4  # attributed=2 + side_effects=2

    def test_mixed_flipped_and_regressed(self):
        """Mix: 1 FLIPPED + 1 REGRESSED → precision = 1/3."""
        attr = {"calmar": AttributionOutcome.FLIPPED, "sharpe": AttributionOutcome.REGRESSED}
        precision, hits, total = compute_precision(attr)
        assert hits == 1
        assert total == 3  # attributed=2 + side_effects=1
        assert precision == 1 / 3

    def test_absent_excluded(self):
        """ABSENT metrics excluded from computation."""
        attr = {
            "calmar": AttributionOutcome.FLIPPED,
            "sharpe": AttributionOutcome.ABSENT,
            "max_dd": AttributionOutcome.REGRESSED,
        }
        precision, hits, total = compute_precision(attr)
        # attributed=2 (FLIPPED+REGRESSED), side_effects=1, total=3
        assert hits == 1
        assert total == 3
        assert precision == 1 / 3

    def test_empty_attribution(self):
        """Empty attribution → precision = 0.0."""
        precision, hits, total = compute_precision({})
        assert precision == 0.0
        assert hits == 0
        assert total == 0

    def test_only_still_true(self):
        """All STILL_T → no FLIPPED, no REGRESSED → precision = 0.0."""
        attr = {"calmar": AttributionOutcome.STILL_T}
        precision, hits, total = compute_precision(attr)
        assert precision == 0.0
        assert hits == 0
        assert total == 1  # attributed but not flipped

    def test_only_still_false(self):
        """All STILL_F → precision = 0.0."""
        attr = {"calmar": AttributionOutcome.STILL_F}
        precision, hits, total = compute_precision(attr)
        assert precision == 0.0
        assert hits == 0
        assert total == 1

    def test_complex_scenario(self):
        """Realistic: 2 FLIPPED + 1 REGRESSED + 1 STILL_T + 1 ABSENT."""
        attr = {
            "calmar": AttributionOutcome.FLIPPED,
            "sharpe": AttributionOutcome.FLIPPED,
            "max_dd": AttributionOutcome.REGRESSED,
            "sortino": AttributionOutcome.STILL_T,
            "turnover": AttributionOutcome.ABSENT,
        }
        precision, hits, total = compute_precision(attr)
        # attributed=4 (2 FLIPPED + 1 REGRESSED + 1 STILL_T), side_effects=1, total=5
        assert hits == 2
        assert total == 5
        assert precision == 2 / 5


class TestAttributionOutcome:
    def test_enum_values(self):
        assert AttributionOutcome.FLIPPED == "flipped"
        assert AttributionOutcome.STILL_F == "still_F"
        assert AttributionOutcome.REGRESSED == "regressed"
        assert AttributionOutcome.STILL_T == "still_T"
        assert AttributionOutcome.ABSENT == "absent"

    def test_all_values_covered(self):
        assert len(AttributionOutcome) == 5
