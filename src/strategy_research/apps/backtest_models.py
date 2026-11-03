"""BacktestResult — W4 shim.

Canonical definition lives in `core.utils.backtest_models`.
This module remains so existing `from ..backtest_models import BacktestResult`
call sites keep working.
"""

from __future__ import annotations

from strategy_research.foundation.utils.backtest_models import BacktestResult

__all__ = ["BacktestResult"]
