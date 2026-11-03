"""context —— 平台数据面之外的第三个中枢：上下文组装。

「目录供件 → 事件记录 → 上下文组装 → LLM」三中枢之一（设计：
docs/context-hub-discussion.md）。只依赖协议，绝不 import 生产方
（core/agent / core/goal / core/study）——架构门禁钉死。
"""
from __future__ import annotations

from .hub import ContextBundle, ContextHub
from .protocol import ContextItem, ContextProvider, ContextQuery

__all__ = [
    "ContextBundle",
    "ContextHub",
    "ContextItem",
    "ContextProvider",
    "ContextQuery",
]
