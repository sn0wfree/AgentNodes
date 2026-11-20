"""CompletionStrategy — Strategy pattern for goal workflow completion.

Three built-in modes:

  - AutoCompleteStrategy: full audit + update_status(COMPLETE, audit=...)
  - LiteCompleteStrategy:  evidence-only via complete_lite()
  - ManualCompleteStrategy: no-op (waits for explicit /goal complete)

**第三轮复审候选 G2**：6 参数的 ``complete`` 接口收成单值
:class:`CompletionContext`（冻结 dataclass）；:func:`complete_goal` 是
hook 与 CLI 共用的深操作——选策略 + 异步派发只有一个拥有者。新策略仍可
经 ``CompletionStrategyFactory.register`` 注册。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletionContext:
    """一次目标完成所需的全部输入（单值 interface，取代 6 参数）。

    ``audit_rows`` 给定时策略直接用它（CLI 的全量 evidence 行形）；否则
    Auto 策略用 :func:`audit.rows_for` 自建 per-criterion 行形。两种行形
    都是已决口径（goal-coverage-unification-r014），此处只承载、不合并。
    """

    store: Any
    session_id: str
    goal_id: str
    snapshot: dict[str, Any]
    mode: str
    workflow_name: str
    recap: str | None = None
    audit_rows: list[Any] | None = None
    strategy: Any | None = None  # 显式注入（测试/宿主自定义）；None → Factory.get(mode)


class CompletionStrategy(Protocol):
    """How a goal workflow finishes its goal."""

    async def complete(self, context: CompletionContext) -> bool:
        """Execute the completion. Returns True on success."""
        ...


# ── Built-in Strategies ──────────────────────────────────────


class AutoCompleteStrategy:
    """Full audit + ``update_status(COMPLETE, audit=...)``."""

    async def complete(self, context: CompletionContext) -> bool:
        from agentnodes.apps.goal.audit import rows_for
        from agentnodes.apps.goal.models import GoalStatus

        rows = (
            context.audit_rows
            if context.audit_rows is not None
            else rows_for(
                context.snapshot.get("criteria", []),
                context.snapshot.get("evidence", []),
                f"Auto-completed by workflow {context.workflow_name}",
            )
        )
        context.store.update_status(
            session_id=context.session_id,
            goal_id=context.goal_id,
            expected_goal_id=context.goal_id,
            status=GoalStatus.COMPLETE,
            audit=rows,
            recap=context.recap,
        )
        return True


class LiteCompleteStrategy:
    """Evidence-only completion via ``complete_lite()``."""

    async def complete(self, context: CompletionContext) -> bool:
        context.store.complete_lite(
            session_id=context.session_id,
            goal_id=context.goal_id,
            expected_goal_id=context.goal_id,
            recap=context.recap,
        )
        return True


class ManualCompleteStrategy:
    """No automatic completion — waits for an explicit ``/goal complete``."""

    async def complete(self, context: CompletionContext) -> bool:
        logger.info(
            "Manual mode: workflow %s finished, awaiting /goal complete",
            context.workflow_name,
        )
        return True


# ── 深操作（hook 与 CLI 共用的唯一派发点）────────────────────


def complete_goal(context: CompletionContext) -> bool | None:
    """目标完成深操作（第三轮复审候选 G2）。

    单点拥有「选策略 + 异步派发」：running loop → ``create_task``（fire-and-
    forget，返回 None）；无 loop → ``asyncio.run`` 同步执行（返回 bool）。
    hook 与 CLI 都只构造 context 并调本函数，不再各自写 store 编排。
    """
    strategy = context.strategy or CompletionStrategyFactory.get(context.mode)
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(strategy.complete(context))
    loop.create_task(strategy.complete(context))
    return None


# ── Factory ───────────────────────────────────────────────────


class CompletionStrategyFactory:
    """Factory: select a CompletionStrategy by mode name."""

    _strategies: dict[str, CompletionStrategy] = {
        "auto": AutoCompleteStrategy(),
        "lite": LiteCompleteStrategy(),
        "manual": ManualCompleteStrategy(),
    }

    @classmethod
    def get(cls, mode: str) -> CompletionStrategy:
        """Get a strategy by mode name.  Falls back to ``"auto"``."""
        return cls._strategies.get(mode, cls._strategies["auto"])

    @classmethod
    def register(cls, mode: str, strategy: CompletionStrategy) -> None:
        """Register a custom strategy under a new mode name."""
        cls._strategies[mode] = strategy

    @classmethod
    def list_modes(cls) -> list[str]:
        """List all registered mode names."""
        return list(cls._strategies.keys())


__all__ = [
    "CompletionContext",
    "CompletionStrategy",
    "AutoCompleteStrategy",
    "LiteCompleteStrategy",
    "ManualCompleteStrategy",
    "CompletionStrategyFactory",
    "complete_goal",
]
