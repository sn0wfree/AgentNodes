"""chat 预算门（决策 11：chat 路径无预算门，随 E1 立刻补）。

语义（三层检查点中的 E1 层）：
- 准入门（A2）：turn 准入时查预算——本模块提供 evaluate()。
- 运行中门（A1 细化）：ContextHub 每次 LLM 调用前查余额，超限 emit
  budget_exhausted + CancelScope（pre-e1 评估 §5 备注 1）——复用本模块。

设计：
- BudgetGate 是纯函数式组件（evaluate 无副作用），接线方决定超限动作。
- **字段级合并**：WorkRequest.budget 声明的字段覆盖会话缺省，
  未声明（None）的字段取缺省——即 None=「未声明」，不存在「显式不限」
  （要不限就把缺省阈值调高，ConfigHub 管缺省）。
- 默认预算 DEFAULT_SESSION_BUDGET 可被 ConfigHub 覆盖（接线 A1/E2）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from .contracts import BudgetSpec


@dataclass(frozen=True)
class BudgetUsage:
    """已消耗量（由调用方从 event_log/账本聚合）。"""

    tokens_used: int = 0
    llm_calls: int = 0
    started_at: float = 0.0


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str | None = None

    @staticmethod
    def ok() -> "BudgetDecision":
        return BudgetDecision(allowed=True)

    @staticmethod
    def deny(reason: str) -> "BudgetDecision":
        return BudgetDecision(allowed=False, reason=reason)


DEFAULT_SESSION_BUDGET = BudgetSpec(
    max_tokens=200_000,
    max_llm_calls=200,
    wallclock_seconds=1800.0,
)


def effective_budget(spec: BudgetSpec | None) -> BudgetSpec:
    """字段级合并：声明的字段覆盖会话缺省；未声明字段用缺省。"""
    if spec is None:
        return DEFAULT_SESSION_BUDGET
    return BudgetSpec(
        max_tokens=spec.max_tokens if spec.max_tokens is not None else DEFAULT_SESSION_BUDGET.max_tokens,
        max_llm_calls=spec.max_llm_calls if spec.max_llm_calls is not None else DEFAULT_SESSION_BUDGET.max_llm_calls,
        wallclock_seconds=(
            spec.wallclock_seconds
            if spec.wallclock_seconds is not None
            else DEFAULT_SESSION_BUDGET.wallclock_seconds
        ),
    )


class BudgetGate:
    """预算判定：allow / deny(reason)。无副作用，接线方决定超限动作。"""

    def evaluate(
        self,
        spec: BudgetSpec | None,
        usage: BudgetUsage,
        *,
        now: float | None = None,
    ) -> BudgetDecision:
        effective = effective_budget(spec)

        # 检查顺序固定为 tokens → llm_calls → wallclock（与既有
        # core/agent/strategy/budget.py 的 tokens→cost→time 一致）：
        # 顺序稳定才能让日志与告警可比。
        if effective.max_tokens is not None and usage.tokens_used > effective.max_tokens:
            return BudgetDecision.deny(
                f"tokens {usage.tokens_used} > max {effective.max_tokens}"
            )
        if effective.max_llm_calls is not None and usage.llm_calls > effective.max_llm_calls:
            return BudgetDecision.deny(
                f"llm_calls {usage.llm_calls} > max {effective.max_llm_calls}"
            )
        if effective.wallclock_seconds is not None and usage.started_at > 0:
            now_ts = now if now is not None else time.time()
            elapsed = now_ts - usage.started_at
            if elapsed > effective.wallclock_seconds:
                return BudgetDecision.deny(
                    f"wallclock {elapsed:.0f}s > max {effective.wallclock_seconds:.0f}s"
                )
        return BudgetDecision.ok()
