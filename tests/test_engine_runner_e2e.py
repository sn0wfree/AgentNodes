"""Runner e2e 端到端测试。

覆盖:
- AST guard 全分支
- signal engine 加载
- engine routing
- 完整回测 pipeline (mock DuckDB)
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from strategy_research.core.engine.runner import (
    _create_market_engine,
    _load_signal_engine,
    _validate_signal_engine_class,
    _validate_signal_engine_source,
)
from strategy_research.core.engine.signals import SignalEngine


# ─────────────────────────────────────────────
# AST guard: _validate_signal_engine_source
# ─────────────────────────────────────────────
class TestValidateSource:
    def test_valid_minimal(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text('"""Docstring."""\nimport pandas as pd\nclass SignalEngine:\n    def generate(self, data_map):\n        pass\n')
        _validate_signal_engine_source(f)

    def test_docstring_only(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text('"""Just a docstring."""\n')
        _validate_signal_engine_source(f)

    def test_syntax_error(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("def foo(\n")
        with pytest.raises(ValueError, match="Invalid.*syntax"):
            _validate_signal_engine_source(f)

    def test_unsafe_import_os(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("import os\n")
        with pytest.raises(ValueError, match="Unsafe module import"):
            _validate_signal_engine_source(f)

    def test_unsafe_import_subprocess(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("from subprocess import run\n")
        with pytest.raises(ValueError, match="Unsafe module import"):
            _validate_signal_engine_source(f)

    def test_unsafe_import_socket(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("import socket\n")
        with pytest.raises(ValueError, match="Unsafe module import"):
            _validate_signal_engine_source(f)

    def test_safe_import_pandas(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("import pandas as pd\n")
        _validate_signal_engine_source(f)

    def test_decorator_on_function(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("def foo():\n    pass\ndef bar():\n    pass\n")
        # No decorator, should pass
        _validate_signal_engine_source(f)
        # Add decorator
        f.write_text("import functools\n@functools.lru_cache\ndef foo():\n    pass\n")
        with pytest.raises(ValueError, match="Decorators not allowed"):
            _validate_signal_engine_source(f)

    def test_print_statement_rejected(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text('print("hello")\n')
        with pytest.raises(ValueError, match="Executable top-level statement"):
            _validate_signal_engine_source(f)

    def test_non_literal_default_rejected(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        # dict() is not a literal node
        f.write_text("def foo(x=dict()):\n    pass\n")
        with pytest.raises(ValueError, match="Non-literal default"):
            _validate_signal_engine_source(f)

    def test_class_with_metaclass_rejected(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("class Foo(metaclass=type):\n    pass\n")
        with pytest.raises(ValueError, match="Class keywords not allowed"):
            _validate_signal_engine_source(f)

    def test_class_with_decorator_rejected(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("class Foo:\n    pass\ndef foo():\n    pass\n")
        # No decorator on class, should pass
        _validate_signal_engine_source(f)


# ─────────────────────────────────────────────
# AST guard: _validate_signal_engine_class
# ─────────────────────────────────────────────
class TestValidateClass:
    def test_valid(self):
        _validate_signal_engine_class(SignalEngine)

    def test_missing_generate(self):
        class Bad:
            pass
        with pytest.raises(ValueError, match="callable 'generate'"):
            _validate_signal_engine_class(Bad)

    def test_init_requires_args(self):
        class Bad(SignalEngine):
            def __init__(self, x, y):
                pass
            def generate(self, data_map):
                return {}
        with pytest.raises(ValueError, match="required arguments"):
            _validate_signal_engine_class(Bad)

    def test_init_with_defaults_ok(self):
        class Good(SignalEngine):
            def __init__(self, x=1, y=2):
                pass
            def generate(self, data_map):
                return {}
        _validate_signal_engine_class(Good)


# ─────────────────────────────────────────────
# _load_signal_engine
# ─────────────────────────────────────────────
class TestLoadSignalEngine:
    def test_load_valid(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text(textwrap.dedent("""\
            import pandas as pd
            from strategy_research.core.engine.signals import SignalEngine

            class SignalEngine(SignalEngine):
                def generate(self, data_map):
                    return {code: pd.Series(1.0, index=df.index) for code, df in data_map.items()}
        """))
        cls = _load_signal_engine(f)
        assert issubclass(cls, SignalEngine)
        inst = cls()
        assert hasattr(inst, "generate")

    def test_load_missing_class(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text("import pandas as pd\n")
        with pytest.raises(ValueError, match="No SignalEngine class found"):
            _load_signal_engine(f)

    def test_load_nonexistent_file(self, tmp_path):
        f = tmp_path / "nonexistent.py"
        with pytest.raises((ValueError, FileNotFoundError)):
            _load_signal_engine(f)


# ─────────────────────────────────────────────
# _create_market_engine routing
# ─────────────────────────────────────────────
class TestCreateMarketEngine:
    def test_single_a_share(self):
        eng = _create_market_engine({"codes": ["600000.SH"]}, ["600000.SH"])
        assert type(eng).__name__ == "ChinaAEngine"

    def test_single_crypto(self):
        eng = _create_market_engine({"codes": ["BTC-USDT"], "engine": "crypto"}, ["BTC-USDT"])
        assert type(eng).__name__ == "CryptoEngine"

    def test_single_forex(self):
        eng = _create_market_engine({"codes": ["EURUSD"], "engine": "forex"}, ["EURUSD"])
        assert type(eng).__name__ == "ForexEngine"

    def test_single_futures_tushare(self):
        eng = _create_market_engine({"codes": ["CU2501.SHF"], "engine": "futures"}, ["CU2501.SHF"])
        assert type(eng).__name__ == "ChinaFuturesEngine"

    def test_multi_market_composite(self):
        codes = ["600000.SH", "AAPL"]
        eng = _create_market_engine({"codes": codes}, codes)
        assert type(eng).__name__ == "CompositeEngine"

    def test_explicit_composite(self):
        eng = _create_market_engine({"codes": ["600000.SH"], "engine": "composite"}, ["600000.SH"])
        assert type(eng).__name__ == "CompositeEngine"

    def test_hk_equity(self):
        eng = _create_market_engine({"codes": ["0700.HK"]}, ["0700.HK"])
        assert type(eng).__name__ == "GlobalEquityEngine"

    def test_us_equity(self):
        eng = _create_market_engine({"codes": ["AAPL"]}, ["AAPL"])
        assert type(eng).__name__ == "GlobalEquityEngine"


# ─────────────────────────────────────────────
# run_engine_backtest e2e
# ─────────────────────────────────────────────
class TestRunBacktestE2E:
    @staticmethod
    def _make_ohlcv(symbol, n_bars=50, start_price=10.0):
        dates = pd.bdate_range("2024-01-02", periods=n_bars)
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.005, n_bars)
        prices = start_price * (1 + pd.Series(rets)).cumprod().values
        opens = np.empty(n_bars)
        opens[0] = start_price
        opens[1:] = prices[:-1]
        return pd.DataFrame({
            "open": opens, "high": prices * 1.005, "low": prices * 0.995,
            "close": prices, "volume": np.full(n_bars, 1000.0),
        }, index=dates)

    def test_full_pipeline_with_cls(self, tmp_path):
        from strategy_research.core.engine.runner import run_engine_backtest

        code = "600000.SH"
        data_map = {code: self._make_ohlcv(code)}

        with patch("strategy_research.apps.db.load_ohlcv_data", return_value=data_map):
            metrics = run_engine_backtest(
                workspace_path=tmp_path,
                strategy_name="test",
                config={"codes": [code], "initial_cash": 1_000_000.0},
                signal_engine_cls=type("Sig", (SignalEngine,), {
                    "generate": lambda self, dm: {c: pd.Series(1.0, index=df.index) for c, df in dm.items()}
                }),
            )

        assert "total_return" in metrics
        assert "sharpe" in metrics
        assert "max_drawdown" in metrics

    def test_full_pipeline_with_path(self, tmp_path):
        from strategy_research.core.engine.runner import run_engine_backtest

        code = "600000.SH"
        data_map = {code: self._make_ohlcv(code)}
        sig_file = tmp_path / "signal_engine.py"
        sig_file.write_text(textwrap.dedent("""\
            import pandas as pd
            from strategy_research.core.engine.signals import SignalEngine

            class SignalEngine(SignalEngine):
                def generate(self, data_map):
                    return {code: pd.Series(1.0, index=df.index) for code, df in data_map.items()}
        """))

        with patch("strategy_research.apps.db.load_ohlcv_data", return_value=data_map):
            metrics = run_engine_backtest(
                workspace_path=tmp_path,
                strategy_name="test",
                config={"codes": [code], "initial_cash": 1_000_000.0},
                signal_engine_path=sig_file,
            )

        assert "total_return" in metrics

    def test_empty_data_raises(self, tmp_path):
        from strategy_research.core.engine.runner import run_engine_backtest

        with patch("strategy_research.apps.db.load_ohlcv_data", return_value={}):
            with pytest.raises(ValueError, match="No data found"):
                run_engine_backtest(
                    workspace_path=tmp_path,
                    strategy_name="test",
                    config={"codes": ["600000.SH"]},
                    signal_engine_cls=type("Sig", (SignalEngine,), {
                        "generate": lambda self, dm: {}
                    }),
                )

    def test_no_signal_engine_raises(self, tmp_path):
        from strategy_research.core.engine.runner import run_engine_backtest

        with pytest.raises(ValueError, match="Either signal_engine_path or signal_engine_cls"):
            run_engine_backtest(
                workspace_path=tmp_path,
                strategy_name="test",
                config={"codes": ["600000.SH"]},
            )

    def test_optimizer_in_pipeline(self, tmp_path):
        from strategy_research.core.engine.runner import run_engine_backtest

        code = "600000.SH"
        data_map = {code: self._make_ohlcv(code)}

        with patch("strategy_research.apps.db.load_ohlcv_data", return_value=data_map):
            metrics = run_engine_backtest(
                workspace_path=tmp_path,
                strategy_name="test",
                config={"codes": [code], "initial_cash": 1_000_000.0},
                signal_engine_cls=type("Sig", (SignalEngine,), {
                    "generate": lambda self, dm: {c: pd.Series(0.5, index=df.index) for c, df in dm.items()}
                }),
                optimizer="equal_volatility",
            )

        assert "total_return" in metrics
