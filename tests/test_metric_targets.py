"""Unit tests for study.metric_targets — comparison operators and mapping."""
from __future__ import annotations

import pytest

from strategy_research.apps.study.method.metric_targets import (
    acceptance_config_from_targets,
    meets_metric_targets,
    metric_pass_set,
    target_failures,
)


class TestMeetsMetricTargets:
    def test_empty_targets_pass(self):
        assert meets_metric_targets({"sharpe": 1.0}, []) is True

    def test_ge_pass_and_fail(self):
        assert meets_metric_targets({"sharpe": 1.0}, [{"name": "sharpe", "op": ">=", "value": 0.5}])
        assert not meets_metric_targets({"sharpe": 0.1}, [{"name": "sharpe", "op": ">=", "value": 0.5}])

    @pytest.mark.parametrize("op,value,actual,expected", [
        ("<=", 1.0, 0.5, True),
        ("<=", 0.5, 1.0, False),
        (">", 1.0, 1.0, False),
        (">", 0.9, 1.0, True),
        ("<", 1.0, 1.0, False),
        ("<", 1.1, 1.0, True),
        ("==", 1.0, 1.0, True),
        ("==", 1.0, 1.1, False),
    ])
    def test_operators(self, op, value, actual, expected):
        res = meets_metric_targets(
            {"m": actual}, [{"name": "m", "op": op, "value": value}]
        )
        assert res is expected

    def test_default_op_is_ge(self):
        assert meets_metric_targets({"m": 2.0}, [{"name": "m", "value": 1.0}])

    def test_unknown_op_fails_closed(self):
        assert not meets_metric_targets(
            {"m": 2.0}, [{"name": "m", "op": "~=", "value": 1.0}]
        )

    def test_missing_metric_fails(self):
        assert not meets_metric_targets({}, [{"name": "x", "value": 1}])

    def test_invalid_spec_fails(self):
        assert not meets_metric_targets({}, [{"name": None, "value": 1}])
        assert not meets_metric_targets({}, [{"name": "x", "value": None}])

    def test_non_numeric_fails(self):
        assert not meets_metric_targets(
            {"m": "abc"}, [{"name": "m", "value": 1}]
        )
        assert not meets_metric_targets(
            {"m": 1.0}, [{"name": "m", "value": "xyz"}]
        )

    def test_all_must_pass(self):
        targets = [
            {"name": "a", "value": 1},
            {"name": "b", "value": 1},
        ]
        assert meets_metric_targets({"a": 2, "b": 2}, targets)
        assert not meets_metric_targets({"a": 2, "b": 0}, targets)


class TestTargetFailures:
    def test_empty_is_no_failures(self):
        assert target_failures({"a": 1}, []) == []

    def test_collects_all_failures(self):
        targets = [
            {"name": "a", "op": ">=", "value": 10},
            {"name": "b", "op": ">=", "value": 10},
        ]
        failures = target_failures({"a": 1, "b": 2}, targets)
        assert len(failures) == 2

    def test_missing_metric_message(self):
        failures = target_failures({}, [{"name": "x", "op": ">=", "value": 1}])
        assert len(failures) == 1
        assert "missing" in failures[0]

    def test_invalid_spec_message(self):
        failures = target_failures({}, [{"name": None, "value": 1}])
        assert "invalid" in failures[0]

    def test_non_numeric_message(self):
        failures = target_failures({"m": "x"}, [{"name": "m", "value": 1}])
        assert "non-numeric" in failures[0]

    def test_passing_target_not_listed(self):
        failures = target_failures({"a": 10}, [{"name": "a", "op": ">=", "value": 5}])
        assert failures == []


class TestMetricPassSet:
    def test_empty(self):
        assert metric_pass_set({"a": 1}, []) == set()

    def test_only_passing_names(self):
        targets = [
            {"name": "a", "op": ">=", "value": 1},
            {"name": "b", "op": ">=", "value": 100},
        ]
        assert metric_pass_set({"a": 5, "b": 1}, targets) == {"a"}

    def test_skips_invalid_and_missing(self):
        targets = [
            {"name": None, "value": 1},
            {"name": "gone", "value": 1},
            {"name": "ok", "value": 1},
        ]
        assert metric_pass_set({"ok": 2}, targets) == {"ok"}

    def test_non_numeric_skipped(self):
        assert metric_pass_set({"m": "x"}, [{"name": "m", "value": 1}]) == set()


class TestAcceptanceConfigFromTargets:
    def test_empty_returns_default(self):
        from strategy_research.apps.strategy_acceptance import DEFAULT_CONFIG
        assert acceptance_config_from_targets(None) is DEFAULT_CONFIG
        assert acceptance_config_from_targets([]) is DEFAULT_CONFIG

    def test_calmar_maps_to_hard_calmar_min(self):
        cfg = acceptance_config_from_targets([{"name": "calmar", "value": 2.5}])
        assert cfg.hard_calmar_min == pytest.approx(2.5)

    def test_sharpe_maps(self):
        cfg = acceptance_config_from_targets([{"name": "sharpe", "value": 1.5}])
        assert cfg.hard_sharpe_min == pytest.approx(1.5)

    def test_max_dd_maps(self):
        cfg = acceptance_config_from_targets([{"name": "max_dd", "value": -0.2}])
        assert cfg.hard_max_dd_min == pytest.approx(-0.2)

    def test_unknown_names_ignored(self):
        from strategy_research.apps.strategy_acceptance import DEFAULT_CONFIG
        cfg = acceptance_config_from_targets([{"name": "not_a_field", "value": 1}])
        assert cfg.hard_calmar_min == DEFAULT_CONFIG.hard_calmar_min

    def test_combined_overrides(self):
        cfg = acceptance_config_from_targets([
            {"name": "calmar", "value": 2.0},
            {"name": "sharpe", "value": 1.0},
        ])
        assert cfg.hard_calmar_min == pytest.approx(2.0)
        assert cfg.hard_sharpe_min == pytest.approx(1.0)
