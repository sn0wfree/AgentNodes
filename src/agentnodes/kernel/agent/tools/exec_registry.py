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

    **2026-11-05 做减法批次 4**：原先这里注册了两个业务 executor
    （``run_backtest_script`` 与 ``decide``），它们 resolve
    ``backtest.script`` / ``backtest.decide`` 两个 kind。这两个 kind 随
    ``apps/backtest`` 与 ``apps/strategy_acceptance`` 一起移除（归档
    ``testcase/golden/apps/``），executor 本身也是回测业务，故一并删除。

    框架层的 executor 注册（若有）仍在此函数内，与业务解耦。
    """


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
