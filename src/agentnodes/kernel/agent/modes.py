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
    InjectorContinuationDecider,
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


def _make_approval_gate(loop: Any, strategy: Any) -> Any | None:
    """包装 ``loop._check_no_progress`` 为 ``(tool_hashes) -> bool``。

    **批次 12 S1（approval gate 收编）**：legacy 的 no-progress 交互
    （用户批准/拒绝/超时）由 ``_check_no_progress`` 承担——它写
    ``result.finished_reason``（user_rejected / approval_timeout_rejected）
    并发事件。decider 只翻译结论。

    需要 response/result/iteration 三个参数，从 loop 的当前运行状态取：
    - result ← ``ctx.result``（LoopPrepare 已挂上）
    - response ← ``ctx.response``（LoopReasoner 已回写）
    - iteration ← ``ctx.iteration``
    """
    if loop is None:
        return None
    check_fn = getattr(loop, "_check_no_progress", None)
    if check_fn is None:
        return None

    def _gate(tool_hashes: list[str]) -> bool:
        # decider 的 decide 拿到的 ctx 是骨架 state（LoopContext）
        # 但 gate 是闭包——需要当前 ctx。用 loop 上暂存的最近 ctx。
        ctx = getattr(loop, "_skeleton_ctx", None)
        if ctx is None:
            return True  # 无 ctx = 保守退出
        return bool(check_fn(
            tool_hashes,
            getattr(ctx, "response", None),
            getattr(ctx, "result", None),
            getattr(ctx, "iteration", 0),
            hashes_pre_recorded=True,
        ))

    return _gate


def deciders_for(strategy: Any, loop: Any, *, max_iterations: int) -> dict[str, list[Decider]]:
    """从 ``LoopStrategy`` 槽位组装**默认** deciders。

    槽位 → 闸位映射（批次 10 设计 §3，以现有循环的闸位实测为准）：

    ================  ========================  ==========================
    现有槽位/机制      闸位                       decider（包装对象）
    ================  ========================  ==========================
    stop (Budget)     after_reason + precheck   BudgetDecider
    continuation      after_reason              GoalContinuationDecider
    injectors>=100    after_reason              InjectorContinuationDecider
    （外部信号）        after_reason              StopSignalDecider
    （内联 :898）      after_reason              TextFinalDecider
    resilience        pre_act（并入 precheck）   CircuitBreakerDecider
    progress          after_act                 NoProgressDecider
    （for-else）       precheck                  MaxIterationsDecider
    ================  ========================  ==========================

    **批次 13（S3-a）顺序修正**：``after_reason`` 的 decider 顺序必须是
    「续跑类在前、终答判定在后」——与 legacy 一致（``loop.py:1070-1098``：
    先 continuation → 再 post-response injector → 最后 ``_handle_stop``）。
    修正前 ``TextFinalDecider`` 排第二，无 tool_calls 时直接 STOP，
    后面的 ``GoalContinuationDecider`` 是死代码——默认引擎因此丢掉了
    goal 长程续跑（实测：legacy 4 轮 / skeleton 1 轮）。

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
        # 顺序敏感：续跑判定（goal / injector）必须在文本终答之前——
        # 否则模型第一次给出文本就会结束整个 run。
        # BudgetDecider 同时在 precheck 与 after_reason：legacy 在每次
        # LLM 返回后立即判预算（loop.py:1056-1068），precheck 那份只是
        # 兜底（保证 t0 维度在下一轮开头也能拦住）。
        "after_reason": [
            BudgetDecider(strategy.stop),
            GoalContinuationDecider(strategy.continuation),
            InjectorContinuationDecider(),
            StopSignalDecider(name="llm_error"),
            TextFinalDecider(),
        ],
        "after_act": [
            NoProgressDecider(strategy.progress, approval_gate=_make_approval_gate(loop, strategy)),
        ],
    }
