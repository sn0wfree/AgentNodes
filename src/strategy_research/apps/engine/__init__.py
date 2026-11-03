"""Backtest engine — bar-by-bar 执行引擎。"""

from .artifacts import write_all_artifacts
from .base import BaseEngine
from .china_a import ChinaAEngine
from .china_futures import ChinaFuturesEngine
from .composite import CompositeEngine
from .config import BacktestConfigSchema
from .crypto import CryptoEngine
from .forex import ForexEngine
from .futures_base import FuturesBaseEngine
from .global_equity import GlobalEquityEngine
from .global_futures import GlobalFuturesEngine
from .india_equity import IndiaEquityEngine
from .market_hooks import calc_crypto_funding_fee, check_crypto_liquidation
from .models import EquitySnapshot, Position, TradeRecord
from .runner import _create_market_engine, _load_signal_engine, _validate_signal_engine_source, run_engine_backtest
from .signals import ConstantWeightEngine, SignalEngine

__all__ = [
    "BaseEngine",
    "SignalEngine",
    "ConstantWeightEngine",
    "Position",
    "TradeRecord",
    "EquitySnapshot",
    "ChinaAEngine",
    "GlobalEquityEngine",
    "CryptoEngine",
    "ForexEngine",
    "IndiaEquityEngine",
    "FuturesBaseEngine",
    "ChinaFuturesEngine",
    "GlobalFuturesEngine",
    "CompositeEngine",
    "BacktestConfigSchema",
    "write_all_artifacts",
    "calc_crypto_funding_fee",
    "check_crypto_liquidation",
    "_create_market_engine",
    "_load_signal_engine",
    "_validate_signal_engine_source",
    "run_engine_backtest",
]
