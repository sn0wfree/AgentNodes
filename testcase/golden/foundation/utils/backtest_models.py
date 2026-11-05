"""Backtest 数据模型 — 借鉴自 vibe-trading backtest/models.py

3 个 immutable dataclass：Position / TradeRecord / EquitySnapshot。

设计要点：
- `frozen=True`：禁止实例字段被修改，避免回测过程中数据被意外改动
- `direction: int` (1=多 / -1=空)：与 vibe-trading 保持一致
- `exit_reason` 限定为 signal / liquidation / end_of_backtest 三类
- 时间字段统一为 `pd.Timestamp`，便于跨模块算术
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Position:
    """未平仓头寸（多/空）。"""

    symbol: str
    direction: int  # 1=多, -1=空
    entry_price: float
    entry_time: pd.Timestamp
    size: float
    leverage: float = 1.0
    entry_bar_idx: int = 0
    entry_commission: float = 0.0


@dataclass(frozen=True)
class TradeRecord:
    """已完成 round-trip 交易记录。"""

    symbol: str
    direction: int
    entry_price: float
    exit_price: float
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    size: float
    leverage: float
    pnl: float
    pnl_pct: float
    exit_reason: str  # "signal" / "liquidation" / "end_of_backtest"
    holding_bars: int
    commission: float = 0.0


@dataclass(frozen=True)
class EquitySnapshot:
    """某 bar 的组合状态快照。"""

    timestamp: pd.Timestamp
    capital: float  # 自由现金
    unrealized: float  # 总浮动盈亏
    equity: float  # 总权益（含未实现）
    positions: int = 0


@dataclass
class BacktestResult:
    """Canonical backtest result (P0-2 Phase F, moved here in W4).

    All engines — callback-based, strategy-based, bar-by-bar — produce
    this shape. `factor_failures` is populated by `run_from_yaml`
    after the run ends; it carries `{factor, asset, error, occurrences}`
    entries for downstream dashboards and gates.
    """
    nav_daily: pd.Series
    weights_history: list[tuple[pd.Timestamp, dict[str, float]]] = field(
        default_factory=list,
    )
    rebalance_dates: list[pd.Timestamp] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    factor_failures: list = field(default_factory=list)


__all__ = [
    "BacktestResult",
    "EquitySnapshot",
    "Position",
    "TradeRecord",
]
