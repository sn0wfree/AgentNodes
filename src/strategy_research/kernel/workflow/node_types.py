"""Node type registry and dispatch for modular DAG workflows.

Each node type is a "sub-agent module": an LLM agent with a role
prompt, or a special operation (planner/evaluator/python/tool).
All nodes produce the unified output envelope (AgentResult +
summary/artifacts/metrics).

Design: docs/workflow-module-design.md
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from strategy_research.core.agent.structured_output import get_parser
from strategy_research.kernel.swarm.runtime import AgentResult, AgentStatus

logger = logging.getLogger(__name__)

# ── Node metadata (consumed by the frontend palette) ───────────


@dataclass(frozen=True)
class NodeMeta:
    """Registry metadata describing a node type for the UI palette."""

    type: str
    label: str
    description: str
    config_schema: dict[str, Any] = field(default_factory=dict)


NODE_METADATA: dict[str, NodeMeta] = {
    "llm_agent": NodeMeta(
        "llm_agent", "子 Agent",
        "一个完整 chat 子 agent：指定角色提示词 + 工具白名单，执行一步研究任务",
        {"role": "str", "prompt_text": "str?", "tools": "list[str]?", "max_iterations": "int?"},
    ),
    "planner": NodeMeta(
        "planner", "生成计划",
        "把目标拆解为 3-8 步研究子图（planner 角色）",
        {"max_steps": "int (3-8)"},
    ),
    "evaluator": NodeMeta(
        "evaluator", "评估进度",
        "评估执行结果，决策 continue / replan / stop（evaluator 角色）",
        {},
    ),
    "approval": NodeMeta(
        "approval", "人工确认",
        "暂停执行等待用户审批（超时保持等待）；不调用 LLM",
        {"timeout": "float? (秒, null=永久)"},
    ),
    "python": NodeMeta(
        "python", "Python 函数",
        "调用已注册的 Python 函数（workspace_path/upstream 注入）",
        {"function": "str", "params": "dict?"},
    ),
    "tool": NodeMeta(
        "tool", "调用工具",
        "直接调用注册工具（run_backtest / check_data / ...）",
        {"tool": "str", "params": "dict?"},
    ),
}


# ── Executable function registry (python / tool nodes) ─────────


class NodeExecutors:
    """Registry for python-node functions and tool-node wrappers.

    Mirrors SwarmRuntime.register_python_executor semantics; kept
    separate so workflow execution never touches SwarmRuntime.
    """

    _fns: dict[str, Callable[..., Any]] = {}

    @classmethod
    def register(cls, name: str, fn: Callable[..., Any]) -> None:
        cls._fns[name] = fn

    @classmethod
    def get(cls, name: str) -> Callable[..., Any] | None:
        return cls._fns.get(name)

    @classmethod
    def reset(cls) -> None:
        cls._fns.clear()


def register_builtin_tool_executors() -> int:
    """Register tool-node wrappers for common builtin tools.

    Each wrapper calls the underlying BaseTool with a ToolContext
    (workspace injected).  Returns the number registered.
    """
    from strategy_research.core.agent.builtin_tools import build_default_registry

    registry = build_default_registry()
    count = 0
    for name in ("run_backtest", "get_market_data", "check_data", "clean_data",
                 "compute_factor", "factor_analysis", "search_symbol"):
        tool = registry.get(name)
        if tool is None:
            continue
        NodeExecutors.register(name, _make_tool_wrapper(tool, name))
        count += 1
    return count


def _make_tool_wrapper(tool: Any, name: str) -> Callable[..., Any]:
    """Wrap a BaseTool so node dispatch can invoke it with a ctx."""

    def _wrapper(**kwargs: Any) -> dict[str, Any]:
        from strategy_research.core.agent.tools import ToolContext

        params = dict(kwargs.get("params") or {})
        workspace = kwargs.get("workspace_path")
        ctx = ToolContext(
            workspace=Path(workspace) if workspace else None,
            session_id=kwargs.get("session_id"),
            emit_event=kwargs.get("emit_event"),
        )
        params["ctx"] = ctx
        output = tool.invoke(params)
        try:
            parsed = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            parsed = {"raw": output}
        return {"output": parsed, "tool": name}

    _wrapper.__name__ = f"tool_{name}"
    return _wrapper


# ── Dispatch ───────────────────────────────────────────────────


@dataclass
class NodeContext:
    """Runtime context for dispatching a single node."""

    workspace: Path
    strategy_name: str = ""
    objective: str = ""
    session_id: str | None = None
    upstream: dict[str, AgentResult] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)   # merged definition params
    llm_config: Any | None = None
    loop_factory: Callable[..., str] | None = None         # run_agent_via_llm-compatible
    emit_event: Callable[[str, dict], None] | None = None


def default_loop_factory():
    """Lazy import of run_agent_via_llm (keeps import graph light)."""
    from strategy_research.core.agent.role_factory import run_agent_via_llm
    return run_agent_via_llm


def dispatch_node(node: Any, ctx: NodeContext) -> AgentResult:
    """Execute a single node, returning the unified envelope.

    Raises NodeDispatchError for unrecoverable configuration errors
    (unknown type / missing executor / planner parse exhaustion).
    """
    t0 = time.perf_counter()
    handlers = {
        "llm_agent": _dispatch_llm_agent,
        "planner": _dispatch_planner,
        "evaluator": _dispatch_evaluator,
        "python": _dispatch_python,
        "tool": _dispatch_tool,
    }
    handler = handlers.get(node.type)
    if handler is None:
        raise NodeDispatchError(f"node '{node.id}': unsupported type '{node.type}'")
    return handler(node, ctx, t0)


class NodeDispatchError(RuntimeError):
    """Unrecoverable node execution error (config-level)."""


def _loop_kwargs(node: Any, ctx: NodeContext) -> dict[str, Any]:
    """Common kwargs for run_agent_via_llm-compatible factories.

    B3: ``exec.node_timeout_seconds`` / ``exec.node_max_retries`` were
    declared in WorkflowDefinition's defaults (definition.py:51) but never
    read anywhere. They are honoured here:

    - ``node_timeout_seconds`` becomes ``iteration_timeout_s`` on the loop.
      ``run_agent_via_llm`` does not accept it, so it is passed through
      ``ctx.params``-independent: an unsupported kwarg would raise, hence
      it is only forwarded when the factory tolerates it (see
      ``_dispatch_llm_agent``).
    - ``node_max_retries`` is applied by ``WorkflowRunner`` around
      ``_dispatch_node`` (it needs to see the returned envelope).
    """
    cfg = node.config or {}
    exec_cfg = ctx.params.get("exec", {}) or {}
    kwargs: dict[str, Any] = {
        "role": cfg.get("role", "researcher"),
        "workspace_path": ctx.workspace,
        "strategy_name": ctx.strategy_name or "default",
        "llm_config": ctx.llm_config,
        "max_iterations": cfg.get("max_iterations")
        or ctx.params.get("loop", {}).get("max_iterations", 8),
    }
    tools = cfg.get("tools")
    if tools:
        kwargs["tools_override"] = list(tools)
    timeout = cfg.get("timeout") or exec_cfg.get("node_timeout_seconds")
    if timeout:
        kwargs["iteration_timeout_s"] = float(timeout)
    return kwargs


def _envelope(
    node_id: str, status: AgentStatus, *, summary: str = "",
    artifacts: dict[str, Any] | None = None, metrics: dict[str, Any] | None = None,
    error: str | None = None, elapsed_s: float = 0.0,
) -> AgentResult:
    return AgentResult(
        agent_id=node_id,
        status=status,
        output=summary,
        error=error,
        elapsed_s=round(elapsed_s, 2),
        summary=summary,
        artifacts=dict(artifacts or {}),
        metrics=dict(metrics or {}),
    )


def _summary_truncate(text: str, ctx: NodeContext) -> str:
    max_chars = ctx.params.get("summary", {}).get("max_chars", 300)
    text = (text or "").strip()
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


# Node errors a retry cannot fix (config-level). Mirrors the classification
# AgentExecutor uses for agent-level retries (executor.py) so the two
# retry layers agree on what "permanent" means.
_PERMANENT_NODE_ERROR_MARKERS = (
    "prompt 不存在",
    "no prompt_file",
    "empty system prompt",
    "unknown node type",
    "unknown_executor",
    "is not registered",
)


def _retryable_node_error(error: str | None) -> bool:
    """False for node failures that repetition cannot fix."""
    if not error:
        return False
    return not any(m in str(error) for m in _PERMANENT_NODE_ERROR_MARKERS)


def _dispatch_llm_agent(node: Any, ctx: NodeContext, t0: float) -> AgentResult:
    factory = ctx.loop_factory or default_loop_factory()
    task = ctx.objective
    prompt_text = (node.config or {}).get("prompt_text")
    if prompt_text:
        task = f"{task}\n\n## 节点附加指令\n{prompt_text}"
    try:
        answer = factory(task=task, context=_upstream_summary(ctx), **{
            k: v for k, v in _loop_kwargs(node, ctx).items() if k != "role"
        } | {"role": (node.config or {}).get("role", "researcher")})
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_agent node %s failed: %s", node.id, exc)
        return _envelope(node.id, AgentStatus.ERROR, error=str(exc), elapsed_s=time.perf_counter() - t0)
    summary = _summary_truncate(answer, ctx)
    artifacts: dict[str, Any] = {}
    try:
        parsed = json.loads(answer)
        if isinstance(parsed, dict):
            artifacts["parsed"] = parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return _envelope(node.id, AgentStatus.SUCCESS, summary=summary,
                     artifacts=artifacts, elapsed_s=time.perf_counter() - t0)


def _upstream_summary(ctx: NodeContext) -> str:
    """Compact upstream context: summary + key artifacts per node."""
    parts: list[str] = []
    for node_id, result in ctx.upstream.items():
        line = f"- {node_id}: {result.summary or result.output or '(空)'}"
        artifacts = result.artifacts or {}
        if artifacts:
            try:
                line += f"\n  artifacts: {json.dumps(artifacts, ensure_ascii=False)[:200]}"
            except TypeError:
                pass
        parts.append(line)
    return "## 上游产出\n" + "\n".join(parts) if parts else ""


def _dispatch_planner(node: Any, ctx: NodeContext, t0: float) -> AgentResult:
    factory = ctx.loop_factory or default_loop_factory()
    max_steps = ctx.params.get("planner", {}).get("max_steps", 6)
    plan_text = _upstream_summary(ctx)
    if ctx.objective:
        plan_text = f"## 研究目标\n{ctx.objective}\n" + plan_text
    task = (
        f"生成研究计划（{max_steps} 步以内）。"
        f"{plan_text}"
    )
    try:
        answer = factory(
            role="planner", task=task, context="",
            workspace_path=ctx.workspace, strategy_name=ctx.strategy_name or "default",
            llm_config=ctx.llm_config,
            max_iterations=(node.config or {}).get("max_iterations") or 6,
        )
    except Exception as exc:  # noqa: BLE001
        return _envelope(node.id, AgentStatus.ERROR, error=f"planner loop failed: {exc}",
                         elapsed_s=time.perf_counter() - t0)
    plan = _parse_plan(answer)
    if plan is None:
        plan = _fallback_plan(max_steps)
        summary = "计划解析失败，使用 5 步标准流水线兜底"
    else:
        summary = f"计划生成：{len(plan)} 步"
    return _envelope(node.id, AgentStatus.SUCCESS, summary=summary,
                     artifacts={"plan": plan}, elapsed_s=time.perf_counter() - t0)


def _parse_plan(answer: str) -> list[dict[str, Any]] | None:
    """Parse planner JSON output into a validated step list."""
    result = get_parser().parse(answer)
    data = result.data if result else None
    if not isinstance(data, dict):
        return None
    raw_steps = data.get("plan") or data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return None
    steps: list[dict[str, Any]] = []
    for raw in raw_steps[:8]:
        if not isinstance(raw, dict):
            continue
        step = {
            "id": str(raw.get("id", "")),
            "title": str(raw.get("title", "")),
            "description": str(raw.get("description", "")),
            "type": str(raw.get("type", "llm_agent")),
            "tools": list(raw.get("tools") or []) if isinstance(raw.get("tools"), list) else [],
            "depends_on": list(raw.get("depends_on") or []) if isinstance(raw.get("depends_on"), list) else [],
        }
        if step["id"] and step["description"]:
            steps.append(step)
    if not steps:
        return None
    # Normalize depends_on: drop references to unknown ids
    known = {s["id"] for s in steps}
    for step in steps:
        step["depends_on"] = [d for d in step["depends_on"] if d in known and d != step["id"]]
    return steps


def _fallback_plan(max_steps: int) -> list[dict[str, Any]]:
    """5-step standard pipeline fallback (hypothesis→data→backtest→validate→report)."""
    steps = [
        ("step_001", "提出研究假设", "基于目标提出可检验的研究假设，明确验证指标。预期产出：假设列表。",
         ["read", "websearch", "factor_analysis"], []),
        ("step_002", "准备数据", "检查并准备所需行情/因子数据，确认覆盖与质量。预期产出：数据就绪说明。",
         ["get_market_data", "check_data"], ["step_001"]),
        ("step_003", "回测验证", "对假设构建策略并运行回测，产出绩效指标。预期产出：回测结果与指标。",
         ["run_backtest"], ["step_002"]),
        ("step_004", "稳健性检查", "检查回测结果稳健性（回撤/换手/参数敏感性）。预期产出：稳健性结论。",
         ["read", "drawdown_analysis"], ["step_003"]),
        ("step_005", "结论报告", "汇总发现，输出最终研究结论与建议。预期产出：完整研究报告。",
         ["show_chart", "show_report"], ["step_004"]),
    ]
    steps = steps[:max_steps]
    return [
        {"id": sid, "title": title, "description": desc, "type": "llm_agent",
         "tools": tools, "depends_on": deps}
        for sid, title, desc, tools, deps in steps
    ]


def _dispatch_evaluator(node: Any, ctx: NodeContext, t0: float) -> AgentResult:
    factory = ctx.loop_factory or default_loop_factory()
    summary = _upstream_summary(ctx)
    try:
        answer = factory(
            role="evaluator", task="评估当前研究进度并决策。", context=summary,
            workspace_path=ctx.workspace, strategy_name=ctx.strategy_name or "default",
            llm_config=ctx.llm_config,
            max_iterations=(node.config or {}).get("max_iterations") or 6,
        )
    except Exception as exc:  # noqa: BLE001
        return _envelope(node.id, AgentStatus.SUCCESS,
                         summary="评估器调用失败，规则层兜底：continue",
                         artifacts={"decision": {"verdict": "continue", "reason": str(exc)}},
                         elapsed_s=time.perf_counter() - t0)
    decision = _parse_decision(answer)
    if decision is None:
        decision = {"verdict": "continue", "reason": "评估输出无法解析，规则层兜底", "findings": []}
    verdict = decision.get("verdict")
    if verdict not in ("continue", "replan", "stop"):
        verdict = "continue"
        decision["verdict"] = verdict
    return _envelope(node.id, AgentStatus.SUCCESS,
                     summary=f"评估：{verdict}",
                     artifacts={"decision": decision},
                     elapsed_s=time.perf_counter() - t0)


def _parse_decision(answer: str) -> dict[str, Any] | None:
    result = get_parser().parse(answer)
    data = result.data if result else None
    if not isinstance(data, dict):
        return None
    return {
        "verdict": str(data.get("verdict", "continue")),
        "reason": str(data.get("reason", "")),
        "findings": data.get("findings") if isinstance(data.get("findings"), list) else [],
    }


def _dispatch_python(node: Any, ctx: NodeContext, t0: float) -> AgentResult:
    fn_name = (node.config or {}).get("function")
    fn = NodeExecutors.get(fn_name)
    if fn is None:
        raise NodeDispatchError(f"node '{node.id}': no python executor registered for '{fn_name}'")
    try:
        result = fn(
            workspace_path=ctx.workspace,
            strategy_name=ctx.strategy_name or "default",
            upstream={k: (v.output or v.summary) for k, v in ctx.upstream.items()},
            params=(node.config or {}).get("params") or {},
            session_id=ctx.session_id,
            emit_event=ctx.emit_event,
        )
    except Exception as exc:  # noqa: BLE001
        return _envelope(node.id, AgentStatus.ERROR, error=str(exc),
                         elapsed_s=time.perf_counter() - t0)
    if isinstance(result, dict):
        summary = str(result.get("summary") or result.get("output") or "")
        artifacts = {k: v for k, v in result.items() if k not in ("summary",)}
        return _envelope(node.id, AgentStatus.SUCCESS, summary=_summary_truncate(summary, ctx),
                         artifacts=artifacts, elapsed_s=time.perf_counter() - t0)
    return _envelope(node.id, AgentStatus.SUCCESS, summary=_summary_truncate(str(result), ctx),
                     elapsed_s=time.perf_counter() - t0)


def _dispatch_tool(node: Any, ctx: NodeContext, t0: float) -> AgentResult:
    tool_name = (node.config or {}).get("tool")
    fn = NodeExecutors.get(tool_name)
    if fn is None:
        raise NodeDispatchError(f"node '{node.id}': no tool executor registered for '{tool_name}'")
    try:
        result = fn(
            workspace_path=ctx.workspace,
            strategy_name=ctx.strategy_name or "default",
            params=(node.config or {}).get("params") or {},
            session_id=ctx.session_id,
            emit_event=ctx.emit_event,
        )
    except Exception as exc:  # noqa: BLE001
        return _envelope(node.id, AgentStatus.ERROR, error=str(exc),
                         elapsed_s=time.perf_counter() - t0)
    if isinstance(result, dict):
        inner = result.get("output")
        summary = str(inner.get("summary") or json.dumps(inner, ensure_ascii=False)[:200]
                      if isinstance(inner, dict) else inner)
        return _envelope(node.id, AgentStatus.SUCCESS, summary=_summary_truncate(summary, ctx),
                         artifacts={"result": inner}, elapsed_s=time.perf_counter() - t0)
    return _envelope(node.id, AgentStatus.SUCCESS, summary=_summary_truncate(str(result), ctx),
                     elapsed_s=time.perf_counter() - t0)


__all__ = [
    "NodeMeta", "NODE_METADATA", "NodeExecutors", "NodeContext",
    "NodeDispatchError", "dispatch_node", "register_builtin_tool_executors",
]
