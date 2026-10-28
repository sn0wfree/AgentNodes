"""Role-scoped agent execution — the study/workflow/swarm agent factory.

``agent_node`` is the current execution path: it assembles the role's
prompt + tool whitelist + registry and runs a ``react`` node. It does not
construct an ``AgentLoop`` (C1 target: GraphEngine is the only runtime).

``build_agent_loop`` / ``run_agent_via_llm`` keep their signatures for
callers; ``run_agent_via_llm`` now delegates to ``agent_node`` and
serialises the result with the historical contract, so study and workflow
call sites need no change. See
docs/stage-c1-wp-agent-node-design-20261028.md.

Falls back to the deterministic stub when:
- `AUTORESEARCH_BEHAVIOR` env var is set (test / CI)
- No LLM API key is configured (LLMConfig has no api_key)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .loop import AgentLoop

import json
import os
from pathlib import Path
from typing import Any

# 9 角色对应的 templates/.prompts/<role>.md 文件名
_ROLE_PROMPT_FILES = {
    "researcher": "researcher.md",
    "data_quality": "data_quality.md",
    "factor_analyst": "factor_analyst.md",
    "strategist": "strategist.md",
    "portfolio_construction": "portfolio_construction.md",
    "risk_controller": "risk_controller.md",
    "attribution_analyst": "attribution_analyst.md",
    "anti_overfit_analyst": "anti_overfit_analyst.md",
    "backtest_diagnostics": "backtest_diagnostics.md",
    "critic": "critic.md",
    # Workflow-module roles (docs/workflow-module-design.md):
    # planner 生成研究计划子图; evaluator 评估进度并决策.
    "planner": "planner.md",
    "evaluator": "evaluator.md",
    # Study v2: inter-round review + knowledge collection (design §10-11)
    "study_reviewer": "study_reviewer.md",
    "study_collector": "study_collector.md",
}

# Role → tool whitelist now lives in core.agent.toolsets (A2).
# Kept as a thin alias for backwards compatibility.
from .toolsets import TOOLSETS as _ROLE_TOOL_WHITELIST
from .toolsets import resolve_toolset as _resolve_toolset


def _get_tool_whitelist(role: str) -> list[str]:
    """返回 role 专属工具白名单. 未知角色返回最小集 (只读)."""
    return _resolve_toolset(role) or list(_ROLE_TOOL_WHITELIST["minimal"])


def should_use_real_llm() -> bool:
    """判断是否走真 LLM 路径.

    走真 LLM 的条件:
    1. 未设置 AUTORESEARCH_BEHAVIOR (env var 仅用于强制 stub 模式)
    2. LLMConfig 至少有一个可用的 api_key

    Returns:
        True = 走 AgentLoop.run() 真调用
        False = 退到 stub (test / CI / 无 API key)
    """
    if os.environ.get("AUTORESEARCH_BEHAVIOR"):
        return False

    try:
        from ...core.llm import LLMConfig
        cfg = LLMConfig.load()
        # api_key 为空 / 占位 → 不走真 LLM
        if not cfg.api_key or cfg.api_key in ("", "your-api-key-here", "sk-placeholder"):
            return False
        return True
    except Exception:
        return False


def build_agent_loop(
    role: str,
    workspace_path: Path,
    strategy_name: str,
    *,
    llm_config: Any | None = None,
    session_manager: Any | None = None,
    max_iterations: int = 8,
    enable_claim_validation: bool = False,
    strict_claim_validation: bool = False,
    tools_override: list[str] | None = None,
    strategy_dir: Path | None = None,
    runs_dir: Path | None = None,
    results_tsv: Path | None = None,
    write_roots: tuple[str, ...] | None = None,
    read_roots: tuple[str, ...] | None = None,
    # P1-5+: accept a LoopStrategy spec (str / dict / LoopStrategy instance
    # / None). Resolved via resolve_loop_strategy(); None defaults to "react".
    loop_strategy: Any | None = None,
    # W-P: node-level knobs. AgentLoop has supported both since the swarm
    # parity work; they were simply never plumbed through this factory.
    iteration_timeout_s: float | None = None,
    on_event: Any | None = None,
) -> "AgentLoop | None":
    """为 role 构造 AgentLoop.

    **DEPRECATED (W-P, 2026-10-28)**: production no longer calls this.
    ``run_agent_via_llm`` now delegates to ``agent_node`` (react node), so
    this function has no caller outside tests. Kept because three tests use
    it to assert the role prompt + tool whitelist contract, which
    ``agent_node`` also honours -- those assertions would otherwise have to
    be rewritten against a private helper. Do **not** add new callers; the
    C1 target is that no study/workflow/swarm path constructs an AgentLoop.

    Args:
        role: 9 角色名 (researcher / strategist / ...)
        workspace_path: 工作区根目录
        strategy_name: 当前策略名 (用于 hypothesis auto-create)
        llm_config: 可选 LLMConfig; None 时自动 LLMConfig.load()
        session_manager: 跨角色共享 session (strategist 可看 researcher 输出)
        max_iterations: ReAct 最大迭代数
        enable_claim_validation: 开启 claim 验证 (truthfulness L2), 默认关
        strict_claim_validation: strict 模式, 未验证数字追加警告, 默认关
        tools_override: 覆盖角色默认工具白名单 (None=角色白名单).
        loop_strategy: P1-5+ — LoopStrategy spec (str / dict / LoopStrategy / None).
            None → "react" (default). Pass e.g. "explorer" or
            {"name": "validator", "config": {"max_iterations": 5}} to
            override the AgentLoop's loop strategy.

    Returns:
        AgentLoop 实例. 如果系统提示词为空, 返回 None (调用方走 stub fallback).
    """
    from ...core.agent.builtin_tools import build_default_registry
    from ...core.agent.loop import AgentLoop
    from ...core.agent.loop_assembly import assemble_loop_parts
    from ...core.agent.strategy.profile_resolver import resolve_loop_strategy
    from ...core.llm import LLMConfig

    # Single-source registry + prompt + strategy (phase 3, shared with
    # chat_loop — see docs/phase3-dual-factory-design.md).
    assembly = assemble_loop_parts(
        role=role,
        registry=None,
        extra_context={},
        workspace=None,
        loop_strategy=loop_strategy,
    )
    if not assembly.system_prompt:
        return None
    system_prompt = assembly.system_prompt

    cfg = llm_config or LLMConfig.load()
    registry = build_default_registry()
    whitelist = tools_override if tools_override is not None else _get_tool_whitelist(role)
    strategy = assembly.loop_strategy if assembly.loop_strategy is not None else resolve_loop_strategy(loop_strategy)

    return AgentLoop(
        config=cfg,
        registry=registry,
        workspace=workspace_path,
        max_iterations=max_iterations,
        system_prompt=system_prompt,
        allowed_tools=whitelist,
        session_manager=session_manager,
        strategy_name=strategy_name,
        strategy_dir=strategy_dir,
        runs_dir=runs_dir,
        results_tsv=results_tsv,
        write_roots=write_roots,
        auto_git_commit=False,  # git commit 由 autoresearch 主循环统一控制
        enable_claim_validation=enable_claim_validation,
        strict_claim_validation=strict_claim_validation,
        strategy=strategy,
        role=role,  # passed for the approval gate (event payload)
        iteration_timeout_s=iteration_timeout_s,
        on_event=on_event,
    )


def agent_node(
    role: str,
    workspace_path: Path,
    strategy_name: str,
    task: str,
    *,
    llm_config: Any | None = None,
    max_iterations: int = 8,
    tools_override: list[str] | None = None,
    loop_strategy: Any | None = None,
    iteration_timeout_s: float | None = None,
    sink: Any | None = None,
) -> dict:
    """Execute one agent turn as a GraphEngine-ready node.

    **Division of labour (option B, 2026-10-28)**: GraphEngine orchestrates
    (nodes / edges / interrupts / budget tree); a single agent turn is still
    executed by ``AgentLoop``. GraphEngine is the only *orchestration*
    runtime -- AgentLoop is the execution primitive beneath it, not a second
    control flow.

    Why not a thin react loop: ``loop_tools.py`` gives every AgentLoop turn
    four things the react node lacks, two of which are unconditional:

    ==========================  ==========================================
    tool result truncation      30k chars, loop_tools.py:214
    transient tool retry        x2, loop_tools.py:144-159
    monotonic deny guards       loop_tools.py:115
    read-only tool parallelism  loop_tools.py:276-332
    ==========================  ==========================================

    Dropping the first two silently bloats long-session context and raises
    transient failure rates, so the AgentLoop is kept as-is.

    Returns an AgentResult-shaped dict so callers (workflow / swarm presets)
    do not need to import ``AgentExecutionResult`` -- that type lives in
    ``core/agent/executor.py`` and must not be a dependency of this module.

    ``sink`` becomes the loop's ``on_event`` (same event vocabulary the
    engine consumes).
    """
    import time as _time

    t0 = _time.perf_counter()
    loop = build_agent_loop(
        role=role,
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        llm_config=llm_config,
        max_iterations=max_iterations,
        tools_override=tools_override,
        loop_strategy=loop_strategy,
        iteration_timeout_s=iteration_timeout_s,
        on_event=sink,
    )
    if loop is None:
        raise RuntimeError(f"无法构造 AgentLoop for role={role!r} (prompt 不存在)")

    try:
        result = loop.run(task)
    except Exception as exc:  # noqa: BLE001 — node contract is a dict, not a raise
        return _agent_result(
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            elapsed_s=_time.perf_counter() - t0,
        )

    return _agent_result(
        status="error" if result.error else "success",
        output=result.answer or "",
        error=result.error,
        iterations=result.iterations,
        tool_calls_made=result.tool_calls_made,
        elapsed_s=_time.perf_counter() - t0,
        metrics={
            "iterations": result.iterations,
            "finished_reason": result.finished_reason,
            **(result.metrics or {}),
        },
    )


def _agent_result(
    *,
    status: str,
    output: str = "",
    error: str | None = None,
    iterations: int = 0,
    tool_calls_made: int = 0,
    elapsed_s: float = 0.0,
    metrics: dict | None = None,
) -> dict:
    """AgentResult-shaped dict (fields mirror AgentExecutionResult)."""
    return {
        "status": status,
        "output": output,
        "error": error,
        "iterations": iterations,
        "tool_calls_made": tool_calls_made,
        "elapsed_s": elapsed_s,
        "summary": _first_two_sentences(output),
        "metrics": dict(metrics or {}),
        "artifacts": {},
    }


def _first_two_sentences(text: str) -> str:
    """Summary extraction (swarm parity; see executor.first_two_sentences).

    Duplicated rather than imported so this module does not depend on
    executor.py (W-W owns it).
    """
    import re

    text = (text or "").strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?。！？])\s+", text, maxsplit=2)
    if len(parts) >= 2:
        return " ".join(parts[:2]).strip()[:400]
    cjk = re.split(r"(?<=[.!?。！？])", text, maxsplit=2)
    if len(cjk) >= 2:
        return "".join(cjk[:2]).strip()[:400]
    return text[:400]


def run_agent_via_llm(
    role: str,
    workspace_path: Path,
    strategy_name: str,
    task: str,
    *,
    context: str | None = None,
    previous_outputs: list | None = None,
    llm_config: Any | None = None,
    session_manager: Any | None = None,
    max_iterations: int = 8,
    tools_override: list[str] | None = None,
    strategy_dir: Path | None = None,
    runs_dir: Path | None = None,
    results_tsv: Path | None = None,
    write_roots: tuple[str, ...] | None = None,
    read_roots: tuple[str, ...] | None = None,
    # P1-5+: forward loop_strategy spec to build_agent_loop → AgentLoop.
    loop_strategy: Any | None = None,
    # W-P: injectable LLM client. Production builds it from llm_config
    # (same as AgentLoop did); tests pass a fake so no network is touched.
    client: Any | None = None,
) -> str:
    """跑一次 role 任务，返回 JSON 字符串。

    **执行体：AgentLoop（选项 B 决策，2026-10-28）**。GraphEngine 是编排层
    （节点/边/中断/预算树），单个 agent turn 仍由 AgentLoop 执行——它是
    agent 执行原语，不是并行的第二套控制流。

    为什么不是薄 react 节点：核对 loop_tools.py 后发现 react 缺四项
    AgentLoop 无条件提供的能力（工具结果 30k 截断、瞬时错误重试 ×2、
    单调 deny guard、只读工具并行）。前两项是真回归，故保留 AgentLoop。

    Args:
        role: 9 角色名
        workspace_path: 工作区根目录
        strategy_name: 策略名
        task: 任务描述 (注入到 system prompt 后的第一条 user message)
        context: 可选上下文 (例如 previous_outputs 序列化的 markdown)
        previous_outputs: 上一步 agent 输出 (用于在 session 中共享)
        llm_config: LLM 配置
        session_manager: 跨调用 session 共享
        max_iterations: ReAct 迭代上限
        loop_strategy: P1-5+ — LoopStrategy spec (str / dict / LoopStrategy / None).

    Returns:
        AgentLoop.run() 输出的 answer 字段. 期望是合法 JSON 字符串.
        失败时返回 {"error", "iterations", "tool_calls_made"} JSON 字符串.

    Raises:
        RuntimeError: 如果构造 AgentLoop 失败 (例如 prompt 文件不存在).
    """
    loop = build_agent_loop(
        role=role,
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        llm_config=llm_config,
        session_manager=session_manager,
        max_iterations=max_iterations,
        tools_override=tools_override,
        strategy_dir=strategy_dir,
        runs_dir=runs_dir,
        results_tsv=results_tsv,
        write_roots=write_roots,
        read_roots=read_roots,
        loop_strategy=loop_strategy,
    )
    if loop is None:
        raise RuntimeError(f"无法构造 AgentLoop for role={role!r} (prompt 不存在)")

    # 构造完整任务文本: 上下文 + 之前 agent 输出 + 当前任务
    task_parts = []
    if context:
        task_parts.append(context)
    if previous_outputs:
        task_parts.append("## 之前 Agent 输出 (来自上一阶段)")
        for i, prev in enumerate(previous_outputs, 1):
            if isinstance(prev, dict):
                task_parts.append(f"### 第 {i} 阶段输出:\n```json\n{json.dumps(prev, ensure_ascii=False)}\n```")
            else:
                task_parts.append(f"### 第 {i} 阶段输出:\n```\n{prev}\n```")
    task_parts.append("## 当前任务\n" + task)
    full_task = "\n\n".join(task_parts)

    result = loop.run(full_task)
    if result.error:
        return json.dumps({
            "error": result.error,
            "iterations": result.iterations,
            "tool_calls_made": result.tool_calls_made,
        }, ensure_ascii=False)
    return result.answer


__all__ = [
    "build_agent_loop",
    "run_agent_via_llm",
    "should_use_real_llm",
]
