"""Tests for engine config schema."""

from __future__ import annotations

from strategy_research.apps.engine.config import BacktestConfigSchema


class TestBacktestConfigSchema:
    def test_valid_config(self):
        config = {
            "codes": ["AAPL"],
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        }
        errors = BacktestConfigSchema.validate_config(config)
        assert errors == []

    def test_missing_codes(self):
        config = {
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        }
        errors = BacktestConfigSchema.validate_config(config)
        assert len(errors) > 0

    def test_empty_codes(self):
        config = {
            "codes": [],
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        }
        errors = BacktestConfigSchema.validate_config(config)
        assert len(errors) > 0

    def test_missing_dates(self):
        config = {"codes": ["AAPL"]}
        errors = BacktestConfigSchema.validate_config(config)
        assert len(errors) >= 2  # Missing both dates

    def test_default_values(self):
        schema = BacktestConfigSchema(
            codes=["AAPL"],
            start_date="2023-01-01",
            end_date="2023-12-31",
        )
        assert schema.interval == "1D"
        assert schema.source == "duckdb"
        assert schema.initial_cash == 1_000_000
        assert schema.leverage == 1.0
        assert schema.engine == "auto"

    def test_negative_cash_invalid(self):
        config = {
            "codes": ["AAPL"],
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
            "initial_cash": -100,
        }
        errors = BacktestConfigSchema.validate_config(config)
        assert len(errors) > 0

    def test_low_leverage_invalid(self):
        config = {
            "codes": ["AAPL"],
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
            "leverage": 0.5,
        }
        errors = BacktestConfigSchema.validate_config(config)
        assert len(errors) > 0

    def test_optional_fields(self):
        schema = BacktestConfigSchema(
            codes=["AAPL"],
            start_date="2023-01-01",
            end_date="2023-12-31",
            signal_engine_path="/path/to/signal.py",
            validation={"method": "monte_carlo"},
            optimizer="risk_parity",
        )
        assert schema.signal_engine_path == "/path/to/signal.py"
        assert schema.validation == {"method": "monte_carlo"}
        assert schema.optimizer == "risk_parity"
