"""Tests for engine signals module."""

from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.apps.engine.signals import (
    ConstantWeightEngine,
    SignalEngine,
)


class TestConstantWeightEngine:
    def test_basic_weights(self):
        engine = ConstantWeightEngine({"AAPL": 0.5, "GOOG": -0.3})
        dates = pd.date_range("2023-01-01", periods=5)
        data_map = {
            "AAPL": pd.DataFrame({"close": [100, 101, 102, 103, 104]}, index=dates),
            "GOOG": pd.DataFrame({"close": [200, 201, 202, 203, 204]}, index=dates),
        }
        signals = engine.generate(data_map)
        assert len(signals) == 2
        assert all(signals["AAPL"].values == 0.5)
        assert all(signals["GOOG"].values == -0.3)

    def test_default_weight_zero(self):
        engine = ConstantWeightEngine({})
        dates = pd.date_range("2023-01-01", periods=3)
        data_map = {"AAPL": pd.DataFrame({"close": [100, 101, 102]}, index=dates)}
        signals = engine.generate(data_map)
        assert all(signals["AAPL"].values == 0.0)

    def test_index_alignment(self):
        engine = ConstantWeightEngine({"AAPL": 1.0})
        dates = pd.date_range("2023-01-01", periods=10)
        data_map = {"AAPL": pd.DataFrame({"close": range(100, 110)}, index=dates)}
        signals = engine.generate(data_map)
        assert len(signals["AAPL"]) == 10
        assert signals["AAPL"].index.equals(dates)


class TestSignalEngineAbstract:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            SignalEngine()

    def test_must_implement_generate(self):
        class IncompleteEngine(SignalEngine):
            pass
        with pytest.raises(TypeError):
            IncompleteEngine()

    def test_complete_subclass(self):
        class SimpleEngine(SignalEngine):
            def generate(self, data_map):
                return {code: pd.Series(0, index=df.index) for code, df in data_map.items()}

        engine = SimpleEngine()
        dates = pd.date_range("2023-01-01", periods=3)
        data_map = {"AAPL": pd.DataFrame({"close": [1, 2, 3]}, index=dates)}
        signals = engine.generate(data_map)
        assert "AAPL" in signals
