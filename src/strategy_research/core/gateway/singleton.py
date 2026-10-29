"""进程内 Gateway 单例（唯一构造点）。

为什么必须唯一（阶段 3a 轻量复查发现的问题）：
session=串行域由 mailbox 保证；若每个入口各持一个 Gateway 实例，
同一 session 经不同入口（API chat / scheduler / CLI）提交会落在
**不同 mailbox**，串行域被打破——两路 turn 并发跑。

因此所有入口一律 `get_gateway()`，共享同一个 EntryPoint 与控制器。
A2 会把这里升级为正式生命周期管理（start/stop、worker 自检、事件总线接线）。
"""
from __future__ import annotations

from typing import Any, Callable

_singleton: dict[str, Any] = {}


def get_gateway(
    *,
    client: Any | None = None,
    system: str | None = None,
    sink: Callable[[str, dict], None] | None = None,
    registry: Any | None = None,
    stream: bool = False,
) -> Any:
    """返回进程内唯一 Gateway（首次调用时构造并自检不变量）。

    `client` 仅在首次构造时生效（后续调用忽略，避免半途换 client 造成
    session 状态混乱）。测试可用 `reset_gateway()` 清空。
    """
    gw = _singleton.get("gw")
    if gw is None:
        from .gateway import Gateway

        if client is None:
            from strategy_research.core.llm import LLMConfig, OpenAICompatClient

            client = OpenAICompatClient(LLMConfig.load())
        # P5-1b：默认装配内置工具链（shell 工具需显式 opt-in，见
        # build_default_registry 的注释）。无 workspace 时用只读工具集。
        if registry is None:
            try:
                from strategy_research.core.agent.builtin_tools import (
                    build_default_registry,
                )

                registry = build_default_registry()
            except Exception:  # noqa: BLE001 —— 工具链不可用则退化为纯 LLM
                registry = None
        # B5 R-1（选项 A）：组装 ContextHub 并注入 Gateway。此前生产 chat
        # 的 controller._context_hub 恒为 None——B1-b 的上下文注入只在旧
        # /send_async 路径活着。失败降级 None（与 registry 降级同款）。
        context_hub = _build_context_hub()
        gw = Gateway(
            client=client, system=system, sink=sink,
            registry=registry, stream=stream,
            context_hub=context_hub,
            context_policy=_build_context_policy(),
        )
        gw.verify_invariants()  # A5：不变量违反即拒启
        _singleton["gw"] = gw
    return gw


def _build_context_hub() -> Any | None:
    """组装生产 chat 的 ContextHub（B5 R-1 + pre-run 收口）。

    两个 provider：
    - ``SessionScopedPreRunProvider``：goal/memory，复用 AgentLoop 那条
      已验证的 injector 链（react 路径没有 loop，用 3 属性 facade 驱动）。
    - ``TodoContextProvider``：todos 快照。

    任一不可用则整体降级 None（与 registry 降级同款）。
    """
    try:
        from ..agent.context_providers import (
            TodoContextProvider,
            build_prerun_provider_for_session,
        )
        from ..context.hub import ContextHub

        hub = ContextHub()
        hub.register(build_prerun_provider_for_session())
        hub.register(TodoContextProvider())
        return hub
    except Exception:  # noqa: BLE001 —— 上下文不可用不阻断 chat
        return None


def _build_context_policy() -> Any | None:
    """生产 chat 的仲裁策略（B5 W2）。

    默认秩表必须与现有渲染序一致（memory 先于 goal），否则仲裁一上线
    就改字节。见 core/context/policy.py 的 DEFAULT_KIND_RANK。
    """
    try:
        from ..context.policy import ContextPolicy

        return ContextPolicy()
    except Exception:  # noqa: BLE001
        return None


def reset_gateway() -> None:
    """测试用：清空单例（生产不调用）。"""
    _singleton.clear()
