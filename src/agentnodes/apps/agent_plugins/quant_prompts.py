"""量化域的角色提示词注册（L2 业务内容）。

**批次 4d 迁出**：8 个业务角色的提示词路由原本硬编码在
``kernel/agent/prompt/builder.py::PromptBuilderFactory._BUILDERS`` 里。

**为什么是业务**：这些角色的**提示词内容**含量化域名词——
``factor_analyst.md`` 讲 IC/IR，``risk_controller.md`` 讲 VaR/回撤，
``attribution_analyst.md`` 讲 Brinson 归因。换一个业务域就得改 kernel 的
路由表，那不是平台。

**保留在 kernel 的通用角色**（见 ``PromptBuilderFactory._BUILDERS``）：
``chat`` / ``researcher`` / ``critic`` / ``planner`` / ``evaluator`` /
``workflow_orchestrator`` / ``study_reviewer`` / ``study_collector`` ——
它们的提示词讲的是「怎么研究 / 怎么评审 / 怎么规划」这类过程，
不依赖具体领域。

**提示词文件仍住 ``templates/.prompts/``**（资产层，与代码分层正交）。
本模块只负责「哪个角色用哪份文件」的路由注册。
"""
from __future__ import annotations

#: 量化域专属角色 → 提示词文件名
QUANT_PROMPT_ROLES: dict[str, str] = {
    "data_quality": "data_quality.md",
    "factor_analyst": "factor_analyst.md",
    "strategist": "strategist.md",
    "portfolio_construction": "portfolio_construction.md",
    "risk_controller": "risk_controller.md",
    "attribution_analyst": "attribution_analyst.md",
    "anti_overfit_analyst": "anti_overfit_analyst.md",
    "backtest_diagnostics": "backtest_diagnostics.md",
}


def register_all() -> int:
    """把量化角色注册进 kernel 的 ``PromptBuilderFactory``。

    返回注册数量。幂等。

    **调用时机**：装配根（L1）在需要量化工作流时显式调用——
    apps → kernel 方向，kernel 不知道这些角色的存在。
    """
    from agentnodes.kernel.agent.prompt.builder import (
        PromptBuilderFactory,
        StaticFilePromptBuilder,
    )

    for role, filename in QUANT_PROMPT_ROLES.items():
        PromptBuilderFactory.register_prompt_builder(
            role, StaticFilePromptBuilder(filename[: -len(".md")])
        )
    return len(QUANT_PROMPT_ROLES)


__all__ = ["QUANT_PROMPT_ROLES", "register_all"]
