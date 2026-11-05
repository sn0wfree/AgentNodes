"""ContextHub：唯一上下文组装点（collect → bundle）。

设计：docs/design/context-hub-discussion.md §6（W0 骨架）；落地记录见
docs/archive/2026-10/stage-p54-context-hub-w0-design-20261027.md。

本阶段职责边界（与设计稿 W2 的差异如实标注）：
    - 只做 collect + 账本 + 溯源；**不仲裁不裁剪**（W2 ContextPlan 并入）；
    - **不渲染**（bundle → messages 归渲染器：PromptBuilder/ContextBuilder）；
    - provider 故障降级为「无贡献」并记入 bundle.dropped，不外抛（不变式 4）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from strategy_research.foundation.utils.token_utils import estimate_tokens as _estimate_message_tokens
from .policy import ContextPolicy
from .protocol import ContextItem, ContextProvider, ContextQuery

logger = logging.getLogger(__name__)


def _content_tokens(text: str) -> int:
    """统一 token 口径：复用 token_utils 的消息估算（包成单条 user 消息）。"""
    return _estimate_message_tokens([{"role": "user", "content": text}])


@dataclass
class ContextBundle:
    """一次组装的产出：items + 账本 + 降级记录。

    不变式（讨论稿 §6）：同 query + 同 provider 状态 → 同 bundle（确定性）；
    provider 故障永不外抛。
    """

    items: list[ContextItem] = field(default_factory=list)
    accounting: dict[str, Any] = field(default_factory=dict)
    dropped: list[dict[str, Any]] = field(default_factory=list)

    def explain(self) -> dict[str, Any]:
        """本次保留了什么、丢了什么、为什么（调试 agent 行为的第一手材料）。"""
        return {
            "kept": [
                {"kind": i.kind, "provider": i.provider, "source": i.source,
                 "tokens": i.tokens}
                for i in self.items
            ],
            "dropped": list(self.dropped),
            "accounting": dict(self.accounting),
        }


class ContextHub:
    """注册生产方、按 query 收集贡献、产出带账本的 bundle。

    三条硬约束（讨论稿 §3）：只依赖协议（架构门禁钉死 core/context 零
    生产方 import）；不做业务判断（优先级/开关都是 provider 侧声明）；
    不持有会话状态（build 是纯函数式收集）。
    """

    def __init__(self) -> None:
        self._providers: list[ContextProvider] = []

    def register(self, provider: ContextProvider) -> None:
        """注册生产方；同名重复注册是编程错误，直接拒绝。"""
        for existing in self._providers:
            if existing.name == provider.name:
                raise ValueError(f"duplicate context provider: {provider.name!r}")
        self._providers.append(provider)

    def build(
        self,
        query: ContextQuery,
        *,
        budget_tokens: int | None = None,
        policy: "ContextPolicy | None" = None,
    ) -> ContextBundle:
        """收集所有注册方的贡献；provider 异常降级为无贡献。

        tokens 由中枢统一估算（provider 不填）；账本按 provider 聚合。

        ``budget_tokens`` 为 None（默认）→ **不仲裁**，行为与 W0 完全一致。
        给出预算时按 ``policy`` 裁剪（W2）——策略是调用方的业务判断，
        hub 只执行机制（讨论稿 §3：中枢不定业务优先级）。
        """
        bundle = ContextBundle()
        per_provider: dict[str, dict[str, int]] = {}
        active_scopes = _active_scopes(query)
        for provider in self._providers:
            provider_scopes = getattr(provider, "scopes", None)
            if provider_scopes and not (set(provider_scopes) & active_scopes):
                # Scope isolation (W2): a study-only provider must not leak
                # into a session-scoped chat turn. Recorded, not silent.
                bundle.dropped.append({
                    "provider": provider.name,
                    "reason": (
                        "scope: "
                        f"{sorted(provider_scopes)} not in "
                        f"{sorted(active_scopes)}"
                    ),
                })
                per_provider[provider.name] = {"items": 0, "tokens": 0}
                continue
            try:
                items = provider.provide(query)
                if items is None:
                    items = []
                elif not isinstance(items, list):
                    raise TypeError(
                        f"returned {type(items).__name__}, expected list",
                    )
            except Exception as exc:  # noqa: BLE001 — 不变式 4：故障降级
                logger.warning(
                    "context provider %s failed: %s", provider.name, exc,
                )
                bundle.dropped.append({
                    "provider": provider.name, "reason": f"error: {exc}",
                })
                per_provider[provider.name] = {"items": 0, "tokens": 0}
                continue
            owned: list[ContextItem] = []
            for item in items:
                tokens = item.tokens or _content_tokens(item.content)
                owned.append(
                    ContextItem(
                        kind=item.kind,
                        content=item.content,
                        source=item.source,
                        provider=provider.name,
                        priority=item.priority,
                        tokens=tokens,
                        confidence=item.confidence,
                        scope=item.scope,
                        created_at=item.created_at,
                        meta=dict(item.meta),
                    )
                )
            bundle.items.extend(owned)
            per_provider[provider.name] = {
                "items": len(owned),
                "tokens": sum(i.tokens for i in owned),
            }
        total_tokens = sum(i.tokens for i in bundle.items)
        bundle.accounting = {
            "per_provider": per_provider,
            "total_items": len(bundle.items),
            "total_tokens": total_tokens,
        }
        if budget_tokens is not None:
            _arbitrate(bundle, budget_tokens, policy)
        return bundle


def _active_scopes(query: ContextQuery) -> frozenset[str]:
    """Infer this query's scope set from the ids it carries.

    ``provider.scopes`` existed since W0 but the hub never read it, so a
    study-only provider registered on the chat hub would leak across
    domains. Inference (no new protocol field) keeps the protocol frozen.
    """
    if query.study_id:
        return frozenset({"study", "session", "global"})
    if query.goal_id:
        return frozenset({"goal", "session", "global"})
    return frozenset({"session", "global"})


def _arbitrate(
    bundle: "ContextBundle",
    budget_tokens: int,
    policy: "ContextPolicy | None",
) -> None:
    """Trim ``bundle.items`` to ``budget_tokens`` (W2).

    Ordering is a deterministic total order so the same inputs always
    yield the same survivors (invariant 3):

        (kind_rank desc, priority asc, tokens desc, provider, source)

    **Higher rank is dropped first.** The rank table encodes stability
    (memory=0 is the most stable段, tool_result=8 the most volatile), so
    trimming from the top removes volatile material and keeps the durable
    blocks -- that is what "稳定段在上 / 目录在下" means operationally.

    Protected kinds are never dropped; if they alone exceed the budget the
    bundle is kept and flagged ``over_budget`` -- an honest relaxation of
    "total_tokens <= budget always holds", because that invariant and
    "goals are never dropped" are mutually exclusive.
    """
    # No policy supplied -> the dataclass defaults: goals protected, no
    # per-provider caps, stable-first rank table. That default encodes the
    # "goals are never dropped" rule from the design (讨论稿 §8 Q3); callers
    # override it explicitly to opt out.
    policy = policy or ContextPolicy()
    caps = policy.provider_caps or {}

    # Per-provider over-cap marking (谁产出谁拥有), recorded before trimming.
    tokens_by_provider: dict[str, int] = {}
    for item in bundle.items:
        tokens_by_provider[item.provider] = (
            tokens_by_provider.get(item.provider, 0) + item.tokens
        )
    for name, used in tokens_by_provider.items():
        cap = caps.get(name)
        if cap is not None:
            bundle.accounting["per_provider"].setdefault(
                name, {"items": 0, "tokens": 0},
            )["over_cap"] = used > cap

    def sort_key(item) -> tuple:
        return (
            -policy.rank_of(item.kind),   # higher rank drops first
            item.priority,
            -item.tokens,
            item.provider,
            item.source,
        )

    total = sum(i.tokens for i in bundle.items)
    if total <= budget_tokens:
        bundle.accounting["over_budget"] = False
        return

    # Candidates: droppable first, and within a provider that exceeded its
    # own cap those items come first (its owner set the limit).
    def drop_group(item) -> int:
        cap = caps.get(item.provider)
        over = cap is not None and tokens_by_provider.get(item.provider, 0) > cap
        return 0 if over else 1

    candidates = sorted(
        (i for i in bundle.items if i.kind not in policy.protected_kinds),
        key=lambda i: (drop_group(i), *sort_key(i)),
    )

    dropped_ids: set[int] = set()
    for item in candidates:
        if total <= budget_tokens:
            break
        dropped_ids.add(id(item))
        total -= item.tokens
        bundle.dropped.append({
            "provider": item.provider,
            "kind": item.kind,
            "source": item.source,
            "tokens": item.tokens,
            "reason": "budget",
        })

    # Stable order preservation: survivors keep their original relative order
    # so the render order of everything that lived is byte-identical.
    bundle.items = [i for i in bundle.items if id(i) not in dropped_ids]
    bundle.accounting["total_items"] = len(bundle.items)
    bundle.accounting["total_tokens"] = total
    bundle.accounting["over_budget"] = total > budget_tokens
    if bundle.accounting["over_budget"]:
        bundle.dropped.append({
            "reason": "budget_exhausted_protected",
            "tokens": total,
            "budget": budget_tokens,
        })


__all__ = [
    "ContextBundle",
    "ContextHub",
    "ContextPolicy",
]
