"""量化域的输出校验器（L2 业务内容）。

**批次 4d 迁出**：这 9 个校验器原本硬编码在
``kernel/workflow/validator.py`` 的 ``AgentValidator._get_validator``
路由表里。问题：kernel 里写死「researcher 输出该有 factor_direction」
「factor_analyst 输出该有 ic_mean」这类**只有量化域才懂**的字段规则。

现在 kernel 只留机制（``AgentValidator`` 的注册表 + 通用基础检查），
业务规则在本模块，由装配根调用 ``register_all()`` 注册。

**为什么是「注册」而不是「继承」**：继承会把业务规则绑死在 kernel 的类上；
注册保持 L3 不认识 L2 的规则内容，只认识「有个函数签了名」。
"""
from __future__ import annotations

from typing import Any

#: 本模块注册的 agent 名（与 ``quant.py`` 的插件 id 对应）。
QUANT_VALIDATED_AGENTS: tuple[str, ...] = (
    "researcher",
    "factor_analyst",
    "strategist",
    "risk_controller",
    "anti_overfit_analyst",
    "data_quality",
    "portfolio_construction",
    "attribution_analyst",
    "backtest_diagnostics",
)


def _validate_researcher(output: dict, result: Any) -> None:
    action = output.get("action", "")
    valid_actions = {
        "tweak_factor",
        "add_factor",
        "remove_factor",
        "adjust_weights",
        "keep_current",
    }
    if action and action not in valid_actions:
        result.add_error(f"Invalid action: {action}")

    direction = output.get("factor_direction", "")
    if direction and direction not in {"positive", "negative"}:
        result.add_error(f"Invalid factor_direction: {direction}")


def _validate_factor_analyst(output: dict, result: Any) -> None:
    ic = output.get("ic_mean")
    if ic is not None and not -1 <= ic <= 1:
        result.add_error(f"IC mean out of range: {ic}")

    ir = output.get("ir_mean")
    if ir is not None and abs(ir) > 10:
        result.add_warning(f"IR mean unusually high: {ir}")


def _validate_strategist(output: dict, result: Any) -> None:
    if not output.get("changes", {}):
        result.add_warning("No strategy changes proposed")


def _validate_risk_controller(output: dict, result: Any) -> None:
    verdict = output.get("verdict", "")
    if verdict and verdict not in {"pass", "fail", "warn"}:
        result.add_error(f"Invalid verdict: {verdict}")

    max_dd = output.get("max_drawdown")
    if max_dd is not None:
        if max_dd > 0:
            result.add_error("max_drawdown should be negative")
        if max_dd < -0.5:
            result.add_warning(f"max_drawdown very high: {max_dd}")


def _validate_anti_overfit(output: dict, result: Any) -> None:
    passed = output.get("methods_passed", 0)
    total = output.get("methods_total", 0)
    if total > 0 and passed > total:
        result.add_error("methods_passed cannot exceed methods_total")


def _validate_data_quality(output: dict, result: Any) -> None:
    completeness = output.get("completeness")
    if completeness is not None and not 0 <= completeness <= 1:
        result.add_error(f"completeness out of range: {completeness}")


def _validate_portfolio(output: dict, result: Any) -> None:
    weights = output.get("weights", {})
    if weights:
        total = sum(weights.values())
        if abs(total - 1.0) > 0.01:
            result.add_warning(f"Weights sum to {total}, expected ~1.0")


def _validate_attribution(output: dict, result: Any) -> None:
    if not output.get("sources", {}):
        result.add_warning("No attribution sources provided")


def _validate_backtest(output: dict, result: Any) -> None:
    metrics = output.get("metrics", {})
    for key in ("sharpe", "calmar", "max_dd"):
        if key not in metrics:
            result.add_warning(f"Missing metric: {key}")


#: agent 名 → 校验函数
VALIDATORS: dict[str, Any] = {
    "researcher": _validate_researcher,
    "factor_analyst": _validate_factor_analyst,
    "strategist": _validate_strategist,
    "risk_controller": _validate_risk_controller,
    "anti_overfit_analyst": _validate_anti_overfit,
    "data_quality": _validate_data_quality,
    "portfolio_construction": _validate_portfolio,
    "attribution_analyst": _validate_attribution,
    "backtest_diagnostics": _validate_backtest,
}


def register_all() -> int:
    """把本模块的校验器注册进 kernel 的 ``AgentValidator``。

    返回注册数量。幂等（重复调用只是覆盖）。

    **调用时机**：装配根（L1）在需要量化工作流时显式调用——
    与 ``apps.agent_plugins.populate()`` 同一模式（apps → kernel 方向，
    kernel 不知道这些规则的存在）。
    """
    from agentnodes.kernel.workflow.validator import AgentValidator

    for agent_name, fn in VALIDATORS.items():
        AgentValidator.register_output_validator(agent_name, fn)
    return len(VALIDATORS)


__all__ = ["QUANT_VALIDATED_AGENTS", "VALIDATORS", "register_all"]
