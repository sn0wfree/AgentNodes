"""Agent mode 注册表（批次 10 · B2）。

一个 mode = 一套检查点 deciders 的**命名配置**。B2 只提供注册表与
从 ``LoopStrategy`` 组装默认 deciders 的``deciders_for``；
``create_agent_loop`` 工厂随 B3 skeleton 落地（它要创建的对象 B3
才存在——现在出货只能是 raise 桩，即「有 API 无消费」病，批次 10
设计 §4 已记录该偏差）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .deciders import (
    BudgetDecider,
    CircuitBreakerDecider,
    Decider,
    GoalContinuationDecider,
    MaxIterationsDecider,
    NoProgressDecider,
    StopSignalDecider,
    TextFinalDecider,
)

__all__ = ["AgentModeConfig", "deciders_for", "get_mode_config", "list_modes", "register_mode"]


@dataclass
class AgentModeConfig:
    """一个 mode 的检查点配置。

    ``deciders`` 的键 = 骨架闸位（B3 定形）：``precheck``（上限/预算/
    熔断等进入 ACT 前的闸）、``after_reason``（REASON 后）、
    ``after_act``（ACT 后）。同闸位多个 decider 按序判定，
    首个非 ``PROCEED`` 生效。
    """

    name: str
    deciders: dict[str, list[Decider]] = field(default_factory=dict)
    description: str = ""


_MODE_REGISTRY: dict[str, AgentModeConfig] = {}


def register_mode(config: AgentModeConfig) -> None:
    """注册（或替换）一个 mode。幂等覆盖。"""
    _MODE_REGISTRY[config.name] = config


def get_mode_config(name: str) -> AgentModeConfig | None:
    return _MODE_REGISTRY.get(name)


def list_modes() -> list[str]:
    return sorted(_MODE_REGISTRY)


def deciders_for(strategy: Any, loop: Any, *, max_iterations: int) -> dict[str, list[Decider]]:
    """从 ``LoopStrategy`` 槽位组装**默认** deciders。

    槽位 → 闸位映射（批次 10 设计 §3，以现有循环的闸位实测为准）：

    ================  ========================  ==========================
    现有槽位/机制      闸位                       decider（包装对象）
    ================  ========================  ==========================
    stop (Budget)     after_reason + precheck   BudgetDecider
    （外部信号）        after_reason              StopSignalDecider
    （内联 :898）      after_reason              TextFinalDecider
    continuation      after_reason              GoalContinuationDecider
    resilience        pre_act（并入 precheck）   CircuitBreakerDecider
    progress          after_act                 NoProgressDecider
    （for-else）       precheck                  MaxIterationsDecider
    ================  ========================  ==========================

    :param strategy: ``LoopStrategy``（其 stop/continuation/resilience/
        progress 槽位被包装）
    :param loop: AgentLoop（steps 的 ``bind_agent_loop`` 对象——
        ProgressStep/ResilienceStep 的降级路径需要它）
    :param max_iterations: 迭代上限
    """
    # 确保槽位 step 拿到 loop（现有 bind 协议；重复绑定幂等）。
    binder = getattr(strategy, "bind_agent_loop", None)
    if binder is not None:
        binder(loop)
    for step_name in ("stop", "continuation", "resilience", "progress"):
        step = getattr(strategy, step_name, None)
        bind = getattr(step, "bind_agent_loop", None)
        if bind is not None:
            bind(loop)

    return {
        "precheck": [
            MaxIterationsDecider(max_iterations),
            BudgetDecider(strategy.stop),
            CircuitBreakerDecider(strategy.resilience),
        ],
        "after_reason": [
            StopSignalDecider(name="llm_error"),
            TextFinalDecider(),
            GoalContinuationDecider(strategy.continuation),
        ],
        "after_act": [
            NoProgressDecider(strategy.progress),
        ],
    }
