# B5 收口：goal/memory 注入 react 路径 — 2026-10-29

> 上游：`docs/archive/2026-10/stage-c1-b5-wiring-20261029.md` §0（本刀是它记录的缺口）
> 前置：`1a9f2c49`（R-1 接线）、`db8bc7b8`（W2 仲裁）

---

## 0. 问题

生产 chat 默认走 Gateway/react（`QNODES_NEW_ENTRY` 默认 True），而
goal/memory 注入只存在于旧 `/send_async` 路径的 AgentLoop 上。
`PreRunContextProvider` 桥接了这两个 injector，但它要求
`__init__(loop, injectors)` 的 AgentLoop 实例——react 路径没有 loop。

**因此 chat 生产路径从未拿到 goal 与 memory**。B1-b 交付的注入只在测试里活。

---

## 1. 关键发现：injector 对 loop 的依赖只有三个属性

实测两个 pre-run injector 读的 loop 成员：

| injector | 读到的 loop 属性 | file:line |
|---|---|---|
| `GoalContextInjector` | `loop.enable_goal_injection`、`loop.session_id` | `context_injector.py:113,115` |
| `MemoryRetrieverInjector` | `loop.enable_memory_injection`、`loop.session_id` | `context_injector.py:299,327-336` |

**没有任何一个读 loop 的方法或 registry**。`inject_pre_run(loop, task, messages)`
的两个参数里 `messages` 也未被使用（两个实现都只改 `task`）。

**结论**：不需要新写 provider，也不需要改 injector——
一个 3 属性的 duck-typed adapter 就能让 `PreRunContextProvider` 在
react 路径工作。这是 less-is-more 的最优解：复用整个已验证的桥接链。

---

## 2. 方案

```python
# core/agent/context_providers.py（新增，W-D 所有权内）
class _LoopFacade:
    """pre-run injector 需要的最小 loop 表面（3 个属性，无方法）。"""

    def __init__(self, *, session_id: str | None,
                 enable_goal_injection: bool = True,
                 enable_memory_injection: bool = True) -> None:
        self.session_id = session_id
        self.enable_goal_injection = enable_goal_injection
        self.enable_memory_injection = enable_memory_injection


def build_prerun_provider_for_session(
    *, session_id: str | None,
    enable_goal_injection: bool = True,
    enable_memory_injection: bool = True,
) -> PreRunContextProvider:
    """react 路径的 pre-run provider（无 AgentLoop）。"""
    from .context_injector import (
        GoalContextInjector, MemoryRetrieverInjector,
    )
    return PreRunContextProvider(
        _LoopFacade(
            session_id=session_id,
            enable_goal_injection=enable_goal_injection,
            enable_memory_injection=enable_memory_injection,
        ),
        [GoalContextInjector(), MemoryRetrieverInjector()],
    )
```

**为什么是 facade 而不是新 provider**：

| 候选 | 代价 | 结论 |
|---|---|---|
| 重写 goal/memory provider（各 ~60 行） | 与 injector 双实现 → 格式/检索语义漂移风险 | 拒绝 |
| 改 injector 接受 `session_id` 而非 loop | 触碰 AgentLoop 调用点 + 5 个测试 | 拒绝（超出必要） |
| **`_LoopFacade`（3 属性）** | 6 行 | **采用**：injector 与桥接链零改动 |

---

## 3. 接线

`singleton._build_context_hub()` 增加一个 **per-session** 的 pre-run provider。
但 `ContextHub.register` 是单例注册、`build(query)` 每轮拿 query：

**问题**：`PreRunContextProvider` 在构造时绑定 loop（含 session_id），
而 hub 是进程单例，跨会话共享。

**解法**：facade 的 `session_id` 改为**可变的每轮赋值**——provider 的
`provide(query)` 已有 `query.session_id`，只要 facade 在 provide 时
读它，就不需要在构造时绑定。

实现：facade 存一个「当前 query」引用，`PreRunContextProvider.provide`
调用前由 hub 侧设置。但这会给 hub 加状态，违反「hub 不持有会话状态」。

**更干净的解法**：让 facade 的 `session_id` 是一个**读 query 的属性**，
由 provider 子类提供。即：

```python
class _SessionScopedPreRunProvider(PreRunContextProvider):
    """契约同 PreRunContextProvider，但每轮从 query 取 session。"""

    def provide(self, query):
        self._loop.session_id = query.session_id   # 每轮刷新
        return super().provide(query)
```

`_loop` 是 facade 实例，改它的属性不触碰任何会话状态存储——
它只是一个参数传递载体，不是状态容器。

**判定**：这不算「hub 持有会话状态」——状态在 query 里，facade 只是
把 query 的值转交给 injector。`ContextHub` 本身仍无状态。

---

## 4. 改动面

| 文件 | 改动 |
|---|---|
| `core/agent/context_providers.py` | `_LoopFacade` + `build_prerun_provider_for_session` + `_SessionScopedPreRunProvider` |
| `gateway/singleton.py` | `_build_context_hub()` 注册 pre-run provider（session 参数留空，每轮取自 query） |
| `tests/test_b5_prerun_react.py`（新） | goal/memory 在 react 路径可见；开关关闭时不注入 |

---

## 5. 测试

| # | 断言 |
|---|---|
| T1 | 有活跃 goal → react 轮次的 user 消息含 `<current-research-goal>` |
| T2 | `enable_goal_injection=False` → 不注入 |
| T3 | memory 命中 → 含 `<relevant-memory>` |
| T4 | `enable_memory_injection=False` → 不注入 |
| T5 | 无 session_id → 不注入（与 AgentLoop 守卫一致） |
| T6 | 每轮 session 正确切换（同一 hub 服务两个会话） |
| T7 | 顺序：goal 块在 memory 块之前（与旧路径一致） |
| T8 | 现有 `test_context_providers_bridge` 9 条零改动通过 |

---

## 6. less-is-more 调查表

| 需求 | 查过 | 结论 |
|---|---|---|
| goal 上下文取数 | `get_current_goal_context(session_id)`（`goal/__init__`） | 复用（injector 已封装） |
| memory 检索 | `PersistentMemory.find_relevant` | 复用（injector 已封装） |
| 前缀契约 | `PreRunContextProvider`（含 endswith 守卫） | 复用整个桥接 |
| loop 表面 | 3 个属性读点 | facade 6 行 |
| 新 provider | — | **不需要**（拒绝双实现） |
