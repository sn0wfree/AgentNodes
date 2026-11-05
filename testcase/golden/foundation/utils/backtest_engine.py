"""统一回测引擎 — BacktestCallbacks 模式。

复用自 QuantNodes/strategy/momentum_etf_rotation/common/backtest_engine.py。
精简版：移除 ETF 特定依赖，使用本地 metrics。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest_config import BacktestConfig
from .backtest_utils import (
    annual_turnover_from_weights,
    apply_max_weight,
    calculate_turnover,
    generate_rebalance_dates,
    normalize_weights,
)
from .metrics import extended_metrics


# ============================================================
# 1. 回调基类
# ============================================================
class BacktestCallbacks:
    """回调基类. 版本特定逻辑通过继承覆盖."""

    def compute_signals(
        self,
        price_panel: pd.DataFrame,
        date: pd.Timestamp,
        state: dict,
        context: dict,
    ) -> dict[str, float]:
        """计算信号分数 (调用日)."""
        raise NotImplementedError("Subclasses must implement compute_signals")

    def select_assets(
        self,
        signals: dict[str, float],
        config: BacktestConfig,
    ) -> list[str]:
        """选择资产 (调用日). 默认: 按分数降序取 top_n."""
        sorted_codes = sorted(signals, key=signals.get, reverse=True)
        return sorted_codes[:config.top_n]

    def compute_weights(
        self,
        selected: list[str],
        price_panel: pd.DataFrame,
        date: pd.Timestamp,
        config: BacktestConfig,
    ) -> dict[str, float]:
        """计算权重 (调用日). 默认: 等权."""
        n = len(selected)
        if n == 0:
            return {}
        return {c: 1.0 / n for c in selected}

    def apply_risk_controls(
        self,
        weights: dict[str, float],
        nav_history: pd.Series,
        date: pd.Timestamp,
        config: BacktestConfig,
    ) -> dict[str, float]:
        """应用风控 (调用日). 默认: 无操作."""
        return weights

    def post_weights(
        self,
        weights: dict[str, float],
        config: BacktestConfig,
    ) -> dict[str, float]:
        """权重后处理 (调用日). 默认: max_weight + normalize."""
        weights = apply_max_weight(weights, config.max_weight)
        return normalize_weights(weights)


# ============================================================
# 2. 结果容器
# ============================================================
# P0-2 Phase F: legacy BacktestResult is a type alias of the canonical
# dataclass. Keep the symbol so existing imports (``from
# .backtest_engine import BacktestResult``) continue to work; both
# fields are now uniformly available, including ``factor_failures``
# (default empty list when the callback-based engine doesn't populate it).
from agentnodes.foundation.utils.backtest_models import BacktestResult


# ============================================================
# 3. 统一回测引擎
# ============================================================
def run_backtest(
    price_panel: pd.DataFrame,
    daily_returns: pd.DataFrame | None = None,
    config: BacktestConfig | None = None,
    callbacks: BacktestCallbacks | None = None,
    context: dict | None = None,
) -> BacktestResult:
    """统一回测引擎.

    Parameters:
        price_panel: (T_daily, N) 价格面板
        daily_returns: (T_daily, N) 日频收益. None 则从 price_panel 自动计算
        config: 回测配置
        callbacks: 回调 (版本特定逻辑)
        context: 预计算数据

    Returns:
        BacktestResult (含日频 NAV)
    """
    context = context or {}
    config = config or BacktestConfig()
    callbacks = callbacks or BacktestCallbacks()
    dates = price_panel.index

    # 自动计算日收益
    if daily_returns is None:
        daily_returns = price_panel.pct_change(fill_method=None)

    # 1. 生成调仓日
    rebal_dates_list = generate_rebalance_dates(
        dates, config.rebal_freq, min_lookback=config.min_history
    )
    rebal_set = set(rebal_dates_list)

    # 2. 主循环
    weights_history: list[tuple[pd.Timestamp, dict[str, float]]] = []
    prev_weights: dict[str, float] = {}
    nav_arr = np.ones(len(dates))

    for i, date in enumerate(dates):
        if date in rebal_set and i >= config.min_history:
            # 信号
            state = {"prev_weights": prev_weights, "nav": nav_arr[:i + 1]}
            signals = callbacks.compute_signals(price_panel, date, state, context)

            # 选择
            selected = callbacks.select_assets(signals, config)

            # 权重
            weights = callbacks.compute_weights(selected, price_panel, date, config)

            # 风控
            nav_series = pd.Series(nav_arr[:i + 1], index=dates[:i + 1])
            weights = callbacks.apply_risk_controls(weights, nav_series, date, config)

            # 后处理
            weights = callbacks.post_weights(weights, config)

            # 记录
            weights_history.append((date, dict(weights)))
            prev_weights = weights

            # 调仓日: 扣成本, 不算日收益
            if config.cost.enabled and len(weights_history) >= 2:
                old_w = weights_history[-2][1]
                new_w = weights_history[-1][1]
                turnover = calculate_turnover(old_w, new_w)
                cost = turnover * config.cost.cost_rate()
                nav_arr[i] = nav_arr[i - 1] * (1 - cost) if i > 0 else 1.0
            elif i > 0:
                nav_arr[i] = nav_arr[i - 1]
        else:
            # 非调仓日: 累积日收益
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

    # 3. 构造日频 NAV Series
    nav_daily = pd.Series(nav_arr, index=dates, name="nav")

    # 4. 计算指标
    metrics = extended_metrics(nav_daily)
    metrics["ann_turnover"] = annual_turnover_from_weights(weights_history, dates)

    return BacktestResult(
        nav_daily=nav_daily,
        weights_history=weights_history,
        rebalance_dates=[d for d, _ in weights_history],
        metrics=metrics,
    )
