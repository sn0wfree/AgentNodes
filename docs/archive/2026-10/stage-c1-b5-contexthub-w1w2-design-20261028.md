# C1 · B5 — ContextHub W1（todos provider）+ W2（ContextPlan 仲裁）设计

> 归属：W-D（`core/context/**`、`core/agent/context_providers.py`）。
> 上游：`docs/archive/2026-10/stage-c1-team-contract-20261028.md`（§1 能力矩阵 / §5 所有权 / §6 验收 / §6.5 less-is-more）、
> `docs/archive/2026-10/stage-p54-context-hub-w0-design-20261027.md`（W0 现状）、
> `docs/design/context-hub-discussion.md`（W1/W2 切分）、`docs/archive/undated/context-hub-membership-rule.md`（成员边界）。
> 基线：分支 `refactor/v6-gateway`，HEAD `755d9051`。本文所有事实为本机实测，带 `file:line`；
> 估算一律标注「估算」。

---

## 0. 结论先行

| # | 结论 | 影响 |
|---|---|---|
| **C1** | **协议不需要动**（`core/context/protocol.py` 零改动）。W1/W2 所需的预算、分区、排序全部能用「`ContextHub.build()` 增加关键字参数 + `ContextItem.meta`」表达 | 满足硬约束「只加实现不改协议」 |
| **C2** | **W1 的 todos provider 必须单条 item**（`kind="todo"`，content = 现有 `_format_todos_snapshot` 输出）。拆成逐条 item 需要 todos 专用渲染器，违反「渲染器与中枢分离」，且收益为零（todo 总量估算 < 700 tokens） | 零协议改动、零格式漂移 |
| **C3** | **W1 在 chat 里今天拿不到任何 todo**——不是 provider 缺失，是写路径把 todo 写进了 `"default"` 桶（三个独立缺陷叠加，见 §2.3）。provider 写出来也是空。**这是 W1 的前置阻塞项，必须由协调者派单**（`todo_tools.py` 不在 W-D 所有权内） | W1 真实工作量 = provider 30 行 + 前置修复 |
| **C4** | **「稳定段在上 / 目录在下」按「块内分区」落地，不移动任何现有字节**：所有 items 被拼进**同一条** user message（`session_controller.py:298-301`、`loop.py:590-593`），块内顺序对 KV cache **零影响**；cache 边界在 message 边界（system vs 末条 user），现状已经正确。落地方式 = 固定 kind 秩 `memory(0) < goal(1) < todo(2)`——todo（任务目录）天然排在最后、紧贴用户输入 | 不改字节、不破 W0 等价测试 |
| **C5** | **生产 chat 的 ContextHub 根本不存在**：`Gateway` 没有 `context_hub` 形参，`SessionController(context_hub=None)` 恒为 None（实测），而 `QNODES_NEW_ENTRY` 默认 True（`entry_adapters.py:32-35`）→ **B1-b 的上下文注入在生产上完全未被调用**，只在测试里活着 | W1/W2 上线前提是协调者接线（§5） |
| **C6** | **W2 的预算来源必须先修**：`SessionController._build_compactor` 读 `self._client.model_context_tokens`，而 `OpenAICompatClient` 没有该属性（真实属性在 `client.config.model_context_tokens`，`config.py:164`）→ 压缩阈值恒落 `8_000` 回退值。W2 若照抄这个取法会继承同一缺陷 | 预算取数单点化（§4.4） |

---

## 1. 现状复核（W0 落地后实际是什么）

### 1.1 协议面（冻结，未改）

| 字段 | 位置 | 现状 |
|---|---|---|
| `ContextItem.kind` | `protocol.py:40` | 取值约定含 `todo`（docstring `protocol.py:35-37`） |
| `ContextItem.priority` | `protocol.py:44` | 注释即写「同 kind 内相对重要度（降级用，**W2 仲裁消费**）」——定义已就位，**W0 零消费** |
| `ContextItem.tokens` | `protocol.py:45` | 中枢统一填（`hub.py:98`） |
| `ContextItem.scope` | `protocol.py:47` | 中枢只透传（`hub.py:108`），**不参与过滤** |
| `ContextProvider.scopes` | `protocol.py:57` | **中枢从不读取**（`hub.py:78-117` 全文无 `scopes`） |
| `ContextProvider.provide` | `protocol.py:59` | 单次调用，无 iteration 语义 |

### 1.2 中枢（W0）

- 收集 + 账本 + 降级：`hub.py:71-124`。provider 异常 → `dropped`（`hub.py:87-95`），非 list → 降级（`hub.py:83-86`），`None` → 空贡献。
- **不做仲裁**：`hub.py:6-10` 自述「只做 collect + 账本 + 溯源；不仲裁不裁剪」。
- **不做渲染**：产出 `ContextBundle`，`explain()` 只回 kept/dropped/accounting（`hub.py:40-50`）。
- 唯一渲染点有两处（重复实现，各 3 行）：
  - AgentLoop：`loop.py:589-593`
  - SessionController：`session_controller.py:296-301`

### 1.3 生产方（W0 已迁）

`PreRunContextProvider`（`context_providers.py:23-93`）：

- 构造时对 `order<0` 成员做一次性快照（`context_providers.py:48-51`）。
- `provide()` 复刻旧叠加循环（`context_providers.py:56-93`），前缀契约 + `endswith` 守卫（`context_providers.py:68-77`）。
- **`priority=getattr(injector,"order",0)`（`context_providers.py:89`）**——把 injector 的**执行序**当成了 item 的**重要度**。
- **`items.reverse()`（`context_providers.py:92`）**——渲染序 = 逆注入序（实测 goal(-100) 先注入、memory(-90) 后注入，最终渲染为 `MEMORY\n\nGOAL`，见 `tests/test_context_providers_bridge.py:86-88`）。
- todos/continuation 不转（`context_providers.py:10-11`）。

### 1.4 旧 injectors（未动）

| injector | 位置 | order | 生命周期 | 消费点 |
|---|---|---|---|---|
| `GoalContextInjector` | `context_injector.py:95-124` | -100 | pre-run | 已桥接进 hub |
| `MemoryRetrieverInjector` | `context_injector.py:265-380` | -90 | pre-run | 已桥接进 hub |
| `TodosInjector` | `context_injector.py:130-165` | 0 | **per-iteration** | `loop.py:900-906`（`order == 0` 才跑） |
| `GoalContinuationInjector` | `context_injector.py:171-244` | 100 | post-response（返回 bool = 决策） | `loop.py` 策略步；**按设计不转** |

`TodosInjector` 的具体行为（这是 W1 必须不破坏的语义）：
1. `if not loop.session_id: return`（`context_injector.py:151-152`）
2. `TodoStore.get(loop.session_id)`（`context_injector.py:154-155`）
3. 空列表 → 不注入（`context_injector.py:158-159`）
4. `block = _format_todos_snapshot(todos)`（`context_injector.py:160`）
5. **hash 去重**：`h = hash(block); if self._last_hash == h: return`（`context_injector.py:161-164`）
6. `messages.append({"role":"system","content":block})`（`context_injector.py:165`）

---

## 2. W1：todos provider

### 2.1 数据源与读路径（不新造）

| 需要的东西 | 已有入口 | 位置 | 复用理由 |
|---|---|---|---|
| 读 todo 列表 | `TodoStore.get(session_id)` | `todo_tools.py:39-42` | 进程内 per-session 唯一真源（`todo_tools.py:29-37`），加锁、返回浅拷贝 |
| 渲染 todo 块 | `_format_todos_snapshot(todos)` | `todo_tools.py:64-72` | `<current-todos>` 包装 + `N. [status] content (id=...)` 格式；W1 **不得重写**（否则与 per-iteration 注入块出现两种格式） |
| 写 todo | `TodoWriteTool` → `TodoStore.set` | `todo_tools.py:167-201` | 不动 |
| 事件 | `todo_updated`（`todo_tools.py:197-201`） | — | 不动（前端抽屉已有消费者） |

**结论**：W1 没有新数据源、没有新格式、没有新读路径。provider 只是「把 `TodoStore.get` + `_format_todos_snapshot` 包成一条 `ContextItem`」。

### 2.2 `kind="todo"` 的 ContextItem 构造（逐字段）

```python
# core/agent/context_providers.py（新增，W-D 所有权内）
class TodoContextProvider:
    name = "todos"
    scopes = frozenset({"session"})

    def provide(self, query: ContextQuery) -> list[ContextItem]:
        if not query.session_id:
            return []
        try:
            from .builtin_tools.todo_tools import TodoStore, _format_todos_snapshot
            todos = TodoStore.get(query.session_id)
        except Exception:                       # 不变式 4：不外抛
            return []
        if not todos:                           # 对齐 context_injector.py:158-159
            return []
        return [ContextItem(
            kind="todo",
            content=_format_todos_snapshot(todos),
            source=f"todo_store:{query.session_id}",   # 溯源：库 + 会话
            provider=self.name,
            priority=_todo_priority(todos),            # 见 §2.5
            scope="session",
            created_at="",                             # 见 §2.5 说明
            meta={"count": len(todos), "visible": True},
        )]
```

字段依据：
- `kind="todo"`：`protocol.py:40` 的取值约定已列（`protocol.py:35-37`）。
- `source`：协议要求「provider 名 + 定位」（`protocol.py:42`），这里给到会话级粒度。
- `priority`：**同 kind 内**相对重要度（`protocol.py:44`）。单 provider 单 kind 时无比较对象，仍填值以支持未来「多来源 todo」（如 study 的 `todos.md` 也接进来）时的降级序。
- `tokens`：**不填**（0），由中枢统一估算（`hub.py:98` 的 `item.tokens or _content_tokens(...)`）——这是 W0 已钉死的口径（`tests/test_context_hub.py:155-169`）。
- `created_at`：`TodoStore` 不存时间戳（`todo_tools.py:98` 只保存 `{id, content, status}`），**不编造**。留空并在 `meta` 记 `count`。

### 2.3 前置阻塞：chat 里 todo 写不到 session 名下（实测）

这是本次调查最重要的发现，三个缺陷叠加：

| # | 缺陷 | 证据 | 后果 |
|---|---|---|---|
| **B-a** | `TodoWriteTool.execute` 只认 kwargs 里的 `session_id`，**不读 `ctx.session_id`** | `todo_tools.py:168`：`session_id = str(kwargs.get("session_id") or "default")` | 任何不经 `_build_tool_kwargs` 的调用方都会落到 `"default"` |
| **B-b** | react 节点调工具时只传 LLM 参数 + `ctx`，不注入 `session_id` | `react.py:141` `registry.execute(name, params, ctx=ctx)`；`_build_tool_kwargs`（`loop_tools.py:53-88`）**只被 AgentLoop 用**（`loop_tools.py:135`） | chat react 路径 → `"default"` |
| **B-c** | 生产 Gateway 的 `ctx_factory` 为 None（无 workspace/权限），react 连 `ctx` 都不传 | `session_controller.py:173-185` 的构造条件；实测 `ReactConfig.ctx_factory is None` | 连 `ctx.session_id` 都没有兜底 |

实测（本机，只读探针，已删除）：

```
react 路径：TodoStore.get("sess-react") == []        # 期望 1 条
             TodoStore.get("default")   == [1 条]     # 实际落点
AgentLoop 路径：TodoStore.get("sess-loop") == [1 条]  # 正确
```

**叠加结论**：chat 的两个入口都有问题——
- Gateway/react（默认入口）：todo 落 `"default"`，按 session 永远读不到。
- `build_chat_agent_loop`（旧 `/send_async` 路径）：`AgentLoop(session_id=attempt.session_id)`（`service.py:932-944`）→ `_build_tool_kwargs` 注入 `session_id`（`loop_tools.py:61-62`）→ 写入正确；`TodosInjector` 也能读到。**所以 todos 只在旧路径可见。**

**因此 W1 的 provider 在 chat react 路径上会稳定返回空**，直到 B-a 修复。修复方式（二选一，推荐前者，因为它是单点且向后兼容）：

```python
# todo_tools.py:168 建议改为（请求协调者代改，不在 W-D 所有权内）
session_id = str(kwargs.get("session_id") or getattr(ctx, "session_id", None) or "default")
```

**派单请求（§5 表已列）**：`core/agent/builtin_tools/todo_tools.py` 无明确 owner（契约 §5 未列）。按契约「未写的视为未决，先问协调者」，本文不擅自实施。

附带记录（范围外，不修）：`role_factory.build_agent_loop` 不传 `session_id`（`role_factory.py:157-176`，实测默认 None）→ 所有 study/swarm 的 AgentLoop 都是 `session_id=None` → `TodosInjector` 因守卫直接返回（`context_injector.py:151`）；同时它们的 `todowrite` 也写 `"default"`，**跨 study 共享同一个 todo 桶**。当前因注入被守卫挡住而不可见，但 B-a 修复后必须同时让 role_factory 传 session_id，否则会把这条隐性污染变成可见污染。**记为依赖项 D-2。**

### 2.4 per-iteration 语义怎么保（替换 / 共存 / 桥接）

**裁定：共存，不是双轨。** 理由必须精确，否则会被误判为双轨漂移：

| 通路 | 触发时机 | 作用 | 内容来源 |
|---|---|---|---|
| `TodoContextProvider`（新） | run 开始（`hub.build` 一次） | 让 **chat react**（max_iterations=1，`session_controller.py:60`）在本轮**首次** LLM 调用前看到 todos | `_format_todos_snapshot` |
| `TodosInjector`（保留） | 每次迭代（`loop.py:900-906`） | 让 **AgentLoop 多轮**在 todo 变化后**刷新**（hash 去重） | `_format_todos_snapshot` |

- 两者**共用同一个 formatter**（`todo_tools.py:64`）和同一个 store（`todo_tools.py:29`）→ 不可能出现格式或数据漂移。
- 生命周期不同（一次性快照 vs 变化即刷）→ 不是「同一件事的两套实现」。
- **避免双注入**：`TodoContextProvider` **只注册进 SessionController 的 hub**，不注册进 `AgentLoop` 的 hub（`loop.py:444-445` 是 W-P 所有权，见 §5）。W-P 侧无需改动，因为 AgentLoop 的 hub 仍只挂 `PreRunContextProvider`。
- **不注册进桥接 provider**：`PreRunContextProvider` 只收 `order<0`（`context_providers.py:48-51`），`todos_snapshot` 的 order=0 天然被排除，不存在重复。

**迁移路径（W2 之后，可选）**：若 hub 获得 per-iteration build（目前没有，`hub.build` 每次调用等价），`TodosInjector` 的 `inject_per_iteration` 可退化为「调 `TodoContextProvider.provide` + 保留 hash 去重 + append」。届时 formatter 的 import 从 injector 消失，单点收口。**W1 不做**（less-is-more：现在没有第二个调用方需要它）。

### 2.5 为什么是单条 item，不是逐条 todo

| 方案 | 收益 | 代价 |
|---|---|---|
| **A（推荐）单条** `kind="todo"`，content = 整块 | 与 `TodosInjector` 逐字节同格式；渲染零改动；item 粒度 = 注入粒度 | 无法按 todo 单独裁剪 |
| B 逐条 item（每条一个 todo） | 可按「已完成 → 待办」顺序裁剪 | 需要 todos 专用渲染器重建 `<current-todos>` 包装 → 违反「渲染归渲染器、中枢不硬编码模板」（`context-hub-membership-rule.md:121`）；且 block 顺序与 `TodosInjector` 不一致，产生两套输出 |

**决定性论据**：todo 块体积很小。`_format_todos_snapshot` 每条约 40-70 字符 + 2 行包装（`todo_tools.py:66-71`），典型 5 条 ≈ 300 字符 ≈ 100 tokens（按 `token_utils` 的 `chars_per_token=3.0`，`token_utils.py:12`）。**估算**：即使 20 条也 < 500 tokens。为这个量级引入专用渲染路径不值得。

---

## 3. W2：ContextPlan 仲裁

### 3.1 `priority` 怎么用（现有字段，零协议改动）

`protocol.py:44` 的语义是「**同 kind 内**相对重要度」。这决定了仲裁必须分两级：

1. **跨 kind 的顺序** → 来自 **kind 秩表**（调用方传入的策略，见 §3.2），**不是** `priority`。
2. **同 kind 内的取舍** → `priority` 降序保留。

关键约束（`context-hub-discussion.md:69`、`context-hub-membership-rule.md:120`）：「中枢不定业务优先级——由 provider 的 priority 声明」。因此**秩表不能硬编码在 hub 里**，必须由组合根（SessionController / AgentLoop）作为参数传入。hub 只做机制。

同时必须**纠正 W0 的一个语义误用**：`PreRunContextProvider` 把 injector 的 `order`（-100/-90，执行序）填进了 `priority`（`context_providers.py:89`）。W2 消费 priority 后，这个负数会让 memory(-90) 排在 goal(-100) 之上——这是 `reverse()`（`context_providers.py:92`）的副作用，不是设计意图。**W1/W2 期间不动它**（因为动它 = 改渲染字节 = 破 W0 等价测试 `tests/test_context_providers_bridge.py:86-88`），但在仲裁的排序键里显式声明「同 kind 内 priority 仅作次级键」，并由 kind 秩主导跨 kind 顺序。修正留到 W3（`PreRunContextProvider` 内部把 order 归一化为 rank 0/1）。

### 3.2 「稳定段在上 / 目录在下」的落地方式

先纠正一个事实性前提：**块内顺序对 KV cache 没有任何影响**。

依据：全部 items 被拼进**同一条** message——
- SessionController：`prefix = "\n\n".join(item.content for item in bundle.items)`，然后 `prefix + "\n\n" + 原文` 塞回**同一条** user message（`session_controller.py:298-301`）。
- AgentLoop：`"\n\n".join(...) + "\n\n" + full_task` → `build_initial_messages` 的**单条** user message（`loop.py:589-595`，`context.py:147-157`）。

KV cache 的粒度是 message/前缀字节序列。同一条 message 内部换顺序不改变「前缀是否命中」——只改变该 message 的字节。真正的 cache 边界是 **system 消息 vs 后续消息**，而现状已经正确：

```
[messages[0]]  system  = principles.md + chat.md（含排序后的 tool_list）
[history...]   历史（append-only，turns 单调增长）
[messages[-1]] user    = items 块 + "\n\n" + 本轮输入      ← 唯一易变段
```

`tool_list` 的稳定性有实证：`loop_assembly.py:64-69` 对 `registry._tools.keys()` 做 `sorted()` 再拼字符串 → 同一 registry 下逐字节稳定。**所以「目录在下」在 message 粒度上今天已经满足。**

**裁定（不是移动字节，而是固定块内分区）**：

| 解读 | 处置 | 理由 |
|---|---|---|
| **A 位置式**：把 `{tool_list}` 从 system 挪到块尾 | **拒绝** | `chat.md:16` 在 system 内；挪走 = 改 system 字节 = 破 B1-a 等价（`tests/test_b1_chat_react_wiring.py:64-78`）且把最可缓存的段变成易变段 |
| **B 块内分区（采用）** | kind 秩 `memory(0) < goal(1) < todo(2)`，todo（任务目录）排最后、紧贴用户输入 | 与现状**零字节差**（当前渲染就是 memory→goal，todo 是新增在尾部，见 §3.3），且语义上「稳定的产出在上、每轮会变的任务目录在下」成立 |

秩表默认值（由调用方传入，hub 不硬编码）：

```python
DEFAULT_KIND_RANK = {"memory": 0, "goal": 1, "todo": 2, "hypothesis": 3,
                     "knowledge": 4, "artifact": 5, "tool_result": 8, "history": 9}
```

注意：这个默认值**必须**与现有渲染序一致（memory 先于 goal），否则 W2 一上线就改字节。校验方式见 §7 的 T-3（字节等价回归）。

### 3.3 预算超限裁谁

设计前提：**bundle 里只有 provider 贡献的 items**，没有任何 history/tool schema（W0 已把 `build(query, *, history, tools)` 的 history/tools 形参推迟，`stage-p54-context-hub-w0-design-20261027.md:49-52`）。因此「不可裁区」natural 成立——system 与 history 根本不在 items 里，hub 无从裁剪它们。这比讨论稿 §3B（`context-hub-discussion.md:105-111`）的「不可裁区」更简单，也更安全。

**裁剪序（确定性，全序比较器）**：

```
drop_candidates = items filtered by (kind not in policy.protected_kinds)
sort key = (kind_rank[kind] asc,      # 低秩先丢：tool_result(8) → history(9) 先于 memory/goal
            priority asc,             # 同 kind 内低重要度先丢
            tokens desc,              # 同 priority 先丢大的（最大节省）
            provider asc, source asc) # 决定性 tie-break（不变式 3）
```

- `protected_kinds` 默认 `("goal",)`——对齐讨论稿 §8 Q3「目标与假设不可丢」（`context-hub-discussion.md:225`）。但**默认值由调用方显式传**，hub 不内置业务判断。
- 已经丢到只剩 protected 仍超预算 → **保留并标记** `accounting["over_budget"] = True`，同时 `dropped` 记 `{"reason": "budget_exhausted_protected"}`。这是对讨论稿不变式 1「`total_tokens <= budget` 恒成立」（`context-hub-discussion.md:198`）的**如实放宽**：保护类 item 单独超预算时，「恒成立」与「目标不可丢」互斥。W0 已有先例（差异如实标注，`stage-p54-context-hub-w0-design-20261027.md:49-52`）。
- 存活 item 的**相对顺序不变**（稳定排序）→ 渲染序对存活者零变化。

**「谁产出谁拥有」的实现方式**：protocol 冻结，provider 不能在协议上声明配额。落法是把「归属」落在**账本**而不是**状态**：

```python
bundle.accounting["per_provider"][name] = {
    "items": n, "tokens": t,
    "over_cap": bool,        # 新增：该 provider 是否超出自己的 cap
}
```

调用方可通过 `provider_caps: dict[str, int]` 为单个 provider 设上限（默认 `{}` = 不设）。裁剪时先裁超 cap 的 provider 的 items（同 provider 内按上面的键），再裁全局。这样「谁产出谁拥有」= 「谁的账记在谁头上、谁先被自己的上限约束」，**不需要 props 扩字段**。

### 3.4 仲裁输出仍是 `list[ContextItem]`；渲染归谁

| 问题 | 答案 | 依据 |
|---|---|---|
| 仲裁输出形态 | `ContextBundle.items`（仍是 `list[ContextItem]`），顺序即渲染序 | `hub.py:36`；保持 W0 契约（`tests/test_context_hub.py:37` 断言顺序） |
| hub 渲染吗 | **不渲染** | 讨论稿开放问题 1 建议「不拥有」（`context-hub-discussion.md:220-221`），W0 已按此落地（`hub.py:8-9`） |
| 渲染归谁 | 现有两处调用方（`session_controller.py:296-301`、`loop.py:589-593`）。W2 **不新增第三处** | less-is-more 规则 4（改造优先于新建） |
| 要不要抽公共渲染函数 | **建议抽，但作为独立纯函数放 `core/context/render.py`**，不是 hub 方法 | 现在同一段逻辑在 2 处逐字重复（3 行），属于「同包纯函数复制」的反面；但 hub 一旦有 `render()` 方法就等于「中枢拥有渲染」，违反开放问题 1 |

`render.py` 的内容（唯一新增纯函数，无 import 生产方，架构门禁不受影响）：

```python
def join_items(items: list[ContextItem]) -> str:
    """块内渲染：items 序即渲染序（仲裁结果必须被尊重，禁止重排）。"""
    return "\n\n".join(i.content for i in items)
```

**判断是否值得新建**（less-is-more 规则 1，先列现有入口）：

| 候选 | 能否复用 | 结论 |
|---|---|---|
| `ContextBuilder.build_initial_messages`（`context.py:130-157`） | 它做的事是 system+history+user 组装 + memory recall，不是 items 拼接 | 不复用（职责不同），也不改它 |
| `PromptBuilder.build_messages`（`prompt_builder.py:196-206`） | **零生产调用方**（全仓 grep 只命中定义本身）→ 死契约 | **禁止**作为 W2 渲染器；按契约 §6.5 规则 2 应删/标注（派单，见 §5） |
| `_compose`（`prompt_builder.py:86-97`） | 只用于 system 段拼接（`\n\n---\n\n` 分隔符） | 分隔符语义不同（`\n\n` vs `\n\n---\n\n`），不复用 |

结论：3 行重复 > 新建 1 个函数的收益是**边际的**。因此本文把 `render.py` 标为 **P2 可选**：W2 只要求「两处调用方都不得重排 items」，并在测试里钉死该约束（§7 T-4）。若协调者认可，再抽函数（净减 6 行、增 1 文件）。

### 3.5 `provider.scopes` 与 scope 隔离（现有字段，未消费）

现状：hub 从不读 `provider.scopes`（`hub.py:78-117`），`PreRunContextProvider` 声明 `{"session","study","goal"}`（`context_providers.py:53`）形同虚设。风险是「study 专属 provider 注册进 chat hub 后串域」。

协议冻结下的落法（不需要新字段）：`ContextQuery` 已有 `session_id/study_id/goal_id`（`protocol.py:26-28`），hub 可从 query **推断**本次作用域集合，再与 `provider.scopes` 求交：

```python
def _active_scopes(query: ContextQuery) -> frozenset[str]:
    if query.study_id:
        return frozenset({"study", "session", "global"})
    if query.goal_id:
        return frozenset({"goal", "session", "global"})
    return frozenset({"session", "global"})
# provider 被跳过时记 dropped: {"provider": n, "reason": "scope: study not in session"}
```

注意：这是 W2 的**可选**项。当前两个 provider 的 scopes 都含 `session`，开启后零行为变化（可作 T-5 回归）。真正的收益在 study 接入 provider 时。

---

## 4. 依赖与取数

### 4.1 预算从哪来（不改协议）

讨论稿建议 `ContextQuery.budget_hint`（`context-hub-discussion.md:101`）——**不采纳**，因为那要改协议。改用 `ContextHub.build()` 的关键字参数（`build` 不是协议面，`protocol.py:52-61` 只定义 provider）：

```python
def build(self, query: ContextQuery, *,
          budget_tokens: int | None = None,
          policy: ContextPolicy | None = None) -> ContextBundle: ...
```

默认 `budget_tokens=None` → **不仲裁**（与 W0 完全兼容，现有 29 条 hub 测试全部照旧）。

### 4.2 预算数值的唯一来源

| 候选 | 位置 | 可用性 | 裁定 |
|---|---|---|---|
| `LLMConfig.model_context_tokens` | `config.py:164` | ✅ 真实字段 | **唯一来源** |
| `client.config.model_context_tokens` | `openai_client.py:257` 起的 `self.config` | ✅ 可达 | 传入路径（客户端无直接属性） |
| `client.model_context_tokens` | — | ❌ **不存在**（实测 `dir(OpenAICompatClient)` 无此属性） | **禁止使用** |
| `CompactConfig.threshold_tokens` | `compact.py:170` | 压缩触发阈值，不是上下文预算 | 不复用（语义不同） |
| `RunBudget.plan_call` | `run_budget.py:38-52` | LLM **调用**门（INV-9），不是**上下文**预算 | 不复用（`invariants.py:94-110` 明确其职责） |

**已发现的现存缺陷（W2 不得继承）**：`session_controller.py:228-233`

```python
model_context_tokens=getattr(self._client, "model_context_tokens", None),   # ← 恒 None
model_max_output_tokens=getattr(self._client, "model_max_output_tokens", None),
```

对真 `OpenAICompatClient` 实测两者均为 `MISSING` → `make_compactor` 收到 `None` → `_resolve_threshold_tokens` 落 `fallback_threshold_tokens=8_000`（`compact.py:174, 738`）。即 chat 的压缩阈值实际是 8k tokens，而不是模型窗口的 80%。对照 AgentLoop 侧是正确的（`loop_compaction.py:58` 读 `self.config.model_context_tokens`，而 `self.config` 是 `LLMConfig`，`loop.py:384`）。

W2 的取数必须写成：

```python
cfg = getattr(client, "config", None)
ctx_tokens = getattr(cfg, "model_context_tokens", None)   # config.py:164
```

**该缺陷本身归 B1-c/协调者**（`session_controller.py` 不是 W-D 所有权），列入派单表（§5 的 R-3）。若不修，W2 的默认预算会恒为 `None`（安全退化：不仲裁），而不会出错——这是有意的失败方向。

### 4.3 「超限触发压缩」怎么落地

架构图：`预算谁产出谁拥有 · 超限触发压缩（runtime 摘要）`（`architecture-diagram.html:129`）。W2 只负责**发出信号**，压缩仍在 runtime（`compaction_runtime.py:31-101`，react 经 `ReactConfig.compactor` `react.py:88-101`）。

落法：仲裁后若 `accounting["over_budget"]`，hub 只在 bundle 里记录；**发事件是调用方的事**（hub 不持有 sink / 事件面，`hub.py:53-61` 的三条硬约束）。调用方（`session_controller._inject_context`）读取 `bundle.accounting` 后可 `self._sink("context.over_budget", ...)`。注意 chat 的事件桥默认丢弃未知 kind（`api/routers/chat.py:806-813`），若要前端可见需同时改白名单——**归契约 §2.3 缺口 E1 的同一处置**（`stage-c1-team-contract-20261028.md:97-103`），W2 不碰。

### 4.4 依赖图（W1/W2 的真实前置）

```
B-a  todo_tools.py:168 读 ctx.session_id        ─┐
D-2  role_factory 传 session_id                  ├→ W1 可见（chat react 能拿到 todo）
R-1  Gateway 注入 context_hub                    ─┘
R-3  session_controller 预算取数修正             ──→ W2 预算非 None
R-2  session_controller._inject_context 传 budget/policy ──→ W2 生效
```

---

## 5. 接线点（含所有权与派单）

### 5.1 `SessionController._inject_context` 怎么改（**请求协调者代改**）

文件 `core/gateway/session_controller.py` 不在 W-D 所有权内（契约 §5：W-D 是 `core/context/**` + `core/agent/context_providers.py`；gateway 归 W-P）。改动最小化：

```python
# session_controller.py:274-304 —— 只加两处
def _inject_context(self, messages, session_id):
    ...
    bundle = self._context_hub.build(
        ContextQuery(task_text=..., session_id=session_id),
        budget_tokens=self._context_budget_tokens(),   # 新增 ①
        policy=self._context_policy,                   # 新增 ②
    )
    if bundle.accounting.get("over_budget"):
        self._sink("context.over_budget", {           # 新增 ③（可选）
            "dropped": len(bundle.dropped),
            "tokens": bundle.accounting["total_tokens"],
        })
    ...
```

- ①② 在 hub 为默认参数时是 no-op，**零回归风险**。
- ③ 只是 sink，失败不影响主路径（现有 try/except 已包住，`session_controller.py:302-304`）。
- **`_inject_context` 本身已经是正确形态**（单块前置、异常降级、`last.role != "user"` 守卫），W1/W2 不需要重构它。

### 5.2 必须接线但当前完全缺失（R-1，最关键）

实测：

```
Gateway.__init__ 形参: ['self','client','system','sink','engine_factory','data_dir','registry','stream']
'context_hub' in 形参: False
gw.controller._context_hub: None
```

调用链：`api/routers/chat.py:827-847` → `_send_via_gateway`（`chat.py:684-729`）→ `_get_gateway()`（`chat.py:677-681`）→ `singleton.get_gateway()`（`singleton.py:18-56`）→ `Gateway(...)`（`singleton.py:50-53`）→ 无 hub。

**后果**：`QNODES_NEW_ENTRY` 默认 True（`entry_adapters.py:32-35`）→ 生产 chat 全走 Gateway 路径 → **goal/memory/todo 一个都不注入**。W0 的 `PreRunContextProvider` 与 B1-b 的注入只在旧 `/send_async` 路径（`service.py:932-944` → `build_chat_agent_loop` → AgentLoop hub）生效。

**派单建议**（R-1）：`Gateway` 增加可选 `context_hub` 形参并透传；`singleton.get_gateway()` 组装
`ContextHub` + `PreRunContextProvider`（需 `enable_goal_injection=True` 对齐旧路径，`chat.py:434`）+ `TodoContextProvider` 后注入。这是 W1 在 chat 可见的**硬前提**，且它不属于任何现有 worker 的显式条目 → **必须先问协调者**（契约 §0）。

### 5.3 派单表（跨所有权，本文不实施）

| ID | 文件 | 改动 | 为什么不能我做 | 阻塞 |
|---|---|---|---|---|
| **B-a** | `core/agent/builtin_tools/todo_tools.py:168` | `session_id` 兼容 `ctx.session_id` | 无明确 owner（契约 §5 未列）；`builtin_tools` 未分配 | W1 |
| **D-2** | `core/agent/role_factory.py:157-176` | 给 `AgentLoop` 传 `session_id` | 归 W-P（§5） | W1（study 侧） |
| **R-1** | `core/gateway/gateway.py:24-53`、`singleton.py:18-56`、`api/routers/chat.py:677-681` | 组装并注入 `context_hub` | 归 W-P / C | W1 |
| **R-2** | `core/gateway/session_controller.py:274-304` | `build(..., budget_tokens=, policy=)` + over_budget 事件 | 归 W-P | W2 |
| **R-3** | `core/gateway/session_controller.py:228-233` | 预算取数改 `client.config.model_context_tokens` | 归 W-P | W2 |
| **R-4** | `core/agent/prompt_builder.py:125-130, 196-206, 246-256, 281-287` | `build_messages` 零调用方（死契约）：删除或标 `@deprecated` | 文件未分配给 W-D | W2（避免误用） |

R-4 依据：全仓 `findstr /s build_messages src/` 只命中 `prompt_builder.py` 的 4 处定义（125/196/246/281），无任何调用点（`tests/` 亦无）。契约 §6.5 规则 2：「零生产调用方 = 死代码，发现即删」。

### 5.4 chat 与 study 两条链的收益

| 链 | 入口 | W1 收益 | W2 收益 | 前置 |
|---|---|---|---|---|
| **chat（Gateway/react）** | `chat.py:827` | **首次**拿到 todos（此前为零） | 预算兜底 | R-1 + B-a |
| **chat（旧 AgentLoop）** | `chat.py:526` → `service.py:932` | 无新增（`TodosInjector` 已工作） | 预算兜底 | R-3（可选） |
| **study** | `role_factory.py:157` → AgentLoop | **无收益**（`session_id=None` → 守卫挡住）；且 todos 语义不同（study 用 `todos.md`，`phase_engine.py:840`） | 无（loop 侧 hub 仍只挂 pre-run） | D-2 后才可能 |
| **swarm/workflow** | `executor.py:413-422` | 无（`session_id` 由 context 传入，非 chat 语义） | 无 | — |

**study 的 todos 不是 `TodoStore`**：study 用文件 `study/{id}/todos.md`（`bootstrap.py:252`、`phase_engine.py:840`），由 `review_loop.apply_todos` 写回（`review_loop.py:190-204`），经 `run_agent_via_llm(task=review_input)` 拼进**任务串**（`phase_engine.py:858`）而非上下文注入。**W1 不碰 study**——把 `todos.md` 也做成 provider 需要 study 侧传 `study_id` 且决定「文件 vs 内存」的优先级，属 W-S 的决定，且 `core/context` 不得 import study（`tests/test_architecture_gates.py:90-106`）。

---

## 6. 协议是否需要动

**结论：不动。**

| W1/W2 需要表达的东西 | 现有协议的表达方式 | 是否需要新字段 |
|---|---|---|
| todo 作为上下文 | `kind="todo"`，已在约定内（`protocol.py:35-37, 40`） | 否 |
| 预算 | `ContextHub.build(..., budget_tokens=)`——`build` 不是协议方法（协议只有 `provide`，`protocol.py:52-61`） | 否 |
| 裁剪时的同 kind 取舍 | `priority`（`protocol.py:44`） | 否 |
| 跨 kind 顺序 / 保护类 | 调用方传 `policy`（`build` 形参） | 否 |
| 分区标记（stable/directory） | `meta: dict`（`protocol.py:49`），如 `meta["section"]="directory"` | 否 |
| 每 provider 配额（谁产出谁拥有） | `per_provider` 账本（`hub.py:114-117`）+ 调用方 `provider_caps` | 否 |
| scope 隔离 | `query.study_id/goal_id`（`protocol.py:26-28`）+ `provider.scopes`（`protocol.py:57`）推断 | 否 |
| per-iteration 上下文 | 不需要：`build` 可重复调用；`query.iteration` 已存在（`protocol.py:27`） | 否 |

**唯一「本来想改但改成不改」的点**：讨论稿的 `budget_hint` 与 `prior_kinds`（`context-hub-discussion.md:101`）。`budget_hint` → 移到 `build()` 关键字参数；`prior_kinds` → **不需要**（W0 的整链桥接已经不拆链，`context_providers.py:4-9`，逐 provider 独立化推迟到 W3 且届时可用 `query.iteration` + provider 内部状态解决）。因此冻结协议是**够用的**，不是勉强够用。

---

## 7. 文件级改动清单

### 7.1 W-D 直接改（所有权内）

| 文件 | 改动 | 规模（估算） |
|---|---|---|
| `src/agentnodes/core/agent/context_providers.py` | 新增 `TodoContextProvider` + `_todo_priority()`；`__all__` 增补 | +45 行 |
| `src/agentnodes/core/context/hub.py` | `build()` 增 `budget_tokens` / `policy` 关键字；新增 `_arbitrate()`、`_active_scopes()`；`accounting` 增 `budget/over_budget/per_provider.over_cap` | +70 行 |
| `src/agentnodes/core/context/policy.py` | **新建**：`ContextPolicy`（frozen dataclass：`kind_rank`/`protected_kinds`/`provider_caps`）+ `DEFAULT_KIND_RANK` | +40 行 |
| `src/agentnodes/core/context/__init__.py` | 导出 `ContextPolicy` | +2 行 |
| `src/agentnodes/core/context/protocol.py` | **零改动**（硬约束） | 0 |

### 7.2 测试（W-D 验收子集内，契约 §6.3）

| 文件 | 新增 |
|---|---|
| `tests/test_context_hub.py` | 预算裁剪序 / 保护类 / over_budget / 确定性 / `budget=None` 兼容 / scope 跳过 |
| `tests/test_context_providers_todos.py`（新） | 空列表 → 空贡献；无 session_id → 空；格式与 `TodosInjector` 逐字节一致；`priority` 映射；store 异常 → 降级 |
| `tests/test_b1_chat_react_wiring.py` | provider 注入后单块前置形状不变；系统消息跨 turn 字节稳定（cache 守卫） |

### 7.3 P2 可选（需协调者认可才做）

| 文件 | 改动 | 净收益 |
|---|---|---|
| `src/agentnodes/core/context/render.py`（新） | `join_items(items) -> str` | 消 2 处 3 行重复；+1 文件 |

### 7.4 明确不动

`core/context/protocol.py`、`core/agent/context_injector.py`（`TodosInjector` 保留）、`core/agent/context.py`、`core/agent/prompt_builder.py`（只派单 R-4）、`core/agent/loop.py`（W-P）、`core/gateway/**`（W-P，派单）。

---

## 8. less-is-more 调查表（契约 §6.5 规则 1）

写任何新代码前，先列查过的现有入口：

| 需求 | 查过的现有入口 | 能否复用 | 结论 |
|---|---|---|---|
| 读取 todos | `TodoStore.get`（`todo_tools.py:39-42`） | ✅ | 直接复用，不新造 store |
| 渲染 todos 块 | `_format_todos_snapshot`（`todo_tools.py:64-72`） | ✅ | 直接复用，**禁止**重写格式 |
| 注入 todos（多轮刷新） | `TodosInjector`（`context_injector.py:130-165`） | ✅ 保留 | 不替换、不复制；新 provider 只服务一次性快照 |
| 上下文收集 | `ContextHub.build`（`hub.py:71`） | ✅ | 扩展参数，不新建收集器（契约 §6.5 速查表） |
| token 估算 | `token_utils.estimate_tokens`（`token_utils.py:15-44`） | ✅ | hub 已用（`hub.py:25`），W2 沿用同一口径 |
| items → 文本 | `session_controller.py:298`、`loop.py:590-592` | ⚠ 2 处逐字重复 | 可选抽 `join_items`（§3.4）；**不**用 PromptBuilder |
| 消息组装（system+history+user） | `ContextBuilder.build_initial_messages`（`context.py:130-157`） | ✅ 已在用 | 不动 |
| 渲染器协议 | `PromptBuilder.build_messages`（`prompt_builder.py:125-130,196-206`） | ❌ **零调用方** | 死契约，**禁止**作为 W2 渲染器；派单 R-4 |
| 上下文预算 | `RunBudget.plan_call`（`run_budget.py:38-52`） | ❌ 语义不同（调用门 vs 上下文额度） | 不复用；预算从 `LLMConfig.model_context_tokens`（`config.py:164`）取 |
| 压缩触发阈值 | `CompactConfig.threshold_tokens` + `_resolve_threshold_tokens`（`compact.py:170,716-738`） | ❌ 不复用 | hub 只发 `over_budget` 信号，压缩仍归 runtime（`compaction_runtime.py`） |
| 事件发送 | `SessionController._sink`（`session_controller.py:56`） | ✅ | hub 不自带 sink（`hub.py:53-61` 硬约束），由调用方发 |
| policy 对象 | 无现成 | — | 新建 `ContextPolicy`（40 行 frozen dataclass）——**这是唯一的新概念**，且不可省（否则秩表只能硬编码进 hub = 业务判断入中枢） |

**净新增**：1 个 provider 类（45 行）、1 个 policy dataclass（40 行）、hub 内 2 个私有方法（70 行）。**零新格式、零新 store、零新渲染路径、零协议改动。**

---

## 9. 测试与基线比对

### 9.1 验收子集（契约 §6.3 W-D 行的子集，聚焦本刀）

```bat
set PYTHONPATH=D:\Github\agentnodes\src
D:\tools\python312\python.exe -m pytest ^
  tests/test_context_hub.py ^
  tests/test_context_providers_bridge.py ^
  tests/test_context_providers_todos.py ^
  tests/test_session_controller.py ^
  tests/test_b1_chat_react_wiring.py ^
  tests/test_context_injector.py ^
  tests/test_context_injector_extended.py ^
  tests/test_todo_tools.py ^
  tests/test_architecture_gates.py ^
  -q --tb=no --no-header > my.log 2>&1
D:\tools\python312\python.exe scripts\c1_gate.py my.log ^
  --files test_context_hub test_context_providers test_session_controller ^
          test_b1_chat_react test_context_injector test_todo_tools test_architecture_gates
```

**改动前实测基线（HEAD `755d9051`，本机 py312）**：

| 子集 | 结果 |
|---|---|
| 上述 8 个既有文件 | **107 passed / 0 failed**（4.16s） |
| 核心 5 文件 | **66 passed / 0 failed**（2.16s） |
| `c1_gate.py`（同一 slice） | `entries: 0`，`current failures: 0`，exit 0 |

说明：冻结基线 `tests/baselines/pr-lane-failures.json` 覆盖 103 条 node id（`counts.failed=96, error=7`，含 3 条 `post_freeze_additions`），其中 **W-D 子集内为 0 条**（`by_file` 里只有 `tests/test_study_todos_knowledge.py: 2` 沾 "todos"，不属本 slice）。因此 W-D 的绿条件是「本 slice 全绿」——比契约 §6.2 的集合包含更强，可执行。

### 9.2 新增测试清单（行为断言）

| ID | 断言 | 防的是什么 |
|---|---|---|
| T-1 | `TodoContextProvider` 输出与 `TodosInjector` 的块**逐字节相同** | 格式漂移（两套 todo 文本） |
| T-2 | todo 为空 / 无 session_id / store 抛异常 → `items == []`，hub 不抛 | 不变式 4（`hub.py:87-95`） |
| T-3 | **字节等价**：同一 registry + 同一 task，W2（`budget=None`）的渲染输出 == W0 的渲染输出 | 仲裁误改顺序/内容 |
| T-4 | `bundle.items` 顺序 == 渲染顺序；不得重排 | 渲染器私自排序 |
| T-5 | 预算过半 → 只丢低秩 item；`protected_kinds=("goal",)` 下 goal 永不丢 | 裁错对象 |
| T-6 | 同输入两次 build → `explain()` 完全相等（含 `dropped`） | 不变式 3 确定性 |
| T-7 | 单个 goal 超预算 → 保留 + `over_budget=True` + `dropped.reason=="budget_exhausted_protected"` | 静默清空上下文 |
| T-8 | scope 不含 query 推断域的 provider → 被跳过并记 `dropped`（默认配置下零变化） | 跨域串（study 内容进 chat） |
| T-9 | `provider_caps` 生效时 `per_provider[cap].over_cap is True` | 「谁产出谁拥有」不可观测 |
| T-10 | 系统消息在同一 registry 下跨 turn 逐字节稳定（含排序后的 tool_list） | cache 前缀被意外破坏 |

### 9.3 架构门禁（不需要改，但要跑）

`tests/test_architecture_gates.py:90-106`（`core/context` 零生产方 import）——`policy.py` / `render.py` 都只依赖 `protocol.py`，通过。`hub.py` 新增的 `_active_scopes` 只读 `ContextQuery` 字段，无 import。

---

## 10. 排序表：如果只能做三件事

| 优先 | 事项 | 理由 |
|---|---|---|
| **P0** | R-1（Gateway 注入 hub）+ B-a（todo session 归属） | 不做这两件，W1 的产出在生产上是**不可见的死代码**（契约 §6.5 规则 2 的定义） |
| **P1** | `TodoContextProvider` + T-1/T-2 | W1 唯一新增能力，且 chat 首次获得 todos |
| **P2** | `ContextPolicy` + `hub._arbitrate`（默认 `budget=None` 不启用）+ T-3~T-7 | W2 的机制面。**默认不启用**，保证零回归；由调用方显式传预算才生效 |
| **P3** | `_active_scopes`（T-8）、`render.join_items`、R-4 死契约清理 | 收口项，可独立排期 |

**明确不做**：拆逐条 todo item（§2.5）、把 `{tool_list}` 移出 system（§3.2-A）、`PreRunContextProvider` 的 priority 语义修正（改字节，留 W3）、study 的 `todos.md` 接入（属 W-S、跨域）、hub 渲染方法（违反开放问题 1）、协议改动。

---

## 附：本文的复验方式

```bat
:: 1) 协议冻结面
findstr /n "priority tokens scopes" src\agentnodes\core\context\protocol.py

:: 2) hub 不仲裁不渲染、不读 scopes
findstr /n "arbitrat render scopes" src\agentnodes\core\context\hub.py

:: 3) todos 三处定义（store / formatter / injector）
findstr /n "class TodoStore _format_todos_snapshot" src\agentnodes\core\agent\builtin_tools\todo_tools.py
findstr /n "class TodosInjector _last_hash" src\agentnodes\core\agent\context_injector.py

:: 4) 生产 chat 无 hub（形参 + 单例组装）
findstr /n "context_hub" src\agentnodes\core\gateway\gateway.py src\agentnodes\core\gateway\singleton.py
findstr /n "context_hub=" src\agentnodes\core\gateway\session_controller.py

:: 5) todo session 归属缺陷
findstr /n "kwargs.get(\"session_id\")" src\agentnodes\core\agent\builtin_tools\todo_tools.py
findstr /n "registry.execute" src\agentnodes\core\runtimes\nodes\react.py

:: 6) 预算取数缺陷
findstr /n "model_context_tokens" src\agentnodes\core\gateway\session_controller.py src\agentnodes\core\llm\config.py

:: 7) 死契约
findstr /s /n "build_messages" src\agentnodes\core\*.py

:: 8) 架构门禁
D:\tools\python312\python.exe -m pytest tests/test_architecture_gates.py -q
```
