"""Portfolio data models — 组合回测数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class StrategyContribution:
    """单个策略在组合中的贡献。"""

    name: str
    weight: float
    sharpe: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    trade_count: int = 0


@dataclass(frozen=True)
class CorrelationPair:
    """两个策略之间的相关性。"""

    strategy_a: str
    strategy_b: str
    correlation: float
    period: int = 0  # 重叠天数


@dataclass
class PortfolioConfig:
    """组合配置。"""

    name: str
    strategies: List[str]
    combine: str = "equal_weight"  # equal_weight | risk_parity | sharpe_weight
    rebalance_freq: str = "monthly"
    initial_cash: float = 1_000_000.0


@dataclass
class PortfolioMetrics:
    """组合级指标。"""

    sharpe: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0
    turnover: float = 0.0
    diversification_ratio: float = 0.0
    component_correlation: float = 0.0  # 平均两两相关性
    n_strategies: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sharpe": round(self.sharpe, 4),
            "annual_return": round(self.annual_return, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "total_return": round(self.total_return, 6),
            "var_95": round(self.var_95, 6),
            "cvar_95": round(self.cvar_95, 6),
            "turnover": round(self.turnover, 4),
            "diversification_ratio": round(self.diversification_ratio, 4),
            "component_correlation": round(self.component_correlation, 4),
            "n_strategies": self.n_strategies,
        }


__all__ = [
    "StrategyContribution",
    "CorrelationPair",
    "PortfolioConfig",
    "PortfolioMetrics",
]
