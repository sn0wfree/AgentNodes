"""ContextHub W0 骨架测试。

设计：docs/design/context-hub-discussion.md §6 / docs/archive/2026-10/stage-p54-context-hub-w0-design-20261027.md。
覆盖：best-effort 降级（provider 抛异常不阻断 + dropped 记录）/ token
统一口径 / explain / 同 query 同 bundle 确定性 / 同名注册拒绝。
"""
from __future__ import annotations

from types import SimpleNamespace

from strategy_research.kernel.context import (
    ContextHub,
    ContextItem,
    ContextQuery,
)


def _item(kind: str, content: str, provider: str = "p") -> ContextItem:
    return ContextItem(
        kind=kind, content=content, source="test", provider=provider,
    )


def test_collect_merges_items_and_accounts_tokens():
    hub = ContextHub()
    hub.register(SimpleNamespace(
        name="a", scopes=frozenset({"session"}),
        provide=lambda q: [_item("goal", "目标文本")],
    ))
    hub.register(SimpleNamespace(
        name="b", scopes=frozenset({"session"}),
        provide=lambda q: [_item("memory", "记忆" * 100)],
    ))

    bundle = hub.build(ContextQuery(task_text="task"))

    assert [i.kind for i in bundle.items] == ["goal", "memory"]
    assert bundle.accounting["total_items"] == 2
    assert bundle.accounting["total_tokens"] > 0
    # tokens 由中枢统一填充（provider 未填）
    assert all(i.tokens > 0 for i in bundle.items)
    # 每 provider 账本
    assert bundle.accounting["per_provider"]["a"]["items"] == 1
    assert bundle.accounting["per_provider"]["b"]["items"] == 1


def test_provider_exception_degrades_to_no_contribution():
    """不变式 4：provider 故障降级为无贡献并记入 dropped，不外抛。"""
    def boom(_q):
        raise RuntimeError("goal store locked")

    hub = ContextHub()
    hub.register(SimpleNamespace(
        name="bad", scopes=frozenset({"session"}), provide=boom,
    ))
    hub.register(SimpleNamespace(
        name="good", scopes=frozenset({"session"}),
        provide=lambda q: [_item("memory", "m")],
    ))

    bundle = hub.build(ContextQuery(task_text="task"))

    assert [i.provider for i in bundle.items] == ["good"]
    assert len(bundle.dropped) == 1
    assert bundle.dropped[0]["provider"] == "bad"
    assert "goal store locked" in bundle.dropped[0]["reason"]
    assert bundle.accounting["per_provider"]["bad"]["items"] == 0


def test_explain_reports_kept_and_dropped():
    hub = ContextHub()
    hub.register(SimpleNamespace(
        name="bad", scopes=frozenset({"session"}),
        provide=lambda q: (_ for _ in ()).throw(ValueError("x")),
    ))
    hub.register(SimpleNamespace(
        name="good", scopes=frozenset({"session"}),
        provide=lambda q: [_item("goal", "g", provider="good")],
    ))

    bundle = hub.build(ContextQuery(task_text="t"))
    explanation = bundle.explain()

    assert explanation["accounting"]["total_items"] == 1
    assert [k["kind"] for k in explanation["kept"]] == ["goal"]
    assert explanation["dropped"][0]["provider"] == "bad"


def test_same_query_same_bundle_deterministic():
    """不变式 3：同 query + 同 provider 状态 → 同 bundle。"""
    hub = ContextHub()
    hub.register(SimpleNamespace(
        name="p", scopes=frozenset({"session"}),
        provide=lambda q: [_item("goal", f"ctx for {q.task_text}")],
    ))

    b1 = hub.build(ContextQuery(task_text="same"))
    b2 = hub.build(ContextQuery(task_text="same"))
    b3 = hub.build(ContextQuery(task_text="different"))

    assert b1.explain() == b2.explain()
    assert b1.items[0].content != b3.items[0].content


def test_duplicate_provider_name_rejected():
    hub = ContextHub()
    hub.register(SimpleNamespace(
        name="dup", scopes=frozenset({"session"}), provide=lambda q: [],
    ))
    try:
        hub.register(SimpleNamespace(
            name="dup", scopes=frozenset({"session"}), provide=lambda q: [],
        ))
        raise AssertionError("expected ValueError for duplicate name")
    except ValueError:
        pass


def test_empty_hub_yields_empty_bundle():
    bundle = ContextHub().build(ContextQuery(task_text="t"))
    assert bundle.items == []
    assert bundle.dropped == []
    assert bundle.accounting["total_tokens"] == 0


# ── 评审补强（多 agent 评审 2026-10-28 发现的边界）──────────


def test_provider_returning_non_list_degrades():
    """不变式 4 的边界：provide 返回非 list 时降级为无贡献（dropped 记录），
    返回 None 规范化为空贡献——两者都绝不外抛击穿 build。"""
    hub = ContextHub()
    hub.register(SimpleNamespace(
        name="none_ret", scopes=frozenset({"session"}),
        provide=lambda q: None,
    ))
    hub.register(SimpleNamespace(
        name="bad_ret", scopes=frozenset({"session"}),
        provide=lambda q: {"kind": "goal"},  # dict = 非 list
    ))
    hub.register(SimpleNamespace(
        name="good", scopes=frozenset({"session"}),
        provide=lambda q: [_item("memory", "m")],
    ))

    bundle = hub.build(ContextQuery(task_text="t"))

    assert [i.provider for i in bundle.items] == ["good"]
    reasons = {d["provider"]: d["reason"] for d in bundle.dropped}
    # None = 温和契约（无贡献），不进 dropped；非 list = 违约，降级记录
    assert "none_ret" not in reasons
    assert "bad_ret" in reasons and "expected list" in reasons["bad_ret"]


def test_prefilled_tokens_are_not_recomputed():
    """provider 预填 tokens 时 hub 尊重该值（口径优先级钉死）。"""
    hub = ContextHub()
    hub.register(SimpleNamespace(
        name="p", scopes=frozenset({"session"}),
        provide=lambda q: [ContextItem(
            kind="goal", content="短文本", source="s", provider="p",
            tokens=999,
        )],
    ))

    bundle = hub.build(ContextQuery(task_text="t"))

    assert bundle.items[0].tokens == 999
    assert bundle.accounting["total_tokens"] == 999


def test_hub_overwrites_spoofed_provider_field():
    """溯源一致性：item.provider 恒等于注册名，provider 自报值被覆写。"""
    hub = ContextHub()
    hub.register(SimpleNamespace(
        name="registered", scopes=frozenset({"session"}),
        provide=lambda q: [ContextItem(
            kind="goal", content="c", source="s", provider="liar",
        )],
    ))

    bundle = hub.build(ContextQuery(task_text="t"))

    assert bundle.items[0].provider == "registered"
