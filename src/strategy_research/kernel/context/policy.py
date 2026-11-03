"""ContextPolicy：W2 仲裁的调用方策略（不进协议面）。

设计：docs/stage-c1-b5-w2-arbitration-20261029.md §1
上游：docs/stage-c1-b5-contexthub-w1w2-design-20261028.md §3

为什么策略由调用方传入而不写进 hub（讨论稿 §3 硬约束）：
「中枢不定业务优先级——由 provider 的 priority 声明」。hub 只做机制
（排序/裁剪/记账），哪些 kind 更稳定、哪些不可丢、谁的配额多少，
全部是调用方（组合根）的业务判断。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 与现有渲染序一致的默认秩（memory 先于 goal）。一上线就改字节的
# 默认值是不可接受的——这里和 session_controller 的现状逐条对齐。
DEFAULT_KIND_RANK: dict[str, int] = {
    "memory": 0,
    "goal": 1,
    "todo": 2,
    "hypothesis": 3,
    "knowledge": 4,
    "artifact": 5,
    "context": 6,      # PreRunContextProvider 的未知 injector 兜底 kind
    "tool_result": 8,
    "history": 9,
}

# 秩表里没有的 kind 落到这个值：保守地排在已知类型之后、tool_result 之前。
_UNRANKED = 7


@dataclass(frozen=True)
class ContextPolicy:
    """How to arbitrate a bundle when a token budget is supplied.

    All fields have "do nothing" defaults, so ``ContextPolicy()`` is a
    valid no-op policy: no protections, no per-provider caps, and a flat
    rank (every kind ties, leaving provider/source as the tie-break).
    """

    kind_rank: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_KIND_RANK))
    # 保护类：超预算也不裁（对齐讨论稿 §8 Q3「目标与假设不可丢」）。
    protected_kinds: frozenset[str] = frozenset({"goal"})
    # 谁产出谁拥有：单个 provider 的 token 上限；{} = 不设。
    provider_caps: dict[str, int] = field(default_factory=dict)

    def rank_of(self, kind: str) -> int:
        return self.kind_rank.get(kind, _UNRANKED)


__all__ = ["ContextPolicy", "DEFAULT_KIND_RANK"]
