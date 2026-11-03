"""PreRunContextProvider 桥接等价测试。

方法论（对齐 test_research_preset）：同一 loop 状态分别喂旧 injector
叠加循环与 hub 渲染路径，断言 full_task 逐字节相等——含检索 query 的
链式语义（memory 检索用 goal 拼接后的串）。
"""
from __future__ import annotations

from types import SimpleNamespace

from strategy_research.core.agent.context_providers import (
    build_prerun_context_provider,
)
from strategy_research.kernel.context import ContextHub, ContextQuery

TASK = "improve momentum_20_60"
GOAL_BLOCK = "<current-research-goal>\n目标是提升 Calmar\n</current-research-goal>"
MEMORY_BLOCK = "<relevant-memory>\n- [memory/笔记] 上轮动量策略已尝试\n</relevant-memory>"


def _make_injectors(captured: dict):
    """两个 pre-run injector 假件：goal(-100) 先拼、memory(-90) 用拼接串检索。"""

    class GoalInjector:
        name = "goal_context"
        order = -100

        def inject_pre_run(self, loop, task, messages):
            if not loop.enable_goal_injection:
                return task
            captured["goal_query"] = task  # goal 收到原始 task
            return GOAL_BLOCK + "\n\n" + task

    class MemoryInjector:
        name = "memory_retriever"
        order = -90

        def inject_pre_run(self, loop, task, messages):
            if not loop.enable_memory_injection:
                return task
            captured["memory_query"] = task  # 链式语义：含 goal 前缀
            return MEMORY_BLOCK + "\n\n" + task

    return [GoalInjector(), MemoryInjector()]


def _make_loop(**flags):
    return SimpleNamespace(
        enable_goal_injection=flags.get("enable_goal_injection", True),
        enable_memory_injection=flags.get("enable_memory_injection", True),
    )


def _legacy_prerun(loop, injectors, task):
    """旧 _prepare_run 的 pre-run 循环（参照实现）。"""
    for injector in sorted(injectors, key=lambda i: i.order):
        if getattr(injector, "order", 0) < 0:
            try:
                task = injector.inject_pre_run(loop, task, [])
            except Exception:
                pass
    return task


def _hub_prerun(loop, injectors, task):
    """新路径：整链桥接 → hub.build → 渲染。"""
    hub = ContextHub()
    hub.register(build_prerun_context_provider(loop, injectors))
    bundle = hub.build(ContextQuery(task_text=task))
    if bundle.items:
        return (
            "\n\n".join(item.content for item in bundle.items)
            + "\n\n" + task
        )
    return task


def test_full_task_byte_identical_to_legacy_loop():
    captured: dict = {}
    injectors = _make_injectors(captured)
    loop = _make_loop()

    legacy = _legacy_prerun(loop, injectors, TASK)
    bridged = _hub_prerun(loop, injectors, TASK)

    assert legacy == bridged == (
        MEMORY_BLOCK + "\n\n" + GOAL_BLOCK + "\n\n" + TASK
    )


def test_memory_retrieval_query_keeps_chained_semantics():
    """检索 query 是 goal 拼接后的串（整链桥接的核心等价点）。"""
    captured: dict = {}
    injectors = _make_injectors(captured)
    loop = _make_loop()

    _hub_prerun(loop, injectors, TASK)

    assert captured["goal_query"] == TASK
    assert captured["memory_query"] == GOAL_BLOCK + "\n\n" + TASK


def test_rendered_items_are_sourced_by_kind():
    """items 按最终渲染序排列，kind/source 可溯源。"""
    injectors = _make_injectors({})
    loop = _make_loop()

    hub = ContextHub()
    hub.register(build_prerun_context_provider(loop, injectors))
    bundle = hub.build(ContextQuery(task_text=TASK))

    assert [(i.kind, i.source) for i in bundle.items] == [
        ("memory", "injector:memory_retriever"),
        ("goal", "injector:goal_context"),
    ]
    assert bundle.items[0].content == MEMORY_BLOCK
    assert bundle.items[1].content == GOAL_BLOCK


def test_disabled_flags_yield_no_items():
    captured: dict = {}
    injectors = _make_injectors(captured)

    hub = ContextHub()
    hub.register(build_prerun_context_provider(
        _make_loop(enable_goal_injection=False, enable_memory_injection=False),
        injectors,
    ))
    bundle = hub.build(ContextQuery(task_text=TASK))

    assert bundle.items == []


def test_injector_exception_degrades_like_legacy_pass():
    """单个 injector 抛异常：旧循环是 pass（跳过该 injector，链继续），
    桥接同语义——后续 injector 照常执行。"""
    class ExplodingInjector:
        name = "goal_context"
        order = -100

        def inject_pre_run(self, loop, task, messages):
            raise RuntimeError("goal store locked")

    injectors = [ExplodingInjector()] + _make_injectors({})
    loop = _make_loop()

    legacy = _legacy_prerun(loop, injectors, TASK)
    bridged = _hub_prerun(loop, injectors, TASK)

    # 爆炸的 goal 被跳过，但第二个 goal injector（同 order，链继续）
    # 与 memory 照常注入——两边逐字节一致
    assert legacy == bridged == (
        MEMORY_BLOCK + "\n\n" + GOAL_BLOCK + "\n\n" + TASK
    )


def test_injector_contracts_affect_accounting():
    """每条 item 的 tokens 由 hub 统一口径计入账本。"""
    injectors = _make_injectors({})
    loop = _make_loop()

    hub = ContextHub()
    hub.register(build_prerun_context_provider(loop, injectors))
    bundle = hub.build(ContextQuery(task_text=TASK))

    assert bundle.accounting["per_provider"]["prerun_context"]["items"] == 2
    assert bundle.accounting["total_tokens"] == sum(
        i.tokens for i in bundle.items
    )


# ── 评审补强（多 agent 评审 2026-10-28）：真 injector 集成 ──


class _StubMemory:
    """记录检索 query 的 memory 假件（真 MemoryRetrieverInjector 消费）。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def find_relevant(self, task: str, max_results: int = 5):
        self.queries.append(task)
        return [
            SimpleNamespace(title="笔记", description="上轮动量策略已尝试"),
        ]


def _real_injectors(memory: _StubMemory, monkeypatch) -> list:
    """真 GoalContextInjector（goal 上下文打桩）+ 真 MemoryRetrieverInjector。"""
    from strategy_research.core.agent.context_injector import (
        GoalContextInjector,
        MemoryRetrieverInjector,
    )

    monkeypatch.setattr(
        "strategy_research.apps.goal.get_current_goal_context",
        lambda session_id: (GOAL_BLOCK, None),
    )
    return [
        GoalContextInjector(),
        MemoryRetrieverInjector(memory=memory, max_results=3),
    ]


def test_real_injectors_via_hub_match_legacy(tmp_path, monkeypatch):
    """真 injector 走 hub 路径与旧循环逐字节等价，且 memory 检索收到的
    是 goal 拼接后的串（链式语义在真实现上成立，非仅假件）。"""
    legacy_mem, hub_mem = _StubMemory(), _StubMemory()
    injectors = _real_injectors(legacy_mem, monkeypatch)
    loop = SimpleNamespace(
        enable_goal_injection=True, enable_memory_injection=True,
        session_id="s1",
    )

    legacy = _legacy_prerun(loop, injectors, TASK)

    hub_injectors = _real_injectors(hub_mem, monkeypatch)
    bridged = _hub_prerun(loop, hub_injectors, TASK)

    assert legacy == bridged
    # 桩 memory 命中一条 → 渲染序 memory 在前（真实现上复现链式叠加结果）
    assert legacy == MEMORY_BLOCK + "\n\n" + GOAL_BLOCK + "\n\n" + TASK
    # 链式语义：真 MemoryRetrieverInjector 收到的 query 含 goal 前缀
    assert hub_mem.queries == [GOAL_BLOCK + "\n\n" + TASK]


def test_real_memory_empty_task_yields_no_memory_contribution(monkeypatch):
    """真 MemoryRetrieverInjector 的空 task 短路：无 memory 贡献
    （设计文档 §6 验收项「空 task → 无贡献」的真实现钉版）。"""
    from strategy_research.core.agent.context_injector import (
        MemoryRetrieverInjector,
    )

    memory = _StubMemory()
    injectors = [MemoryRetrieverInjector(memory=memory, max_results=3)]
    loop = SimpleNamespace(enable_memory_injection=True, session_id="s1")

    hub = ContextHub()
    hub.register(build_prerun_context_provider(loop, injectors))
    bundle = hub.build(ContextQuery(task_text=""))

    assert bundle.items == []
    assert memory.queries == []  # 短路：检索从未被调用


def test_contract_violating_injector_is_skipped_not_garbled():
    """前缀契约违约（追加式改写）：按无贡献降级 + 链继续，
    不产出垃圾 block、不污染后续检索 query。"""
    class _AppendingInjector:
        name = "goal_context"
        order = -100

        def inject_pre_run(self, loop, task, messages):
            return task + " [modified]"  # 违约：追加式

    injectors = [_AppendingInjector()] + _make_injectors({})
    loop = _make_loop()

    hub = ContextHub()
    hub.register(build_prerun_context_provider(loop, injectors))
    bundle = hub.build(ContextQuery(task_text=TASK))

    kinds = [i.kind for i in bundle.items]
    assert kinds == ["memory", "goal"]  # 违约 injector 无贡献，链继续
    assert all("[modified]" not in i.content for i in bundle.items)
