"""生产方适配器：把 pre-run injector 链桥接为 ContextProvider。

P5-4（docs/archive/2026-10/stage-p54-context-hub-w0-design-20261027.md）：AgentLoop 的
pre-run 注入通路（GoalContextInjector / MemoryRetrieverInjector）迁入
ContextHub。桥接是**整链**而非逐 injector 拆散——memory 检索的 query 是
含 goal 前缀的叠加串（历史语义），逐个拆散会改变检索输入 → 行为漂移。
等价性由构造保证：provide() 内复刻旧叠加循环，产出按最终渲染序的多
items（内容逐字节同），等价测试钉死。

TodosInjector（per-iteration）与 GoalContinuationInjector（决策类）不转
（设计文档 §4），仍走 AgentLoop._injectors 原通路。
"""
from __future__ import annotations

import logging
from typing import Any

from agentnodes.kernel.context.protocol import ContextItem, ContextQuery

logger = logging.getLogger(__name__)


class PreRunContextProvider:
    """桥接 pre-run injector 链为单一 ContextProvider。

    **契约（P5-4 收窄）**：pre-run injector 必须是前缀注入式——返回
    ``f"{block}\\n\\n{task}"`` 或原样返回。hub 的 items+task 渲染契约
    表达不了任意 task 变换（旧循环曾接受追加式改写；生态里仅有的两个
    真实实现均为前缀式，docstring 也如此声明）。违约的变换不会被桥接
    正确分解——写新 injector 时遵守前缀契约。

    provide() 复刻 ``AgentLoop._prepare_run`` 的旧循环：按 injector.order
    升序逐个 ``inject_pre_run(loop, task, [])`` 叠加改写 task；每个注入了
    内容的 injector 产出一条 ContextItem（kind 按 injector 名映射），items
    顺序 = 最终渲染序（= 旧叠加结果里块的出现顺序）。链内的检索 query
    语义（memory 用 goal 拼接后的串）由复刻循环天然保持。
    """

    # injector.name → ContextItem.kind（溯源分区）
    _KIND_BY_INJECTOR = {
        "goal_context": "goal",
        "memory_retriever": "memory",
    }

    def __init__(self, loop: Any, injectors: list[Any]) -> None:
        self._loop = loop
        # 只收 pre-run 成员（order<0），按 order 升序 = 旧循环执行序
        self._injectors = sorted(
            (i for i in injectors if getattr(i, "order", 0) < 0),
            key=lambda i: i.order,
        )
        self.name = "prerun_context"
        self.scopes = frozenset({"session", "study", "goal"})

    def provide(self, query: ContextQuery) -> list[ContextItem]:
        task = query.task_text
        items: list[ContextItem] = []
        current = task
        for injector in self._injectors:
            try:
                new_task = injector.inject_pre_run(self._loop, current, [])
            except Exception as exc:  # noqa: BLE001 — 注入失败不阻断
                logger.debug("prerun injector %s failed: %s",
                             getattr(injector, "name", "?"), exc)
                continue
            if new_task == current:
                continue  # 无贡献（开关关闭 / 空命中）
            if len(new_task) <= len(current) + 2 or not new_task.endswith(current):
                # 契约违约（追加式改写 / 缩短 / 变换）：无法可靠分解出
                # 前缀块——按无贡献降级并告警，绝不把垃圾 block 喂进
                # 渲染序或污染后续 injector 的检索 query。
                logger.warning(
                    "prerun injector %s violated the prefix contract "
                    "(expected f'{block}\\n\\n{task}' or unchanged); skipped",
                    getattr(injector, "name", "?"),
                )
                continue
            # 契约：注入型 injector 返回 f"{block}\n\n{task}"；提取块内容
            block = new_task[: len(new_task) - len(current) - 2]
            if not block:
                continue
            current = new_task
            inj_name = getattr(injector, "name", "unknown")
            items.append(ContextItem(
                kind=self._KIND_BY_INJECTOR.get(inj_name, "context"),
                content=block,
                source=f"injector:{inj_name}",
                provider=self.name,
                priority=getattr(injector, "order", 0),
            ))
        # 渲染序 = 块在最终 full_task 中的出现序（先注入的先出现）
        items.reverse()
        return items


def build_prerun_context_provider(loop: Any, injectors: list[Any]) -> PreRunContextProvider:
    """从 injector 链构造 pre-run provider（AgentLoop.__init__ 调用）。"""
    return PreRunContextProvider(loop, injectors)


class _LoopFacade:
    """The minimal ``loop`` surface the pre-run injectors actually read.

    Both ``GoalContextInjector`` and ``MemoryRetrieverInjector`` only touch
    ``enable_goal_injection`` / ``enable_memory_injection`` / ``session_id``
    (context_injector.py:113,115,299,327) — no methods, no registry. So the
    react path (which has no AgentLoop) can drive the very same injectors
    through this adapter instead of growing a parallel implementation.
    """

    def __init__(
        self,
        *,
        session_id: str | None = None,
        enable_goal_injection: bool = True,
        enable_memory_injection: bool = True,
    ) -> None:
        self.session_id = session_id
        self.enable_goal_injection = enable_goal_injection
        self.enable_memory_injection = enable_memory_injection


class SessionScopedPreRunProvider(PreRunContextProvider):
    """``PreRunContextProvider`` that takes the session from each query.

    The hub is a process singleton while ``PreRunContextProvider`` binds its
    loop once at construction. Copying the query's ``session_id`` onto the
    facade before each ``provide`` keeps the hub stateless: the session lives
    in the query, the facade is only a carrier.
    """

    def provide(self, query: ContextQuery) -> list[ContextItem]:
        facade = self._loop
        if isinstance(facade, _LoopFacade):
            facade.session_id = query.session_id
        return super().provide(query)


def build_prerun_provider_for_session(
    *,
    enable_goal_injection: bool = True,
    enable_memory_injection: bool = True,
) -> SessionScopedPreRunProvider:
    """Pre-run provider for the react path (no AgentLoop available).

    Reuses the shipped injector chain end to end — the same
    ``GoalContextInjector`` / ``MemoryRetrieverInjector`` the AgentLoop path
    runs, so goal/memory block shapes cannot drift between executors.
    """
    from .context_injector import GoalContextInjector, MemoryRetrieverInjector

    return SessionScopedPreRunProvider(
        _LoopFacade(
            enable_goal_injection=enable_goal_injection,
            enable_memory_injection=enable_memory_injection,
        ),
        [GoalContextInjector(), MemoryRetrieverInjector()],
    )


class TodoContextProvider:
    """把 TodoStore 快照暴露为一条 ``kind="todo"`` 的上下文贡献（B5 W1）。

    设计（docs/archive/2026-10/stage-c1-b5-contexthub-w1w2-design-20261028.md §2.1-2.2）：
    - 数据源与渲染**复用** todo_tools 的 TodoStore / _format_todos_snapshot
      ——与 AgentLoop 侧 TodosInjector 共用同一 formatter，格式不可能漂移。
    - **单条 item**（整块快照），不拆逐条：拆条需要 todos 专用渲染器重建
      ``<current-todos>`` 包装，违反「渲染归渲染器」，且 todo 总量
      < 700 tokens，无裁剪收益。
    - ``tokens`` 不填（0）：由中枢统一估算（W0 钉死的口径）。
    - ``created_at`` 留空：TodoStore 不存时间戳，不编造。
    - ``priority=0``：当前单来源（TodoStore），同 kind 内无比较对象；
      留字段位是为了将来多来源（如 study 的 todos.md）接入时的降级序。
    """

    name = "todos"
    scopes = frozenset({"session"})

    def provide(self, query: ContextQuery) -> list[ContextItem]:
        if not query.session_id:
            return []
        try:
            from agentnodes.kernel.agent.builtin_tools.todo_tools import (
                TodoStore,
                _format_todos_snapshot,
            )

            todos = TodoStore.get(query.session_id)
        except Exception:  # noqa: BLE001 — 不变式 4：provider 故障降级不外抛
            return []
        if not todos:
            return []
        return [ContextItem(
            kind="todo",
            content=_format_todos_snapshot(todos),
            source=f"todo_store:{query.session_id}",
            provider=self.name,
            priority=0,
            scope="session",
            meta={"count": len(todos)},
        )]


__all__ = [
    "PreRunContextProvider",
    "SessionScopedPreRunProvider",
    "TodoContextProvider",
    "build_prerun_context_provider",
    "build_prerun_provider_for_session",
]
