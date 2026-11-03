"""StrategyEngineAdapter — wraps ``utils.strategy_engine.StrategyEngine``.

The legacy ``StrategyEngine.run(price_panel, strategy, ...)`` accepts
positional ``strategy``; the Protocol's ``run(strategy, price_panel,
*, config=None)`` reverses the order so the strategy comes first.

This adapter delegates 1:1 — every kwarg (``rebal_freq``, ``min_history``,
``cost``) is supported via the optional ``config`` parameter, which
may be either a dict or an object with matching attributes.

P0-3 only renames the surface; the internal NAV / risk / cost loop
stays in ``utils.strategy_engine`` so all 195+ existing tests remain
the equivalence oracle.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from strategy_research.apps.backtest_models import BacktestResult
from strategy_research.foundation.utils.strategy_engine import StrategyEngine


class StrategyEngineAdapter:
    """Adapter that exposes ``utils.strategy_engine.StrategyEngine`` as
    a ``BacktestEngine`` Protocol."""

    def __init__(self) -> None:
        self._engine = StrategyEngine()

    def run(
        self,
        strategy: Any,
        price_panel: pd.DataFrame,
        *,
        config: Any | None = None,
    ) -> BacktestResult:
        # ``config`` is an optional duck-typed bag of kwargs:
        # - rebal_freq: str
        # - min_history: int
        # - cost: CostConfig | None
        # - vol_targeting / trend_filter / stop_loss: passed at construction
        kwargs: dict[str, Any] = {}
        if config is not None:
            for key in ("rebal_freq", "min_history", "cost"):
                if hasattr(config, key):
                    kwargs[key] = getattr(config, key)
                elif isinstance(config, dict) and key in config:
                    kwargs[key] = config[key]
        return self._engine.run(price_panel, strategy, **kwargs)


__all__ = ["StrategyEngineAdapter"]
