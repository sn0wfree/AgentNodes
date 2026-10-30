"""Tests for falsifiable claims tracking (C1-C5).

Covers:
- C1 schema: predictions_json / prediction_outcome_json round-trip
- C4 core/study/claims.py: normalize / validate / summarize
- C3 phase_engine capture + fill (with mock runner)
- C5 build_journal_context claims-calibration block
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from strategy_research.core.goal.store import GoalStore
from strategy_research.core.study.method.claims import (
    normalize_predictions,
    summarize_prediction_accuracy,
    validate_predictions,
)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path):
    os.environ["QUANTNODES_RESEARCH_GOAL_DB_PATH"] = str(tmp_path / "goals.db")
    yield
    os.environ.pop("QUANTNODES_RESEARCH_GOAL_DB_PATH", None)


@pytest.fixture
def store():
    return GoalStore()


@pytest.fixture
def goal(store):
    return store.replace_goal(
        session_id="test-sess",
        objective="test objective",
        criteria=["calmar >= 0.5"],
    )


PREDICTIONS = {
    "sharpe": {"direction": "up", "expected": 0.8, "tolerance": 0.3},
    "max_dd": {"direction": "down", "expected": -0.10, "tolerance": 0.05},
}


# ── C1: schema round-trip ────────────────────────────────────────


class TestJournalClaimsSchema:
    def test_append_with_predictions_round_trips(self, store, goal):
        entry = store.append_journal_entry(
            goal.goal_id, "test-sess", 1, "hyp-1", "test",
            levers=["optimize"], predicted_affected=["sharpe"],
            predictions=PREDICTIONS,
        )
        assert entry.predictions == PREDICTIONS
        rows = store.list_journal_entries(goal.goal_id)
        assert rows[0].predictions == PREDICTIONS
        assert rows[0].prediction_outcome == {}

    def test_append_without_predictions_defaults_empty(self, store, goal):
        store.append_journal_entry(goal.goal_id, "test-sess", 1, "h", "l")
        rows = store.list_journal_entries(goal.goal_id)
        assert rows[0].predictions == {}
        assert rows[0].prediction_outcome == {}

    def test_fill_prediction_outcome(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "test-sess", 1, "h", "l", predictions=PREDICTIONS,
        )
        outcome = {
            "sharpe": {"actual": 0.55, "abs_error": 0.25,
                       "direction_correct": True, "within_tolerance": True},
        }
        ok = store.fill_journal_prediction_outcome(
            goal.goal_id, "test-sess", 1, outcome,
        )
        assert ok
        rows = store.list_journal_entries(goal.goal_id)
        assert rows[0].prediction_outcome == outcome

    def test_fill_outcome_idempotent_guard(self, store, goal):
        """Second fill must not overwrite (WHERE outcome = '{}')."""
        store.append_journal_entry(goal.goal_id, "test-sess", 1, "h", "l")
        first = store.fill_journal_prediction_outcome(
            goal.goal_id, "test-sess", 1, {"sharpe": {"actual": 1.0}})
        second = store.fill_journal_prediction_outcome(
            goal.goal_id, "test-sess", 1, {"sharpe": {"actual": 2.0}})
        assert first and not second
        rows = store.list_journal_entries(goal.goal_id)
        assert rows[0].prediction_outcome["sharpe"]["actual"] == 1.0

    def test_fill_attribution_backfills_levers_and_changeset(self, store, goal):
        """Two-phase write: capture leaves levers empty; fill backfills."""
        store.append_journal_entry(
            goal.goal_id, "test-sess", 1, "h", "l", levers=[],
            predictions=PREDICTIONS,
        )
        ok = store.fill_journal_attribution(
            goal.goal_id, "test-sess", 1, "accepted", {"sharpe": "flipped"},
            levers=["optimize"], changeset={"param": "x"},
        )
        assert ok
        rows = store.list_journal_entries(goal.goal_id)
        assert rows[0].levers == ["optimize"]
        assert rows[0].changeset == {"param": "x"}
        # predictions preserved through the fill
        assert rows[0].predictions == PREDICTIONS


# ── C4: claims.py ────────────────────────────────────────────────


class TestNormalizePredictions:
    def test_valid_predictions_kept(self):
        out = normalize_predictions(
            PREDICTIONS, known_metrics=["sharpe", "max_dd", "calmar"])
        assert set(out) == {"sharpe", "max_dd"}
        assert out["sharpe"] == {"direction": "up", "expected": 0.8, "tolerance": 0.3}

    def test_unknown_metric_dropped(self):
        out = normalize_predictions(
            {"sharpe": PREDICTIONS["sharpe"], "foo": {"direction": "up", "expected": 1}},
            known_metrics=["sharpe"],
        )
        assert set(out) == {"sharpe"}

    def test_malformed_entries_dropped_never_raises(self):
        raw = {
            "sharpe": {"direction": "up", "expected": 0.5},
            "calmar": {"direction": "sideways", "expected": 1.0},  # bad direction
            "max_dd": {"direction": "down", "expected": "abc"},     # bad number
            "junk": "not-a-dict",
            42: {"direction": "up", "expected": 1},
        }
        out = normalize_predictions(raw)
        assert set(out) == {"sharpe"}

    def test_none_and_non_dict_inputs(self):
        assert normalize_predictions(None) == {}
        assert normalize_predictions([1, 2]) == {}
        assert normalize_predictions("sharpe") == {}

    def test_negative_tolerance_normalized_to_zero(self):
        out = normalize_predictions(
            {"sharpe": {"direction": "up", "expected": 0.5, "tolerance": -1}})
        assert out["sharpe"]["tolerance"] == 0.0


class TestValidatePredictions:
    def test_direction_up_correct_and_wrong(self):
        preds = {"sharpe": {"direction": "up", "expected": 0.5, "tolerance": 0.1}}
        out = validate_predictions(preds, {"sharpe": 0.7})
        o = out["sharpe"]
        assert o["direction_correct"] is True
        assert o["within_tolerance"] is False
        assert o["abs_error"] == pytest.approx(0.2)

    def test_direction_down(self):
        preds = {"max_dd": {"direction": "down", "expected": -0.10, "tolerance": 0.05}}
        out = validate_predictions(preds, {"max_dd": -0.12})
        assert out["max_dd"]["direction_correct"] is True

    def test_missing_metric_skipped(self):
        preds = {"sharpe": {"direction": "up", "expected": 0.5, "tolerance": 0.1}}
        assert validate_predictions(preds, {}) == {}
        assert validate_predictions(preds, None) == {}

    def test_empty_predictions(self):
        assert validate_predictions({}, {"sharpe": 1.0}) == {}


class TestSummarizePredictionAccuracy:
    def _entry(self, predictions=None, outcome=None):
        e = MagicMock()
        e.predictions = predictions or {}
        e.prediction_outcome = outcome or {}
        return e

    def test_empty_entries(self):
        stats = summarize_prediction_accuracy([])
        assert stats["n_predictions"] == 0
        assert stats["direction_hit_rate"] is None
        assert stats["adoption_rate"] is None

    def test_adoption_rate(self):
        entries = [
            self._entry(predictions={"sharpe": {}}),
            self._entry(),
            self._entry(),
            self._entry(),
        ]
        stats = summarize_prediction_accuracy(entries)
        assert stats["n_predictions"] == 1
        assert stats["adoption_rate"] == 0.25

    def test_decay_weights_newest_more(self):
        newest = self._entry(outcome={"sharpe": {"abs_error": 0.1, "direction_correct": True}})
        oldest = self._entry(outcome={"sharpe": {"abs_error": 0.9, "direction_correct": False}})
        stats = summarize_prediction_accuracy([newest, oldest])
        # decay=0.9: newest weight 1.0, oldest 0.9 → mean_abs_error < plain mean
        assert stats["mean_abs_error"] < (0.1 + 0.9) / 2
        assert stats["direction_hit_rate"] == pytest.approx(1.0 / 1.9, abs=1e-3)


# ── C3: phase_engine capture + fill ─────────────────────────────


class TestCaptureRoundClaims:
    def _runner(self, goal_id="g1", session="s1"):
        runner = MagicMock()
        study = MagicMock()
        study.goal_id = goal_id
        study.session_id = session
        runner._get_study.return_value = study
        return runner

    def test_capture_appends_with_predictions(self):
        runner = self._runner()
        researcher_output = {
            "hypothesis": "momentum works",
            "predictions": {"sharpe": {"direction": "up", "expected": 0.8}},
        }
        from strategy_research.core.study.phase_engine import _capture_round_claims
        captured = _capture_round_claims(
            runner, 3, researcher_output, ["sharpe"],
            [{"name": "sharpe"}],
        )
        assert "sharpe" in captured
        runner._goal_store.append_journal_entry.assert_called_once()
        kwargs = runner._goal_store.append_journal_entry.call_args.kwargs
        assert kwargs["predictions"]["sharpe"]["direction"] == "up"

    def test_capture_malformed_predictions_stores_empty(self):
        runner = self._runner()
        researcher_output = {
            "hypothesis": "h",
            "predictions": {"sharpe": "garbage"},
        }
        from strategy_research.core.study.phase_engine import _capture_round_claims
        captured = _capture_round_claims(
            runner, 3, researcher_output, ["sharpe"], [{"name": "sharpe"}])
        assert captured == {}
        # entry still appended (with empty predictions) — two-phase invariant
        runner._goal_store.append_journal_entry.assert_called_once()

    def test_capture_store_failure_never_raises(self):
        runner = self._runner()
        runner._goal_store.append_journal_entry.side_effect = RuntimeError("db down")
        from strategy_research.core.study.phase_engine import _capture_round_claims
        captured = _capture_round_claims(
            runner, 3, {"hypothesis": "h", "predictions": PREDICTIONS},
            ["sharpe"], [{"name": "sharpe"}])
        assert captured == {}


class TestRecordJournalAndRegression:
    def _runner(self, regression_passes=True):
        runner = MagicMock()
        study = MagicMock()
        study.goal_id = "g1"
        study.session_id = "s1"
        runner._get_study.return_value = study
        runner._check_regression.return_value = (regression_passes, [])
        return runner

    def test_fill_path_uses_captured_predictions(self):
        runner = self._runner()
        runner._goal_store.fill_journal_attribution.return_value = True
        from strategy_research.core.study.phase_engine import _record_journal_and_regression
        _record_journal_and_regression(
            runner, 1, "hyp", ["sharpe"], "optimize", {},
            "accepted", {"sharpe": "flipped"},
            predictions=PREDICTIONS,
            metrics={"sharpe": 1.2},
        )
        # fill called with levers backfill
        kwargs = runner._goal_store.fill_journal_attribution.call_args.kwargs
        assert kwargs["levers"] == ["optimize"]
        # prediction outcome filled
        runner._goal_store.fill_journal_prediction_outcome.assert_called_once()
        outcome = runner._goal_store.fill_journal_prediction_outcome.call_args.args[-1]
        assert outcome["sharpe"]["direction_correct"] is True
        # no fallback append
        runner._goal_store.append_journal_entry.assert_not_called()

    def test_fallback_append_when_no_captured_entry(self):
        runner = self._runner()
        runner._goal_store.fill_journal_attribution.return_value = False
        from strategy_research.core.study.phase_engine import _record_journal_and_regression
        _record_journal_and_regression(
            runner, 1, "hyp", ["sharpe"], "optimize", {},
            "accepted", {"sharpe": "flipped"},
        )
        runner._goal_store.append_journal_entry.assert_called_once()

    def test_validation_failure_does_not_break_round(self):
        runner = self._runner()
        runner._goal_store.fill_journal_attribution.return_value = True
        runner._goal_store.fill_journal_prediction_outcome.side_effect = RuntimeError("x")
        from strategy_research.core.study.phase_engine import _record_journal_and_regression
        # must not raise
        _record_journal_and_regression(
            runner, 1, "hyp", ["sharpe"], "optimize", {},
            "accepted", {"sharpe": "flipped"},
            predictions=PREDICTIONS, metrics={"sharpe": 1.0},
        )


# ── C5: journal context calibration block ───────────────────────


class TestJournalContextCalibration:
    def test_context_includes_calibration_block(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "test-sess", 1, "h", "test", predictions=PREDICTIONS)
        store.fill_journal_prediction_outcome(
            goal.goal_id, "test-sess", 1,
            {"sharpe": {"actual": 0.9, "abs_error": 0.1,
                        "direction_correct": True, "within_tolerance": True}})
        ctx = store.build_journal_context(goal.goal_id, current_round=2)
        assert "<claims-calibration" in ctx
        assert "direction_hit_rate=1.0" in ctx

    def test_context_without_predictions_has_no_calibration(self, store, goal):
        store.append_journal_entry(goal.goal_id, "test-sess", 1, "h", "test")
        ctx = store.build_journal_context(goal.goal_id, current_round=2)
        assert "<claims-calibration" not in ctx