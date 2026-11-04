"""Python / evaluator function registry for non-LLM agent plugins.

Hoisted from ``SwarmRuntime._python_executors`` so every consumer
(AgentExecutor, SwarmRuntime, WorkflowRunner) shares one registry.
Builtins (``run_backtest_script`` / ``decide``) register on import,
guarded so missing optional deps never break startup.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_python_executors: dict[str, Callable[..., Any]] = {}
_evaluators: dict[str, Callable[..., Any]] = {}


def register_python_executor(name: str, fn: Callable[..., Any]) -> None:
    """Register a Python function for ``executor_type="python"`` plugins."""
    _python_executors[name] = fn


def get_python_executor(name: str) -> Callable[..., Any] | None:
    return _python_executors.get(name)


def list_python_executors() -> list[str]:
    return sorted(_python_executors.keys())


def register_evaluator(name: str, fn: Callable[..., Any]) -> None:
    """Register a decision function for ``executor_type="evaluator"`` plugins."""
    _evaluators[name] = fn


def get_evaluator(name: str) -> Callable[..., Any] | None:
    return _evaluators.get(name)


def list_evaluators() -> list[str]:
    return sorted(_evaluators.keys())


def extract_metrics_from_upstream(
    upstream: dict[str, str] | None,
) -> dict[str, Any]:
    """Pull ``metrics`` out of the first upstream JSON output containing it."""
    for result_str in (upstream or {}).values():
        if not isinstance(result_str, str):
            continue
        try:
            parsed = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and "metrics" in parsed:
            m = parsed["metrics"]
            if isinstance(m, dict):
                return m
    return {}


# ── Builtins ─────────────────────────────────────────────────────────


def _register_builtin_executors() -> None:
    """Register the built-in python executors.

    **resolve 在闭包内部、调用时才执行**：本函数由模块级 import 触发，
    此刻业务包可能尚未 import，registry 还是空的（ADR-003 端口化后
    kernel 不再直接 import apps）。把 resolve 搬到闭包里，让"谁需要
    谁解析"，与注册时机解耦。
    """
    def _backtest_executor(workspace_path, upstream=None, **kwargs):
        from strategy_research.kernel.agent.capability import resolve

        run_backtest_script = resolve("backtest.script")
        strategy_name = kwargs.get("strategy_name", "default")
        action = kwargs.get("action", "unknown")
        description = kwargs.get("description", "")
        return run_backtest_script(
            workspace_path=workspace_path,
            strategy_name=strategy_name,
            action=action,
            description=description,
        )

    register_python_executor("run_backtest_script", _backtest_executor)

    def _decide_executor(metrics=None, **kwargs):
        from strategy_research.kernel.agent.capability import resolve

        _decide_fn = resolve("backtest.decide")
        llm_verdict = kwargs.get("llm_verdict")
        cfg = kwargs.get("cfg")
        stagnation_count = kwargs.get("stagnation_count", 0)
        return _decide_fn(
            metrics=metrics or {},
            llm_verdict=llm_verdict,
            cfg=cfg,
            stagnation_count=stagnation_count,
        )

    register_evaluator("decide", _decide_executor)
    # Parity with the old SwarmRuntime registry: "decide" was also
    # reachable via the python-executor table.
    register_python_executor("decide", _decide_executor)


_register_builtin_executors()


__all__ = [
    "register_python_executor",
    "get_python_executor",
    "list_python_executors",
    "register_evaluator",
    "get_evaluator",
    "list_evaluators",
    "extract_metrics_from_upstream",
]
