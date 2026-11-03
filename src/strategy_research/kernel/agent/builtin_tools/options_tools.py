"""期权定价工具: options_pricing（Black-Scholes）。"""

from __future__ import annotations

import logging

from strategy_research.kernel.agent.tools import (
    BaseTool,
    ToolContext,
)
from .utils import err_actionable, tool_ok

logger = logging.getLogger(__name__)




# ── 11. OptionsPricingTool ──────────────────────────────────────────


class OptionsPricingTool(BaseTool):
    """Black-Scholes 期权定价与 Greeks。

    # ── 工具说明书 ──────────────────────────────────────────────
    # 版本: 1.1.0
    # 变更: v1.1.0 补全说明书 (v2 范式 8 节模板)
    #
    # ## 用途
    # 用 Black-Scholes 公式计算欧式期权理论价与 Greeks
    # (delta/gamma/theta/vega/rho), 用于研究中的敏感度分析。
    # 仅支持欧式期权, 不处理分红与美式提前行权。
    #
    # ## 参数
    # - spot/strike/rate/volatility/time_to_expiry: 标的价/行权价/
    #   无风险利率/波动率/剩余期限 (年), 均须为正
    # - option_type: call 或 put (默认 call)
    #
    # ## 示例
    # {"spot": 100.0, "strike": 105.0, "rate": 0.03, "volatility": 0.25,
    #  "time_to_expiry": 0.5, "option_type": "call"}
    #
    # ## 边界
    # 只读工具; 无需 workspace/数据库; 需 scipy; strict 工具 (schema
    # 由 strict 模式强制必填)。
    #
    # ## 错误处理范式
    # - option_type 非 call/put → error + 枚举提示, 修正后重试
    # - 任一参数非正 → error + 提示, 修正后重试
    # - 幂等: 纯函数计算
    #
    # ## 相关工具
    # pattern_recognition: 行情形态分析 (研究输入)
    # ─────────────────────────────────────────────────────────────
    """

    name = "options_pricing"
    description = "计算 Black-Scholes 期权价格与 Greeks (delta/gamma/theta/vega/rho)。"
    repeatable = True
    strict = True  # Simple shape — OpenAI strict mode applies cleanly
    category = "分析"

    def execute(
        self,
        ctx: ToolContext,
        spot: float,
        strike: float,
        rate: float,
        volatility: float,
        time_to_expiry: float,
        option_type: str = "call",
    ) -> str:
        spot = float(spot)
        strike = float(strike)
        rate = float(rate)
        vol = float(volatility)
        T = float(time_to_expiry)
        option_type = option_type.lower()

        if option_type not in ("call", "put"):
            return err_actionable("option_type must be 'call' or 'put'", tool="options_pricing")
        if T <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
            return err_actionable("spot, strike, volatility, and time_to_expiry must be positive", tool="options_pricing")

        from math import exp, log, sqrt

        from scipy.stats import norm

        d1 = (log(spot / strike) + (rate + 0.5 * vol**2) * T) / (vol * sqrt(T))
        d2 = d1 - vol * sqrt(T)

        if option_type == "call":
            price = spot * norm.cdf(d1) - strike * exp(-rate * T) * norm.cdf(d2)
            delta = float(norm.cdf(d1))
        else:
            price = strike * exp(-rate * T) * norm.cdf(-d2) - spot * norm.cdf(-d1)
            delta = float(norm.cdf(d1) - 1)

        gamma = float(norm.pdf(d1) / (spot * vol * sqrt(T)))
        theta = float(
            -(spot * norm.pdf(d1) * vol) / (2 * sqrt(T))
            - rate * strike * exp(-rate * T) * norm.cdf(d2 if option_type == "call" else -d2)
        )
        vega = float(spot * norm.pdf(d1) * sqrt(T) / 100)
        rho = float(
            strike * T * exp(-rate * T) * norm.cdf(d2 if option_type == "call" else -d2) / 100
        )

        return tool_ok({
            "option_type": option_type,
            "spot": spot,
            "strike": strike,
            "rate": rate,
            "volatility": vol,
            "time_to_expiry": T,
            "price": round(price, 4),
            "delta": round(delta, 4),
            "gamma": round(gamma, 4),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
            "rho": round(rho, 4),
        })
