"""回测工具: 换手成本, 调仓日生成, 权重约束, 日频 NAV 计算。

复用自 QuantNodes/strategy/momentum_etf_rotation/common/backtest_utils.py。
"""
from __future__ import annotations

from typing import cast

import pandas as pd

from .backtest_config import CostConfig


# ============================================================
# 1. 换手与成本
# ============================================================
def calculate_turnover(
    old_weights: dict[str, float],
    new_weights: dict[str, float],
) -> float:
    """计算单边换手率.

    turnover = 0.5 * Σ|new_w[c] - old_w[c]| for all c in union
    """
    all_codes = set(old_weights) | set(new_weights)
    return sum(
        abs(new_weights.get(c, 0.0) - old_weights.get(c, 0.0))
        for c in all_codes
    ) / 2.0


def calculate_turnover_cost(
    old_weights: dict[str, float],
    new_weights: dict[str, float],
    cost_cfg: CostConfig,
) -> float:
    """计算换手成本."""
    if not cost_cfg.enabled:
        return 0.0
    turnover = calculate_turnover(old_weights, new_weights)
    return turnover * cost_cfg.cost_rate()


def annual_turnover_from_weights(
    weights_history: list[tuple[pd.Timestamp, dict[str, float]]],
    dates: pd.DatetimeIndex,
) -> float:
    """从调仓权重历史计算年化单边换手率.

    年化换手 = 相邻调仓日单边换手均值 × 年调仓次数.
    少于 2 个调仓日 → 0.0.
    """
    if len(weights_history) < 2:
        return 0.0
    turnovers = [
        calculate_turnover(weights_history[i][1], weights_history[i + 1][1])
        for i in range(len(weights_history) - 1)
    ]
    avg_turnover = float(sum(turnovers) / len(turnovers))
    if len(dates) >= 2:
        years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    else:
        years = 1.0
    return round(avg_turnover * (len(weights_history) / years), 6)


# ============================================================
# 2. 调仓日生成
# ============================================================
def generate_rebalance_dates(
    dates: pd.DatetimeIndex,
    freq: str = "M",
    min_lookback: int | None = None,
) -> list[pd.Timestamp]:
    """从交易日索引生成调仓日."""
    if freq in ("M", "Q"):
        period = dates.to_period(freq)
        rebal = pd.Series(dates).groupby(period).max().tolist()
    elif freq.startswith("W"):
        rebal = dates.to_series().resample(freq).last().dropna().tolist()
        # 确保是实际交易日
        date_set = set(dates)
        rebal = [d for d in rebal if d in date_set]
    else:
        raise ValueError(f"Unsupported rebalance freq: {freq}")

    result = [pd.Timestamp(d) for d in rebal]

    if min_lookback is not None:
        valid = [d for d in result if dates.searchsorted(d) >= min_lookback]
        if not valid:
            raise ValueError(
                f"Insufficient data: need {min_lookback} periods, "
                f"got max index {dates.searchsorted(result[-1]) if result else 0}"
            )
        result = valid

    return cast(list[pd.Timestamp], result)


# ============================================================
# 3. 权重约束
# ============================================================
def apply_max_weight(
    weights: dict[str, float],
    max_w: float,
    max_iters: int = 50,
) -> dict[str, float]:
    """最大权重约束 (迭代法)."""
    w = dict(weights)
    for _ in range(max_iters):
        excess = {c: v - max_w for c, v in w.items() if v > max_w + 1e-10}
        if not excess:
            break
        total_excess = sum(excess.values())
        for c in excess:
            w[c] = max_w
        remaining = {c: v for c, v in w.items() if v < max_w - 1e-10}
        total_remaining = sum(remaining.values())
        if total_remaining > 0:
            for c in remaining:
                w[c] += total_excess * (w[c] / total_remaining)
    return w


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """权重归一化 (和为 1)."""
    total = sum(weights.values())
    if total <= 0:
        return weights
    return {c: v / total for c, v in weights.items()}


# ============================================================
# 4. 日频 NAV 计算
# ============================================================
def compute_daily_nav_from_weights(
    weights_history: list[tuple[pd.Timestamp, dict[str, float]]],
    daily_returns: pd.DataFrame,
    cost_cfg: CostConfig,
) -> pd.Series:
    """从权重历史 + 日频收益, 计算日频 NAV."""
    all_dates = daily_returns.index
    nav = pd.Series(1.0, index=all_dates, dtype=float)

    if not weights_history:
        return nav

    # 构建: 生效日期 → 权重
    date_to_weights: dict[pd.Timestamp, dict[str, float]] = {}
    for rebal_date, weights in weights_history:
        after = all_dates[all_dates > rebal_date]
        if len(after) > 0:
            date_to_weights[after[0]] = weights

    current_weights: dict[str, float] = {}

    for i in range(1, len(all_dates)):
        date = all_dates[i]

        # 检查是否有新权重生效
        cost = 0.0
        if date in date_to_weights:
            old_weights = current_weights
            current_weights = date_to_weights[date]
            cost = calculate_turnover_cost(old_weights, current_weights, cost_cfg)

        # 计算日收益
        daily_ret = 0.0
        for code, w in current_weights.items():
            if code in daily_returns.columns:
                ret = daily_returns.loc[date, code]
                if pd.notna(ret):
                    daily_ret += w * ret

        # 调仓日扣减成本
        daily_ret -= cost

        # 累积 NAV
        nav.iloc[i] = nav.iloc[i - 1] * (1 + daily_ret)

    return nav
