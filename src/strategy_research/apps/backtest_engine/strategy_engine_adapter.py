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

W10-3 (2026-11-04): ``__init__`` now forwards the four *constructor*
parameters (``vol_targeting`` / ``trend_filter`` / ``stop_loss`` /
``risk_free_rate``). They used to be unreachable through this adapter,
which is why ``config_runner.create_engine()`` bypassed the factory and
built ``StrategyEngine`` directly — the P0-3 Protocol shipped but the
production path never adopted it. With the constructor surface complete,
``create_engine()`` can return an adapter from ``get_engine()`` without
losing risk configuration.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from strategy_research.apps.backtest_models import BacktestResult
from strategy_research.foundation.utils.strategy_engine import StrategyEngine


class StrategyEngineAdapter:
    """Adapter that exposes ``utils.strategy_engine.StrategyEngine`` as
    a ``BacktestEngine`` Protocol."""

    def __init__(
        self,
        *,
        vol_targeting: Any | None = None,
        trend_filter: Any | None = None,
        stop_loss: Any | None = None,
        risk_free_rate: float = 0.0,
    ) -> None:
        # Forward exactly the kwargs the legacy engine accepts at
        # construction. Anything else is rejected by ``StrategyEngine``
        # itself, so this stays a 1:1 surface.
        self._engine = StrategyEngine(
            vol_targeting=vol_targeting,
            trend_filter=trend_filter,
            stop_loss=stop_loss,
            risk_free_rate=risk_free_rate,
        )

    @property
    def engine(self) -> StrategyEngine:
        """The wrapped engine (escape hatch for callers that need it)."""
        return self._engine

    # ── Risk configuration read-through ──────────────────────────
    # Existing callers reach for these on the object returned by
    # ``config_runner.create_engine()`` (test_config_runner_orchestration
    # asserts on ``eng.vol_targeting`` etc.). Delegating keeps that
    # contract while the object itself becomes the Protocol adapter.

    @property
    def vol_targeting(self) -> Any | None:
        return self._engine.vol_targeting

    @property
    def trend_filter(self) -> Any | None:
        return self._engine.trend_filter

    @property
    def stop_loss(self) -> Any | None:
        return self._engine.stop_loss

    @property
    def risk_free_rate(self) -> float:
        return self._engine.risk_free_rate

    def run(self, *args: Any, **kwargs: Any) -> BacktestResult:
        # Three calling styles coexist in this codebase; the parameter list is
        # deliberately ``*args/**kwargs`` so that none of them raises
        # "multiple values for argument".
        #
        # 1. Protocol — ``run(strategy, price_panel, config={...})``
        #    (``config_runner.run_from_yaml`` after W10-3).
        # 2. Legacy positional + keyword — ``run(panel, strategy=...,
        #    rebal_freq=...)`` (``test_config_runner_import``).
        # 3. Raw keyword passthrough — ``run(price_panel=..., strategy=...,
        #    rebal_freq=..., min_history=..., cost=...)`` (``run_from_yaml``
        #    and every stubbed engine in the tests).
        #
        # Order is disambiguated by type: a DataFrame can only be the price
        # panel, so if the first positional is a DataFrame the caller used
        # the legacy (panel-first) order.
        config = kwargs.pop("config", None)

        pos = list(args)
        strategy = kwargs.pop("strategy", None)
        panel = kwargs.pop("price_panel", None)

        if pos:
            first = pos.pop(0)
            if isinstance(first, pd.DataFrame):
                panel = first
            else:
                strategy = first
        if pos:
            second = pos.pop(0)
            # Whichever slot is still empty takes it; protocol order puts
            # the panel second, legacy puts nothing here.
            if panel is None:
                panel = second
            else:
                strategy = second
        if pos:
            raise TypeError(
                f"{type(self).__name__}.run() takes at most 2 positional "
                f"arguments, got {len(args)}"
            )

        if config is not None:
            for key in ("rebal_freq", "min_history", "cost"):
                if hasattr(config, key):
                    kwargs.setdefault(key, getattr(config, key))
                elif isinstance(config, dict) and key in config:
                    kwargs.setdefault(key, config[key])
        return self._engine.run(panel, strategy, **kwargs)


__all__ = ["StrategyEngineAdapter"]
