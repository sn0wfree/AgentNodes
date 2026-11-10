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
from agentnodes.foundation import settings as _settings

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
    # **W3**：默认 True——生产服务在启动时经 ``_start_gateway_consumer``
    # 构造单例，此后 chat 入口的 stream 参数是无效调用（单例已存在）。
    # 适配器对不支持流式的 client（测试假件）自动降级非流式，默认开是
    # 安全的；此前默认 False 让 gateway /send 从不打流式 delta。
    stream: bool = True,
    permission_evaluator: Any | None = None,
    permission_gateway: Any | None = None,
) -> Any:
    """返回进程内唯一 Gateway（首次调用时构造并自检不变量）。

    `client` 仅在首次构造时生效（后续调用忽略，避免半途换 client 造成
    session 状态混乱）。测试可用 `reset_gateway()` 清空。

    **W5**：``permission_evaluator`` 缺省时装配 chat 默认 evaluator
    （``build_default_permission_evaluator``，与 /send_async 同构）——
    此前 gateway /send 路径完全不设防（evaluator=None + shell 工具
    无条件注册），任何登录用户可经 /send 执行任意命令。
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
                # **W5**：shell / run_bg_command 按同一 env 闸 opt-in（与
                # /send_async 的 ``shell_tools_enabled`` 同判据）。此前
                # gateway 注册表无条件含这两个工具 + evaluator=None——
                # 不设防组合。
                _gate_shell_tools(registry)
            except Exception:  # noqa: BLE001 —— 工具链不可用则退化为纯 LLM
                registry = None
        if permission_evaluator is None:
            try:
                from agentnodes.kernel.agent.chat_loop import (
                    build_default_permission_evaluator,
                )

                permission_evaluator = build_default_permission_evaluator()
            except Exception:  # noqa: BLE001 —— evaluator 不可用不拒启
                # （registry 已被 _gate_shell_tools 收紧，降级安全）
                pass
        if permission_gateway is None:
            try:
                from agentnodes.kernel.agent.chat_loop import (
                    build_default_permission_gateway,
                )

                # **W5**：进程级默认审批通道（kernel 不 import L1 的
                # routers/chat——那是接口层；L1 装配根会把自己那个带 SSE
                # 钩子的实例传进来覆盖它）。
                permission_gateway = build_default_permission_gateway()
            except Exception:  # noqa: BLE001 —— 无通道时 ASK fail-closed
                pass
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
            # AGENTNODES_PLATFORM_STORE=0 可关闭（灰度回退位）；构造失败降级 None。
            data_dir=_platform_data_dir(ws),
            workspace=ws,
            # **W4**（V3 §8.2）：/send 此前恒为 controller 默认 1 轮——
            # 工具轮走不完（实跑抓到：写文件成功但无最终答复）。与
            # /send_async（chat.py 的 LLMConfig.max_iterations）取同一值。
            max_iterations=_resolve_max_iterations(),
            # **W5**：Tier 1 权限闸门（默认 = chat 默认 evaluator）。
            permission_evaluator=permission_evaluator,
            # **W5**：审批通道（ASK 判定需要它）。
            permission_gateway=permission_gateway,
        )
        # 阶段一/二/三：L1 组装根的后置接线（Ticket/Assignment、容量闸、
        # 基因组版本链）。**在 verify_invariants 之前**——自检要看到已接线
        # 的真实形态，否则自检的是个没接线的空壳。
        _run_wiring_hooks(gw)
        gw.verify_invariants()  # A5：不变量违反即拒启
        _singleton["gw"] = gw
    return gw


def _resolve_workspace() -> Any:
    """workspace 根（AGENTNODES_WORKSPACE 或 cwd）——与 gateway._resolve_workspace 同源。"""
    import os
    from pathlib import Path

    env = _settings.get_str("AGENTNODES_WORKSPACE")
    return Path(env) if env else Path.cwd()


def _resolve_max_iterations() -> int:
    """**W4**：gateway 的迭代上限与 /send_async 同源（``LLMConfig.max_iterations``，
    默认 50；plan 模式的单轮语义由 /send 路由按 payload 覆盖，见
    ``session_controller._execute`` 的 per-turn 分支）。装载失败回退 50
    ——与 chat.py:261 的回退值一致，配错不阻断对话。
    """
    try:
        from agentnodes.foundation.llm.config import LLMConfig

        return int(LLMConfig.load().max_iterations)
    except Exception:
        return 50


#: **W5**：受同一 opt-in 闸管控的高危工具（与 /send_async 的
#: ``allow_shell_tools`` 语义对齐）。registry 未注册时静默跳过。
_SHELL_GATED_TOOLS = ("shell", "run_bg_command")


def _gate_shell_tools(registry: Any) -> None:
    """按 ``AGENTNODES_ALLOW_SHELL_TOOLS`` 剔除 shell / run_bg_command。

    与 ``apps/chat/policy.shell_tools_enabled`` 同一判据（canonical 名）。
    **批 E-2**：旧名 ``AN_ALLOW_SHELL_TOOLS`` 已不再被读取。
    plan 模式的恒禁在
    gateway 路径由 evaluator 兜底（task/shell 的 ASK→deny）——这里只做
    **注册表级**的全局 opt-in（gateway 是进程级单例，没有 per-request
    注册表）。
    """
    enabled = _settings.get_str(
        "AGENTNODES_ALLOW_SHELL_TOOLS"
    ).strip().lower() in ("1", "true", "yes")
    if enabled:
        return
    tools = getattr(registry, "_tools", None)
    if not isinstance(tools, dict):
        return  # MagicMock / 鸭子类型替身——测试路径，无从剔除
    for name in _SHELL_GATED_TOOLS:
        tools.pop(name, None)


def _platform_data_dir(workspace: Any) -> Any | None:
    """P-3/P-1 持久化目录；None = 纯内存（现行为）。

    关闭开关：``AGENTNODES_PLATFORM_STORE=0``（测试/排障）。目录创建失败也只
    降级 None——chat 不因数据面不可用而中断。
    """
    import os

    if _settings.get_str("AGENTNODES_PLATFORM_STORE").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return None
    try:
        from agentnodes.foundation import paths as _paths

        data_dir = (
            workspace / _paths.active_profile().home_dir_name / "platform"
        )
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
