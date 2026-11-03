"""BacktestEngine + Strategy Protocols (P0-3).

P0-2.B unified ``BacktestResult`` (canonical dataclass lives in
``core/backtest_models.py``). P0-3 takes the next step: collapse the
two backtest entry points — ``StrategyEngine.run`` (YAML path,
``utils.strategy_engine``) and ``run_backtest`` (Callback path,
``utils.backtest_engine``) — behind a single ``BacktestEngine`` Protocol.

``Strategy`` Protocol:
- Single ``compute_weights(date, price_panel, nav_history)`` method
  (matches ``BaseStrategy`` and ``FactorStrategy`` already).
- Optional ``on_risk_check`` hook (matches ``BaseStrategy``).

``BacktestEngine`` Protocol:
- ``run(strategy, price_panel, *, config=None) -> BacktestResult``.
  Engine implementations may accept extra kwargs (rebal_freq, cost,
  min_history) via the optional ``config`` parameter or stay simple.

Why a single ``compute_weights`` signature for everything:
- YAML / Factor paths already implement it.
- The Callback path's five-step flow (compute_signals / select_assets
  / compute_weights / apply_risk / post_weights) becomes an internal
  detail of ``CallbackEngineAdapter``; consumers keep the single-method
  Protocol surface.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pandas as pd

from strategy_research.core.backtest_models import BacktestResult


@runtime_checkable
class Strategy(Protocol):
    """Single-method strategy interface (P0-3 unified surface)."""

    def compute_weights(
        self,
        date: pd.Timestamp,
        price_panel: pd.DataFrame,
        nav_history: pd.Series,
    ) -> dict[str, float]:
        """Return the target weights for ``date`` given price history +
        nav history. Empty dict = hold cash / flat."""
        ...


@runtime_checkable
class BacktestEngine(Protocol):
    """Unified backtest runner (P0-3)."""

    def run(
        self,
        strategy: Strategy,
        price_panel: pd.DataFrame,
        *,
        config: Any | None = None,
    ) -> BacktestResult:
        """Run the strategy over ``price_panel`` and return a canonical
        ``BacktestResult``. ``config`` is engine-specific (e.g.
        ``BacktestConfig`` or a dict of legacy kwargs).
        """
        ...


__all__ = ["BacktestEngine", "Strategy"]
