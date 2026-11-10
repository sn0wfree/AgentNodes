"""Phase 6 P1 — chat-mode AgentLoop 构造工厂.

Unifies the 3 inline ``AgentLoop(...)`` constructors used by chat paths:

- ``api/routers/chat.py`` (web chat)
- ``cli/tui/session.py`` (TUI chat / goal mode)
- ``api/session/service.py`` (SessionService background runner)

Centralizes chat-mode defaults (``stream_mode=True``, ``max_iterations=1``,
``compact_config`` from ``cfg``, default ``registry``, goal/hypothesis
auto-create disabled) and exposes a single parameter surface so P2
(``allowed_tools`` unlock) and P3 (``{workspace}`` / ``{tool_list}``
rendering) can be applied uniformly.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from .loop import AgentLoop
from agentnodes.foundation import settings as _settings

logger = logging.getLogger(__name__)

#: **W5**：默认审批通道的进程级单例（审批是跨请求交互，必须全局唯一）。
_DEFAULT_PERMISSION_GATEWAY: Any | None = None


def build_default_permission_evaluator() -> Any:
    """构造 chat 场景的**默认**权限 evaluator（W5 提取，两路共享）。

    模式解析：显式参数 > ``AGENTNODES_PERMISSION_MODE`` env > 路径默认。
    无提问通道的默认是 **L2**（shell 自动、沙箱边界内、hard_deny 兜底）。

    **消费者**：
    - ``build_chat_agent_loop``（/send_async 路径，原内联逻辑提取）
    - ``gateway/singleton.get_gateway``（/send 路径，W5 之前恒为 None
      ——不设防；现在与 /send_async 同构）

    ``interactive=True``：ASK 判定在无 approval channel 时 fail-closed
    deny（``PermissionDeniedError``），与生产实测一致（冒烟 tool_help）。
    """
    from agentnodes.kernel.permission import PermissionEvaluator
    from agentnodes.kernel.permission.schema import (
        MODE_PRESETS,
        PermissionMode,
    )

    env_mode = _settings.get_str("AGENTNODES_PERMISSION_MODE")
    if env_mode:
        mode = PermissionMode.from_value(env_mode)
    else:
        mode = PermissionMode.L2_SANDBOX_EXEC
    return PermissionEvaluator(
        ruleset=list(MODE_PRESETS[mode.value]),
        interactive=True,
    )


def build_default_permission_gateway() -> Any:
    """构造 chat 场景的**默认**审批通道（W5）。

    **进程级单例**：审批是"一次请求 → 用户在 UI 上点 → 另一个请求回
    答"的跨请求交互——gateway 必须全局唯一，否则 ``tool_call_id`` 对不上
    挂起的 future（web 的 ``/api/chat/permission/respond`` 也按 id 查）。

    **无 SSE 钩子**：默认形态用于 CLI/TUI 等无审批 UI 的路径——此时 ASK
    仍 fail-closed（``tools.py`` 找不到 on_request 就等超时后拒绝），
    但通道本身在位，接上 UI 即可用。web 路由会传入自己那个带
    ``on_request`` 钩子的实例（``routers/chat._get_permission_gateway``）。
    """
    from agentnodes.kernel.permission import PermissionGateway

    global _DEFAULT_PERMISSION_GATEWAY
    if _DEFAULT_PERMISSION_GATEWAY is None:
        _DEFAULT_PERMISSION_GATEWAY = PermissionGateway()
    return _DEFAULT_PERMISSION_GATEWAY


def build_chat_agent_loop(
    config: Any,
    session_id: str,
    *,
    role: str = "chat",
    workspace: Path | None = None,
    allowed_tools: list[str] | None = None,
    max_iterations: int = 1,
    on_event: Callable | None = None,
    event_bus: Any | None = None,
    strategy_name: str | None = None,
    extra_context: dict[str, Any] | None = None,
    system_prompt_override: str | None = None,
    registry: Any = None,
    compact_config: Any | None = None,
    allow_shell_tools: bool = True,
    enable_claim_validation: bool = False,
    strict_claim_validation: bool = False,
    enable_goal_injection: bool = False,
    # ── Tier 1 A1: permission gate plumbing ─────────────────────
    permission_evaluator: Any | None = None,
    permission_gateway: Any | None = None,
) -> AgentLoop:
    """Construct a chat-mode ``AgentLoop``.

    Differences from ``role_factory.build_agent_loop``:

    - ``stream_mode`` forced to True (token-by-token streaming)
    - ``max_iterations`` defaults to 1 (single-pass chat, no ReAct loop)
    - ``compact_config`` taken from ``config.compact_config`` unless
      explicitly overridden
    - ``registry`` defaults to ``build_default_registry()`` (safe fallback
      to ``None`` if import fails)
    - ``enable_goal_injection`` (default ``False``): when True the loop
      injects <current-research-goal> context and forces continuation
      while required criteria are uncovered (long-horizon chat mode);
      ``enable_hypothesis_auto_create`` stays disabled
    - ``system_prompt`` is rendered via ``PromptBuilderFactory.get(role)``
      with ``extra_context`` (e.g. ``{"workspace": ..., "tool_list": ...}``)
      unless ``system_prompt_override`` is provided
    - ``allow_shell_tools`` (default ``False``): opt-in to include
      ``run_command`` in the registry. When ``False``, the shell tool is
      removed from the default registry before the agent is built so the
      LLM never sees it as an available option.

    P2 (``allowed_tools`` unlock):
        ``allowed_tools`` defaults to ``None`` (= all tools). Previously
        web chat passed ``[]`` (= no tools) — that hard lock is removed.

    P3 (workspace / tool_list rendering):
        When ``workspace`` is provided, it is included in the
        ``PromptBuilderFactory`` context so the ``{workspace}`` /
        ``{tool_list}`` placeholders in ``chat.md`` render with real values.
        When ``workspace`` is ``None``, ``{workspace}`` stays empty and
        ``{tool_list}`` still renders from the default registry (so the LLM
        sees what tools are available even without a workspace).
    """
    from agentnodes.kernel.agent.builtin_tools import build_default_registry
    from .loop_assembly import assemble_loop_parts

    # Tool registry (default: all built-in tools)
    if registry is None:
        try:
            registry = build_default_registry()
        except Exception:
            registry = None

    # Gate shell tools: remove from registry when not allowed
    if registry is not None and not allow_shell_tools:
        for shell_tool_name in ("shell",):
            registry._tools.pop(shell_tool_name, None)

    # DAG-orchestrator sessions (session_id prefix "dag:"): the LLM gets
    # exactly one tool — submit_dag_step — nothing else ("其他都不干").
    # It submits the full modified DAG each round; validation errors are
    # returned inside the tool result and the agent loop feeds them back
    # to the LLM for self-correction.
    if session_id.startswith("dag:"):
        from agentnodes.kernel.workflow.orchestrate_tool import SubmitDagStepTool

        if registry is not None:
            registry._tools.clear()
        else:
            from .tools import ToolRegistry
            registry = ToolRegistry()
        registry.register(SubmitDagStepTool())
        if allowed_tools is None:
            allowed_tools = ["submit_dag_step"]

    # System prompt + strategy resolution via the shared assembly core
    # (phase 3 — the registry/prompt/strategy trio was duplicated with
    # role_factory; see docs/design/phase3-dual-factory-design.md).
    assembly = assemble_loop_parts(
        role=role,
        registry=registry,
        extra_context=extra_context,
        system_prompt_override=system_prompt_override,
        workspace=workspace,
    )
    registry = assembly.registry
    system_prompt: str | None = assembly.system_prompt

    # Compact config: prefer explicit override, then config.compact_config
    if compact_config is None and config is not None:
        compact_config = getattr(config, "compact_config", None)

    loop = AgentLoop(
        config=config,
        registry=registry,
        workspace=workspace,
        on_event=on_event,
        stream_mode=True,  # chat mode: token-by-token streaming
        max_iterations=max_iterations,
        session_id=session_id,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,  # P2: default None (all tools)
        enable_goal_injection=enable_goal_injection,
        enable_hypothesis_auto_create=False,
        strategy_name=strategy_name,
        compact_config=compact_config,
        event_bus=event_bus,
        enable_claim_validation=enable_claim_validation,
        strict_claim_validation=strict_claim_validation,
    )
    # Tier 1 A1: stash the permission gate on the loop instance so
    # ToolContext can pick them up per tool call (see AgentLoop._*
    # hooks in loop.py). Defaults to None — sync / TUI paths can
    # opt out by simply not passing these kwargs.
    #
    # **批次 7（E2）**：未显式传 evaluator 时**默认接线**——shell 已进
    # registry，权限闸必须同时在岗（否则分级形同虚设）。模式解析：
    # 显式参数 > AGENTNODES_PERMISSION_MODE env > 路径默认。
    # CLI/TUI 无提问通道 → 默认 L2（批次 7 设计 §4）；
    # web 路由自行传 L1 evaluator + SSE gateway。
    if permission_evaluator is None and permission_gateway is None:
        # **W5 提取**：默认选型逻辑原内联于此——现在与 gateway /send 路径
        # 共享同一实现（两路同构，漂移即测试失败）。
        permission_evaluator = build_default_permission_evaluator()
        # **W5（2026-11-10）**：同时装配**审批通道**。
        #
        # 此前只有 evaluator、没有 gateway —— 于是任何 ASK 规则的工具在
        # 两条 chat 路径上都被 fail-closed 拒绝（tools.py:684 的
        # "no approval channel is wired on this path"），而
        # ``/api/chat/permission/respond`` 端点的 gateway 从没被工具执行
        # 用到过——审批 UI 是接上了、闸门那头却没插上。
        #
        # 这里装默认 gateway（无 on_request 钩子时 ASK 仍走 fail-closed，
        # 但**有**通道可用）；web 路由会传入带 SSE 推送钩子的进程级
        # gateway（``routers/chat._get_permission_gateway``）。
        permission_gateway = build_default_permission_gateway()
    # 防御式挂载：测试/子类可能传入不接受任意属性的 loop 替身。
    for _attr, _val in (
        ("_permission_evaluator", permission_evaluator),
        ("_permission_gateway", permission_gateway),
    ):
        if _val is None:
            continue
        try:
            setattr(loop, _attr, _val)
        except AttributeError:
            logger.debug(
                "loop %r rejects %s; permission gate not wired",
                type(loop).__name__, _attr,
            )
    return loop


__all__ = [
    "build_chat_agent_loop",
    "build_default_permission_evaluator",
    "build_default_permission_gateway",
]
