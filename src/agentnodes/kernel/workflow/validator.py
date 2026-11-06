from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ValidationResult:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


class AgentValidator:
    """输出校验器：**通用机制** + 可插拔的业务规则。

    **批次 4d 改造**（原为 9 个量化 agent 的硬编码路由表）：

    kernel 只提供**机制**——空输出检查、基础字段检查、规则查找、错误聚合。
    「哪个 agent 的输出该校验什么字段」是**业务内容**，由 L2 在装配期
    通过 ``register_output_validator()`` 注册。

    **默认无业务规则**（与 ``AgentPluginRegistry`` 同口径）：没有注册时
    ``validate()`` 只做通用检查（空输出 + action/hypothesis 基础字段），
    这正是一个通用平台该有的行为——它不知道"因子分析"该有哪些字段。

    原 9 个业务校验器（researcher / factor_analyst / strategist /
    risk_controller / anti_overfit_analyst / data_quality /
    portfolio_construction / attribution_analyst / backtest_diagnostics）
    已迁 ``apps/agent_plugins/quant_validators.py``。
    """

    #: 类级注册表：agent 名 → 校验函数。L2 通过
    #: ``register_output_validator()`` 填充。
    _registry: dict[str, Callable[[dict, "ValidationResult"], None]] = {}

    @classmethod
    def register_output_validator(
        cls, agent_name: str, fn: Callable[[dict, "ValidationResult"], None]
    ) -> None:
        """注册（或替换）某 agent 的输出校验器。

        **幂等**：重复注册同一 agent 会覆盖，不报错——插件重载/测试
        重置都会走到这里。
        """
        cls._registry[str(agent_name)] = fn

    @classmethod
    def clear_output_validators(cls) -> None:
        """清空业务校验器（测试用）。"""
        cls._registry.clear()

    @classmethod
    def registered_agents(cls) -> list[str]:
        """已注册校验器的 agent 名（排序）。"""
        return sorted(cls._registry)

    def validate(self, agent_name: str, output: dict) -> ValidationResult:
        result = ValidationResult()

        if not output:
            result.add_error("Empty output")
            return result

        self._validate_base_fields(output, result)

        validator = self._get_validator(agent_name)
        if validator:
            validator(output, result)

        return result

    def _validate_base_fields(self, output: dict, result: ValidationResult) -> None:
        """**领域无关**的基础字段检查。

        只检查两个几乎所有结构化输出都会有的字段。业务字段
        （``ic_mean`` / ``sharpe`` / ``weights`` ...）不在这里。
        """
        if "action" not in output:
            result.add_warning("Missing 'action' field")

        if "hypothesis" not in output:
            result.add_warning("Missing 'hypothesis' field")

    def _get_validator(self, agent_name: str):
        """查业务校验器；未注册 = 不校验（返回 None 是合法状态）。"""
        return self._registry.get(agent_name)
