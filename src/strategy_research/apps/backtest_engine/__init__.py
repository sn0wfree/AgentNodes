"""BacktestEngine subpackage (P0-3).

Re-exports ``Strategy`` + ``BacktestEngine`` (Protocols) + factory
functions. The default providers are registered in ``factory.py`` at
import time.
"""

from .factory import (
    BacktestEngineRegistry,
    create_engine,
    get_engine,
    register_engine,
)
from .protocol import BacktestEngine, Strategy

__all__ = [
    "BacktestEngine",
    "BacktestEngineRegistry",
    "Strategy",
    "create_engine",
    "get_engine",
    "register_engine",
]
