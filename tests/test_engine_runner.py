"""Tests for runner — AST guard + engine routing + config validation"""

from __future__ import annotations

import pytest

from strategy_research.apps.engine.config import BacktestConfigSchema
from strategy_research.apps.engine.runner import (
    _create_market_engine,
    _validate_signal_engine_class,
    _validate_signal_engine_source,
)
from strategy_research.apps.engine.signals import SignalEngine

# ============================================================
# Config validation
# ============================================================


class TestConfigSchema:
    def test_valid_config(self):
        cfg = BacktestConfigSchema(
            codes=["000001.SZ", "600000.SH"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert cfg.interval == "1D"
        assert cfg.initial_cash == 1_000_000

    def test_empty_codes_rejected(self):
        with pytest.raises(Exception):
            BacktestConfigSchema(
                codes=[],
                start_date="2024-01-01",
                end_date="2024-12-31",
            )

    def test_defaults(self):
        cfg = BacktestConfigSchema(
            codes=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert cfg.leverage == 1.0
        assert cfg.source == "duckdb"

    def test_validate_config_valid(self):
        errors = BacktestConfigSchema.validate_config({
            "codes": ["AAPL"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        })
        assert errors == []

    def test_validate_config_empty_codes(self):
        errors = BacktestConfigSchema.validate_config({
            "codes": [],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        })
        assert len(errors) > 0

    def test_validate_config_missing_fields(self):
        errors = BacktestConfigSchema.validate_config({})
        assert len(errors) > 0

    def test_validate_config_bad_leverage(self):
        errors = BacktestConfigSchema.validate_config({
            "codes": ["AAPL"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "leverage": 0.5,  # must be >= 1.0
        })
        assert len(errors) > 0


# ============================================================
# AST Guard
# ============================================================


class TestASTGuard:
    def test_valid_signal_engine(self, tmp_path):
        code = '''
"""Valid signal engine."""

import pandas as pd
import numpy as np


class SignalEngine:
    def generate(self, data_map):
        result = {}
        for code, df in data_map.items():
            result[code] = pd.Series(0.5, index=df.index)
        return result
'''
        f = tmp_path / "signal_engine.py"
        f.write_text(code)
        from strategy_research.apps.engine.runner import _validate_signal_engine_source
        _validate_signal_engine_source(f)  # Should not raise

    def test_rejects_decorator(self, tmp_path):
        code = '''
import pandas as pd

def my_decorator(f):
    return f

@my_decorator
class SignalEngine:
    def generate(self, data_map):
        return {}
'''
        f = tmp_path / "signal_engine.py"
        f.write_text(code)
        with pytest.raises(ValueError, match="Decorators not allowed"):
            _validate_signal_engine_source(f)

    def test_rejects_import_os(self, tmp_path):
        code = '''
import os

class SignalEngine:
    def generate(self, data_map):
        return {}
'''
        f = tmp_path / "signal_engine.py"
        f.write_text(code)
        with pytest.raises(ValueError, match="Unsafe module import"):
            _validate_signal_engine_source(f)

    def test_rejects_exec(self, tmp_path):
        code = '''
exec("import os")

class SignalEngine:
    def generate(self, data_map):
        return {}
'''
        f = tmp_path / "signal_engine.py"
        f.write_text(code)
        with pytest.raises(ValueError):
            _validate_signal_engine_source(f)

    def test_rejects_circular_import(self, tmp_path):
        code = '''
from signal_engine import something

class SignalEngine:
    def generate(self, data_map):
        return {}
'''
        f = tmp_path / "signal_engine.py"
        f.write_text(code)
        with pytest.raises(ValueError, match="Circular import"):
            _validate_signal_engine_source(f)

    def test_rejects_non_literal_default(self, tmp_path):
        code = '''
import pandas as pd

def f(x=get_list()):
    pass

class SignalEngine:
    def generate(self, data_map):
        return {}
'''
        f = tmp_path / "signal_engine.py"
        f.write_text(code)
        with pytest.raises(ValueError, match="Non-literal default"):
            _validate_signal_engine_source(f)

    def test_rejects_executable_class_body(self, tmp_path):
        code = '''
import pandas as pd

class SignalEngine:
    x = os.system("ls")  # executable statement
    def generate(self, data_map):
        return {}
'''
        f = tmp_path / "signal_engine.py"
        f.write_text(code)
        with pytest.raises(ValueError, match="Executable statement"):
            _validate_signal_engine_source(f)

    def test_syntax_error(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("def f(:\n  pass")
        with pytest.raises(ValueError, match="syntax"):
            _validate_signal_engine_source(f)


# ============================================================
# Engine class validation
# ============================================================


class TestEngineClassValidation:
    def test_valid_class(self):
        class GoodEngine(SignalEngine):
            def generate(self, data_map):
                return {}
        _validate_signal_engine_class(GoodEngine)  # Should not raise

    def test_missing_generate(self):
        class BadEngine:
            pass
        with pytest.raises(ValueError, match="generate"):
            _validate_signal_engine_class(BadEngine)

    def test_required_init_args(self):
        class BadInit(SignalEngine):
            def __init__(self, x):
                pass
            def generate(self, data_map):
                return {}
        with pytest.raises(ValueError, match="required arguments"):
            _validate_signal_engine_class(BadInit)


# ============================================================
# Engine routing
# ============================================================


class TestEngineRouting:
    def test_china_a_routing(self):
        engine = _create_market_engine({}, ["000001.SZ"])
        assert type(engine).__name__ == "ChinaAEngine"

    def test_us_equity_routing(self):
        engine = _create_market_engine({}, ["AAPL"])
        assert type(engine).__name__ == "GlobalEquityEngine"

    def test_crypto_routing(self):
        engine = _create_market_engine({}, ["BTC-USDT"])
        assert type(engine).__name__ == "CryptoEngine"

    def test_forex_routing(self):
        engine = _create_market_engine({}, ["EUR/USD"])
        assert type(engine).__name__ == "ForexEngine"

    def test_multi_market_composite(self):
        engine = _create_market_engine({}, ["000001.SZ", "AAPL"])
        assert type(engine).__name__ == "CompositeEngine"

    def test_explicit_engine_type(self):
        engine = _create_market_engine({"engine": "crypto"}, ["AAPL"])
        assert type(engine).__name__ == "CryptoEngine"
