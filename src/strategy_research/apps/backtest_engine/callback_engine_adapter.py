"""CallbackEngineAdapter — wraps ``utils.backtest_engine.BacktestCallbacks``.

The legacy callback engine is a five-step pipeline:
  1. ``compute_signals(panel, date, state, context)``
  2. ``select_assets(signals, config)``
  3. ``compute_weights(selected, panel, date, config)``
  4. ``apply_risk_controls(weights, nav, date, config)``
  5. ``post_weights(weights, config)``

The Protocol expects a single ``compute_weights(date, panel, nav)``. This
adapter plays the role of a ``Strategy`` whose ``compute_weights``
runs steps 1-5 internally, with a per-adapter ``context`` dict and the
nav history injected as ``state.prev_weights / state.nav``.

Existing 195+ tests already exercise ``run_backtest``; this adapter
delegates 1:1 so the equivalence oracle keeps passing.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from strategy_research.core.backtest_models import BacktestResult
from strategy_research.core.utils.backtest_config import BacktestConfig
from strategy_research.core.utils.backtest_engine import BacktestCallbacks
from strategy_research.core.utils.backtest_utils import (
    annual_turnover_from_weights,
    calculate_turnover,
    generate_rebalance_dates,
)
from strategy_research.core.utils.metrics import extended_metrics
from .protocol import Strategy


class _CallbackStrategyAdapter(Strategy):
    """Adapter Strategy that drives a ``BacktestCallbacks`` 5-step flow.

    Each call to ``compute_weights`` runs the full pipeline for one
    rebalance date. The nav history is reconstructed from prior
    rebalance results held by the surrounding engine.
    """

    def __init__(
        self,
        callbacks: BacktestCallbacks,
        config: BacktestConfig,
        price_panel: pd.DataFrame,
        daily_returns: pd.DataFrame | None,
        context: dict,
    ):
        self._callbacks = callbacks
        self._config = config
        self._price_panel = price_panel
        self._daily_returns = daily_returns
        self._context = context
        # Per-run state the callbacks can read via ``state``.
        self._weights_history: list[tuple[pd.Timestamp, dict[str, float]]] = []

    def compute_weights(
        self,
        date: pd.Timestamp,
        price_panel: pd.DataFrame,
        nav_history: pd.Series,
    ) -> dict[str, float]:
        prev_weights = (
            self._weights_history[-1][1] if self._weights_history else {}
        )
        state = {
            "prev_weights": prev_weights,
            "nav": nav_history.values if nav_history is not None else np.array([]),
        }
        signals = self._callbacks.compute_signals(
            self._price_panel, date, state, self._context,
        )
        selected = self._callbacks.select_assets(signals, self._config)
        weights = self._callbacks.compute_weights(
            selected, self._price_panel, date, self._config,
        )
        weights = self._callbacks.apply_risk_controls(
            weights, nav_history, date, self._config,
        )
        weights = self._callbacks.post_weights(weights, self._config)
        self._weights_history.append((date, dict(weights)))
        return weights


class CallbackEngineAdapter:
    """Adapter that exposes the legacy callback engine as a
    ``BacktestEngine`` Protocol."""

    def __init__(self) -> None:
        # Adapter is stateless across runs; per-run state lives in
        # ``_CallbackStrategyAdapter``.
        pass

    def run(
        self,
        strategy: Any,
        price_panel: pd.DataFrame,
        *,
        config: Any | None = None,
    ) -> BacktestResult:
        # ``strategy`` here is the user-provided ``BacktestCallbacks``
        # instance. ``config`` is a ``BacktestConfig`` (or a duck-typed
        # bag exposing the same attributes).
        cfg = config or BacktestConfig()
        daily_returns = price_panel.pct_change(fill_method=None)
        context: dict = {}
        # Drive the legacy loop 1:1 — the same NAV / weights logic.
        dates = price_panel.index
        rebal_dates_list = generate_rebalance_dates(
            dates, cfg.rebal_freq, min_lookback=cfg.min_history,
        )
        rebal_set = set(rebal_dates_list)
        adapter = _CallbackStrategyAdapter(
            callbacks=strategy,
            config=cfg,
            price_panel=price_panel,
            daily_returns=daily_returns,
            context=context,
        )
        weights_history = adapter._weights_history
        prev_weights: dict[str, float] = {}
        nav_arr = np.ones(len(dates))

        for i, date in enumerate(dates):
            if date in rebal_set and i >= cfg.min_history:
                nav_s = pd.Series(nav_arr[:i + 1], index=dates[:i + 1])
                _ = adapter.compute_weights(date, price_panel, nav_s)
                # The legacy loop carries costs at the same point the
                # weights were applied; replicate that here so the
                # equivalence oracle holds.
                weights = weights_history[-1][1]
                if cfg.cost.enabled and len(weights_history) >= 2:
                    old_w = weights_history[-2][1]
                    new_w = weights
                    turnover = calculate_turnover(old_w, new_w)
                    cost = turnover * cfg.cost.cost_rate()
                    nav_arr[i] = nav_arr[i - 1] * (1 - cost) if i > 0 else 1.0
                elif i > 0:
                    nav_arr[i] = nav_arr[i - 1]
                prev_weights = weights
            else:
                if i > 0 and prev_weights:
                    daily_ret = 0.0
                    for code, w in prev_weights.items():
                        if code in daily_returns.columns:
                            ret = daily_returns.loc[date, code]
                            if pd.notna(ret):
                                daily_ret += w * ret
                    nav_arr[i] = nav_arr[i - 1] * (1 + daily_ret)
                else:
                    nav_arr[i] = 1.0 if i == 0 else nav_arr[i - 1]

        nav_daily = pd.Series(nav_arr, index=dates, name="nav")
        metrics = extended_metrics(nav_daily)
        metrics["ann_turnover"] = annual_turnover_from_weights(weights_history, dates)
        return BacktestResult(
            nav_daily=nav_daily,
            weights_history=weights_history,
            rebalance_dates=[d for d, _ in weights_history],
            metrics=metrics,
        )


__all__ = ["CallbackEngineAdapter"]
