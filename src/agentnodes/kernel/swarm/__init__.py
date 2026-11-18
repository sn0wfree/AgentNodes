"""Swarm — multi-agent swarm orchestration.

**K2 懒导出（2026-11-18，顺序④）**：``SwarmRuntime`` / ``load_preset`` /
``list_presets`` 改为 PEP 562 模块级 ``__getattr__`` 按需加载——它们住在
``runtime.py``，会拉起 LangGraph/GraphEngine 重依赖链；而
``from agentnodes.kernel.swarm.types import AgentStatus`` 这类**轻量契约
导入**不应付出该成本（K3：types 只 import stdlib）。

兼容面不变：``from agentnodes.kernel.swarm import SwarmRuntime, load_preset``
仍可用（属性访问触发懒加载）。要彻底轻量，请直接从子模块导入：
``from agentnodes.kernel.swarm.types import AgentStatus``。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 类型标注仅供 IDE；运行时按需加载
    from agentnodes.kernel.swarm.preset_loader import (
        load_preset as _load_preset,
        list_presets as _list_presets,
    )
    from agentnodes.kernel.swarm.runtime import SwarmRuntime as _SwarmRuntime

__all__ = ["SwarmRuntime", "list_presets", "load_preset"]

_LAZY = {
    "SwarmRuntime": ("agentnodes.kernel.swarm.runtime", "SwarmRuntime"),
    "load_preset": ("agentnodes.kernel.swarm.preset_loader", "load_preset"),
    "list_presets": ("agentnodes.kernel.swarm.preset_loader", "list_presets"),
}


def __getattr__(name: str):  # noqa: ANN202 — PEP 562 动态属性，返回类型随名而变
    try:
        module_path, attr = _LAZY[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value  # 缓存，后续访问零开销
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
