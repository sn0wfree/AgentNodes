"""共享 ToolContext 构造（B1-d）。

单一事实源：AgentLoop 与 react 节点都必须用同一份 ``ToolContext`` 装配
逻辑，否则权限语义会漂移——这正是 B1 调查发现的缺口（react 此前根本不
构造 ctx，三道闸门全部绕过）。

调用方提供「运行环境」（workspace/session_id/权限对象/事件发送），本模块
只负责把它们装配成 ToolContext，不 import 任何域实现。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def build_tool_context(
    *,
    tool_call_id: str,
    workspace: Path | None = None,
    session_id: str | None = None,
    strategy_dir: Path | None = None,
    runs_dir: Path | None = None,
    results_tsv: Path | None = None,
    write_roots: Any = None,
    read_roots: Any = None,
    emit_progress: Callable | None = None,
    emit_event: Callable | None = None,
    message_id: str | None = None,
    permission_evaluator: Any = None,
    permission_gateway: Any = None,
    data_store: Any = None,
    sandbox: Any = None,
) -> Any:
    """Assemble a ``ToolContext`` from an execution environment.

    Mirrors ``AgentLoop._execute_tool_call_core``'s constructor argument
    list so both executors produce equivalent contexts.
    """
    from .tools import ToolContext

    return ToolContext(
        workspace=workspace,
        session_id=session_id,
        strategy_dir=strategy_dir,
        runs_dir=runs_dir,
        results_tsv=results_tsv,
        write_roots=write_roots,
        read_roots=read_roots,
        emit_progress=emit_progress,
        emit_event=emit_event,
        message_id=message_id,
        permission_evaluator=permission_evaluator,
        permission_gateway=permission_gateway,
        tool_call_id=tool_call_id,
        data_store=data_store,
        sandbox=sandbox,
    )


def make_ctx_factory(
    *,
    workspace: Path | None = None,
    session_id: str | None = None,
    permission_evaluator: Any = None,
    permission_gateway: Any = None,
    emit_event: Callable | None = None,
    message_id: str | None = None,
    data_store: Any = None,
    sandbox: Any = None,
) -> Callable[[str, dict], Any]:
    """Return a ``(tool_call_id, params) -> ToolContext`` factory.

    This is the shape ``ReactConfig.ctx_factory`` expects (B1-d): the node
    asks for a context per tool call without knowing what fields it needs.
    """

    def _factory(tool_call_id: str, params: dict) -> Any:  # noqa: ARG001
        return build_tool_context(
            tool_call_id=tool_call_id,
            workspace=workspace,
            session_id=session_id,
            permission_evaluator=permission_evaluator,
            permission_gateway=permission_gateway,
            emit_event=emit_event,
            message_id=message_id,
            data_store=data_store,
            sandbox=sandbox,
        )

    return _factory


__all__ = ["build_tool_context", "make_ctx_factory"]
