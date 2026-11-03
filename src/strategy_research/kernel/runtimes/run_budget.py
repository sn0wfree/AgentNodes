"""预算执行点（A1 运行中门）—— 所有 LLM 调用的唯一预算入口。

A5 不变量 INV-9：**任何 LLM 调用必须经过 plan_call()**。
- react 节点每迭代调用一次（运行中检查）
- llm 节点调用一次（单次调用前检查）
- 未来任何新节点同样必须调用——这是「预算绕不过去」的结构保证

为什么单独成模块（而非散落各节点）：审计门①发现 llm 节点曾直接调
client.chat 而绕过预算检查。把检查收口到一处，节点只负责「问能不能调」。

与 budget_gate.py 的分层：
- budget_gate：**session 层**（chat 准入的静态额度合并）
- 本模块：**run 层运行时**（已消费量 vs 上限，每次调用前判定）
- strategy/budget.py：已消费量的记账（record_usage）与三维判定
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from strategy_research.core.agent.strategy.budget import (
    BudgetLimits,
    BudgetUsage,
    evaluate_budget,
    record_usage,
)


@dataclass
class RunBudget:
    """单 run 的预算状态（记账 + 判定）。"""

    limits: BudgetLimits = field(default_factory=BudgetLimits)
    usage: BudgetUsage = field(default_factory=lambda: BudgetUsage(started_at=time.perf_counter()))
    exhausted: bool = False
    reason: str | None = None

    def plan_call(self) -> tuple[bool, str | None]:
        """LLM 调用前的唯一检查点。返回 (allowed, reason)。

        已 exhausted 的对象直接短路（不重评估）——这同时挡住
        「剩余为 0 的预留切片」漏洞：0 上限在 evaluate_budget 里等于
        未启用（无限），预标 exhausted 是唯一诚实的表达（F2）。
        """
        if self.exhausted:
            return False, self.reason
        exceeded, reason = evaluate_budget(self.usage, self.limits)
        if exceeded:
            self.exhausted = True
            self.reason = reason
            return False, reason
        return True, None

    def record(self, response_usage: dict | None) -> None:
        """调用后记账（在 plan_call 之后、prompt 追加之前调用）。"""
        self.usage = record_usage(self.usage, self.limits, response_usage or {})

    # ── 预算树（F2，七规则④：父预留、子消费、settle 归还）──

    def reserve(self, n: int) -> list["RunBudget"]:
        """均分剩余额度为 n 个子切片（floor，余量留父）。

        子切片是独立对象——fanout 各分支各拿一份、各线程只碰自己的，
        热路径无锁。任一启用维度剩余为 0 的切片直接预标 exhausted
        （理由见 plan_call docstring）。price 字段透传（成本记账用）。
        """
        if n < 1:
            raise ValueError("reserve: 至少切 1 份")
        slices: list[RunBudget] = []
        for _ in range(n):
            limits = BudgetLimits(
                max_tokens=self._split_int(self.limits.max_tokens, self.usage.tokens, n),
                max_cost_usd=self._split_float(
                    self.limits.max_cost_usd, self.usage.cost_usd, n
                ),
                max_seconds=self._split_float(
                    self.limits.max_seconds, self.usage.elapsed(), n
                ),
                price_per_mtok_input=self.limits.price_per_mtok_input,
                price_per_mtok_output=self.limits.price_per_mtok_output,
            )
            starved = (
                limits.max_tokens == 0
                or limits.max_cost_usd == 0.0
                or limits.max_seconds == 0.0
            )
            slices.append(
                RunBudget(
                    limits=limits,
                    exhausted=starved,
                    reason="budget:reserve_starved" if starved else None,
                )
            )
        return slices

    def absorb(self, *children: "RunBudget") -> None:
        """settle 归还：吸收子切片的实际消耗。

        token/成本求和；时间不吸收（分支并行跑在父的墙钟里）；
        子切片的 exhausted 不回传（切份额耗尽 ≠ 父预算耗尽）；父 limits
        不变（未预留余量在 fanout 结束后仍归父）。
        """
        for child in children:
            self.usage.tokens += child.usage.tokens
            self.usage.prompt_tokens += child.usage.prompt_tokens
            self.usage.completion_tokens += child.usage.completion_tokens
            self.usage.cost_usd += child.usage.cost_usd

    @staticmethod
    def _split_int(limit: int | None, used: int, n: int) -> int | None:
        if not BudgetLimits._active(limit):
            return None
        return max(0, (int(limit) - used) // n)

    @staticmethod
    def _split_float(limit: float | None, used: float, n: int) -> float | None:
        if not BudgetLimits._active(limit):
            return None
        return max(0.0, (float(limit) - used) / n)

    def snapshot(self) -> dict:
        return {
            "total_tokens": self.usage.tokens,
            "cost_usd": round(self.usage.cost_usd, 6),
            "exhausted": self.exhausted,
            "reason": self.reason,
        }


INV_LLM_BUDGET = "INV-9: 所有 LLM 调用必须经过 RunBudget.plan_call（预算不可绕过）"
