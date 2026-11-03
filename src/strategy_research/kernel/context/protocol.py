"""ContextHub 协议面：ContextItem / ContextProvider / ContextQuery。

设计：docs/context-hub-discussion.md（W0 骨架）；P5-4 落地记录见
docs/stage-p54-context-hub-w0-design-20261027.md。

硬约束（讨论稿 §3，由架构门禁测试钉死）：
    中枢只依赖协议，绝不 import 生产方；中枢不持有会话状态。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ContextQuery:
    """一次上下文组装的请求面。

    task_text 是本轮任务串；其余为可选定位（跨域不串靠 provider 的
    scopes 声明 + 调用方传入的 id）。
    """

    task_text: str
    session_id: str | None = None
    study_id: str | None = None
    goal_id: str | None = None
    iteration: int = 0
    phase: str = ""


@dataclass(frozen=True)
class ContextItem:
    """一条上下文贡献。

    kind 取值约定：goal|todo|memory|hypothesis|skill|knowledge|artifact|
    tool_result|history（讨论稿 §3A）。tokens 由中枢统一估算，provider
    不填。
    """

    kind: str
    content: str
    source: str          # 溯源：provider 名 + 定位
    provider: str        # 贡献方名（= provider.name）
    priority: int = 0    # 同 kind 内相对重要度（降级用，W2 仲裁消费）
    tokens: int = 0      # 中枢统一口径填充
    confidence: float = 1.0
    scope: str = "session"
    created_at: str = ""  # ISO 时间，模型可判断新鲜度
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ContextProvider(Protocol):
    """生产方协议：实现并注册进中枢，中枢从不 import 生产方。"""

    name: str
    scopes: frozenset[str]

    def provide(self, query: ContextQuery) -> list[ContextItem]:
        """返回本次贡献（可为空）；异常由中枢降级，不应外抛。"""
        ...


__all__ = [
    "ContextItem",
    "ContextProvider",
    "ContextQuery",
]
