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

#: 构造后置钩子。L1 组装根（``interfaces/gateway_wiring.py``）在自身 import
#: 时注册一个 ``gw -> None`` 可调用，把 Ticket/容量闸/基因组接上去。
#:
#: **为什么用钩子而不是直接 import**：kernel 是 L3、interfaces 是 L1，
#: L3→L1 是上行 import（会破层门禁 H2）。方向必须是 L1 主动注册到 L3，
#: 与 ``CapabilityRegistry`` 同一思路（apps 主动注册，kernel 只解析）。
_hooks: list[Callable[[Any], None]] = []


def register_wiring_hook(hook: Callable[[Any], None]) -> None:
    """L1 组装根注册后置接线钩子（幂等：同一函数只登记一次）。"""
    if hook not in _hooks:
        _hooks.append(hook)


def _run_wiring_hooks(gw: Any) -> None:
    """构造后依次执行钩子。任一失败只记日志，不阻断启动。"""
    for hook in tuple(_hooks):
        try:
            hook(gw)
        except Exception:  # noqa: BLE001 —— 接线失败不得拒启
            import logging

            logging.getLogger(__name__).warning(
                "gateway wiring hook failed", exc_info=True,
            )


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
            # R5：ModelHub 是 provider/model 解析的唯一入口（解析序集中 +
            # 非法 provider 拒绝而非静默降级）。等价于原来的
            # OpenAICompatClient(LLMConfig.load())。
            from agentnodes.foundation.llm.model_hub import get_client

            try:
                client = get_client()
            except Exception as exc:  # noqa: BLE001 —— 解析失败给出可操作错误
                raise RuntimeError(
                    f"LLM provider/model 解析失败：{exc}。"
                    "请检查 ~/.quantnodes/llm.json 的 provider 是否为已注册值"
                    "（deepseek/kimi/minimax/nvidia/openai/qwen/siliconflow）。"
                ) from exc
        # P5-1b：默认装配内置工具链（shell 工具需显式 opt-in，见
        # build_default_registry 的注释）。无 workspace 时用只读工具集。
        if registry is None:
            try:
                from agentnodes.kernel.agent.builtin_tools import (
                    build_default_registry,
                )

                registry = build_default_registry()
            except Exception:  # noqa: BLE001 —— 工具链不可用则退化为纯 LLM
                registry = None
        # B5 R-1（选项 A）：组装 ContextHub 并注入 Gateway。此前生产 chat
        # 的 controller._context_hub 恒为 None——B1-b 的上下文注入只在旧
        # /send_async 路径活着。失败降级 None（与 registry 降级同款）。
        context_hub = _build_context_hub()
        ws = _resolve_workspace()
        gw = Gateway(
            client=client, system=system, sink=sink,
            registry=registry, stream=stream,
            context_hub=context_hub,
            context_policy=_build_context_policy(),
            # R4：PlatformStore + artifact 通道（P-3 登记）。
            # QNODES_PLATFORM_STORE=0 可关闭（灰度回退位）；构造失败降级 None。
            data_dir=_platform_data_dir(ws),
            workspace=ws,
        )
        # 阶段一/二/三：L1 组装根的后置接线（Ticket/Assignment、容量闸、
        # 基因组版本链）。**在 verify_invariants 之前**——自检要看到已接线
        # 的真实形态，否则自检的是个没接线的空壳。
        _run_wiring_hooks(gw)
        gw.verify_invariants()  # A5：不变量违反即拒启
        _singleton["gw"] = gw
    return gw


def _resolve_workspace() -> Any:
    """workspace 根（SR_WORKSPACE_PATH 或 cwd）——与 gateway._resolve_workspace 同源。"""
    import os
    from pathlib import Path

    env = os.environ.get("SR_WORKSPACE_PATH")
    return Path(env) if env else Path.cwd()


def _platform_data_dir(workspace: Any) -> Any | None:
    """P-3/P-1 持久化目录；None = 纯内存（现行为）。

    关闭开关：``QNODES_PLATFORM_STORE=0``（测试/排障）。目录创建失败也只
    降级 None——chat 不因数据面不可用而中断。
    """
    import os

    if os.environ.get("QNODES_PLATFORM_STORE", "").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return None
    try:
        data_dir = workspace / ".quantnodes" / "platform"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
    except Exception:  # noqa: BLE001 —— 数据面不可用不阻断 chat
        return None


def _build_context_hub() -> Any | None:
    """组装生产 chat 的 ContextHub（B5 R-1 + pre-run 收口）。

    两个 provider：
    - ``SessionScopedPreRunProvider``：goal/memory，复用 AgentLoop 那条
      已验证的 injector 链（react 路径没有 loop，用 3 属性 facade 驱动）。
    - ``TodoContextProvider``：todos 快照。

    任一不可用则整体降级 None（与 registry 降级同款）。
    """
    try:
        from agentnodes.kernel.agent.context_providers import (
            TodoContextProvider,
            build_prerun_provider_for_session,
        )
        from agentnodes.kernel.context.hub import ContextHub

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
        from agentnodes.kernel.context.policy import ContextPolicy

        return ContextPolicy()
    except Exception:  # noqa: BLE001
        return None


def reset_gateway() -> None:
    """测试用：清空单例（生产不调用）。"""
    _singleton.clear()
