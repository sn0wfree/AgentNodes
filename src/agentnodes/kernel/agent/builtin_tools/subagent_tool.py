"""SubAgentTool — delegate focused subtasks to a lightweight sub-agent.

Constraints (enforced):
    1. Sub-agents cannot call SubAgentTool (no nested delegation).
    2. Parent agent may spawn at most MAX_SUBAGENTS per turn.
       Excess calls return an actionable error.

The sub-agent runs via the unified AgentExecutor (AgentLoop) and returns
the answer as a tool result to the parent agent.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from agentnodes.foundation.llm.config import LLMConfig
from agentnodes.kernel.agent.tools import EFFECT_FS, BaseTool, ToolRegistry

logger = logging.getLogger(__name__)

MAX_SUBAGENTS = 5


def _forward_event(
    emit_event: Any,
    agent_id: str,
    message_id: str | None,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Forward a child agent event as a subagent_* SSE event.

    ``subagent_started`` / ``subagent_completed`` / ``subagent_failed`` are
    passed through as-is; child events (tool_call / tool_result / text_delta)
    are namespaced with the ``subagent_`` prefix.
    """
    if emit_event is None:
        return
    mapped = event_type
    if event_type in ("tool_call", "tool_result", "text_delta"):
        mapped = f"subagent_{event_type}"
    emit_event(mapped, {
        "agent_id": agent_id,
        "message_id": message_id,
        **data,
    })


class SubAgentTool(BaseTool):
    """委派子任务给专注的子 agent，获取结果后继续。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.0.0
    # 变更: v1.0.0 新增 (SubAgentTool 委派机制)
    #
    # ## 用途
    # 将独立子任务委派给子 agent。子 agent 运行一个轻量的 ReAct 循环
    # (AgentExecutor → AgentLoop), 拥有独立工具白名单（不含 delegate_to_agent），
    # 可读写文件、跑回测、调用因子分析等工具。结果作为工具返回值交回父 agent。
    #
    # ## 参数
    # - task: 子任务详细描述 (必填) — 包含目标、约束、期望输出格式
    # - tools: 可选工具白名单 (数组) — 限制子 agent 可用工具
    # - max_iterations: 子 agent 最大推理轮次 (默认 10, 上限 20)
    #
    # ## 示例
    # {"task": "分析 momentum_20_60 因子的 IC 衰减, 输出摘要报告"}
    #
    # ## 边界
    # - 子 agent 不能再次调用 delegate_to_agent (禁止嵌套委派)
    # - 父 agent 单次执行最多委派 MAX_SUBAGENTS 个 (默认 5)
    # - 超过上限拒绝执行, 提示使用工作流路径 (/study)
    #
    # ## 错误处理范式
    # - 缺 task → error + 提示必填参数
    # - 超委派上限 → error + 提示切换工作流执行
    # - 子 agent 失败 → error + 透传子 agent 错误信息
    #
    # ## 相关工具
    # 子 agent 内部可用: read_file, write_file, run_backtest, compute_factor 等
    # ─────────────────────────────────────────────
    """

    name = "task"
    category = "agent"
    # Sub-agents may write files / run backtests → treated as a write tool
    # (serial execution; avoids count-ref races in parallel dispatch).
    effects = frozenset({EFFECT_FS})
    description = (
        "委派一个独立的子任务给专注的子 agent。子 agent 拥有独立的推理循环，"
        "可读写文件、运行回测、调用因子分析等工具。适合需要独立思考或多步操作"
        "的子任务。结果会作为文本返回。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "子任务的详细描述。应包含足够的上下文让子 agent 独立完成工作，"
                    "例如目标、约束条件、期望的输出格式等。"
                ),
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "可选的工具白名单。如果指定，子 agent 只能使用这些工具。"
                    "不指定则使用全部可用工具（不含 delegate_to_agent）。"
                ),
            },
            "toolset": {
                "type": "string",
                "description": (
                    "命名工具集（A6）：minimal/chat/researcher/factor_analyst/"
                    "strategist/critic 等。与 tools 二选一，tools 优先。"
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": "子 agent 最大推理轮次（默认 10，最大 20）。",
                "default": 10,
            },
            "budget_max_tokens": {
                "type": "integer",
                "description": "子任务 token 预算（继承/限制父运行，默认不限）。",
            },
            "budget_max_seconds": {
                "type": "number",
                "description": "子任务时间预算（秒）。",
            },
        },
        "required": ["task"],
    }

    def _build_child_registry(
        self,
        parent_registry: ToolRegistry | None,
        tools_whitelist: list[str] | None,
    ) -> ToolRegistry:
        """Build the sub-agent's tool registry.

        Excludes ``delegate_to_agent`` (no nested delegation) and applies
        the optional whitelist on top.
        """
        from agentnodes.kernel.agent.tools import ToolRegistry as TR

        filtered = TR()
        if parent_registry is not None:
            for t_name, t_obj in parent_registry._tools.items():
                if t_name != "task":
                    filtered.register(t_obj)
        if tools_whitelist:
            wl_registry = TR()
            for t_name in tools_whitelist:
                t = filtered.get(t_name)
                if t is not None:
                    wl_registry.register(t)
            filtered = wl_registry
        return filtered

    def execute(self, **kwargs: Any) -> str:
        """Execute delegation — runs AgentExecutor in a thread."""
        # Extract params
        task = kwargs.get("task", "")
        if not task:
            return json.dumps(
                {"status": "error", "error": "missing required parameter 'task'"},
                ensure_ascii=False,
            )

        tools_whitelist: list[str] | None = kwargs.get("tools")
        toolset_name: str | None = kwargs.get("toolset")
        if tools_whitelist is None and toolset_name:
            from agentnodes.kernel.agent.toolsets import resolve_toolset

            tools_whitelist = resolve_toolset(toolset_name)
        max_iterations = min(kwargs.get("max_iterations", 10), 20)
        budget_max_tokens = kwargs.get("budget_max_tokens")
        budget_max_seconds = kwargs.get("budget_max_seconds")

        # Emit event helpers
        emit_event = kwargs.get("emit_event")
        message_id = kwargs.get("message_id")
        workspace = kwargs.get("workspace")
        session_id = kwargs.get("session_id")
        subagent_count_ref = kwargs.get("_subagent_count_ref")

        # ── Count limit ────────────────────────────────────────────
        if subagent_count_ref is not None:
            current = subagent_count_ref[0]
            if current >= MAX_SUBAGENTS:
                return json.dumps(
                    {
                        "status": "error",
                        "error": (
                            f"单次最多委派 {MAX_SUBAGENTS} 个子任务（已达上限）。"
                            "更多任务请使用工作流执行（/study 命令），"
                            "或减少委派数量。"
                        ),
                    },
                    ensure_ascii=False,
                )
            subagent_count_ref[0] = current + 1

        agent_id = f"sub-{uuid.uuid4().hex[:8]}"

        # ── Emit subagent_started ──────────────────────────────────
        _forward_event(emit_event, agent_id, message_id, "subagent_started", {
            "agent_id": agent_id,
            "name": task[:80],
            "message_id": message_id,
        })

        # ── Build child agent via unified AgentExecutor ──────────
        try:
            from agentnodes.kernel.agent.dag_config import AgentNodeConfig
            from agentnodes.kernel.agent.executor import AgentExecutor
            from agentnodes.kernel.agent.plugin import AgentPlugin
            from agentnodes.kernel.agent.registry import get_default_registry

            # Filtered registry (exclude delegate_to_agent + toolset whitelist)
            parent_registry: ToolRegistry | None = kwargs.get("_parent_registry")
            filtered = self._build_child_registry(parent_registry, tools_whitelist)

            child_plugin = AgentPlugin(
                id=agent_id, name="SubAgent", category="execution",
                description="sub-agent delegation",
                tools=tuple(tools_whitelist or []),
            )
            node = AgentNodeConfig(
                id=agent_id, tools_override=tools_whitelist,
                toolset=toolset_name,
                max_iterations=max_iterations,
                budget_max_tokens=(
                    int(budget_max_tokens) if budget_max_tokens else None
                ),
                budget_max_seconds=(
                    float(budget_max_seconds) if budget_max_seconds else None
                ),
            )

            # Build the user-message: sub-agent role prefix + task.
            # (No trailing comma — a stray one makes this a 1-tuple and
            # downstream string concatenation would TypeError.)
            full_task = (
                "你是一个专注的子 agent。根据给定的任务独立完成工作，"
                "使用可用工具读取数据、运行分析、生成报告。\n"
                "回复简洁，直接给出结果。\n\n## 当前任务\n" + task
            )

            executor = AgentExecutor(
                registry=filtered or get_default_registry(),
                llm_config=LLMConfig.load(),
            )

            def _forward(et: str, data: dict[str, Any]) -> None:
                _forward_event(emit_event, agent_id, message_id, et, data)

            workspace_path = Path(workspace) if workspace else Path.cwd()
            result = executor.execute(
                child_plugin, full_task, workspace_path,
                context={"session_id": session_id},
                node=node,
                on_event=_forward,
            )

            _forward_event(emit_event, agent_id, message_id, "subagent_completed", {
                "agent_id": agent_id,
                "message_id": message_id,
            })

            return json.dumps(
                {
                    "status": "ok" if result.success else "error",
                    "answer": result.output,
                    "summary": result.summary,
                    "iterations": result.metrics.get("iterations", 0),
                    "tool_calls_made": result.metrics.get("tool_calls_made", 0),
                },
                ensure_ascii=False,
            )

        except Exception as exc:
            logger.exception("SubAgentTool failed")
            _forward_event(emit_event, agent_id, message_id, "subagent_failed", {
                "agent_id": agent_id,
                "message_id": message_id,
                "error": str(exc),
            })
            return json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
