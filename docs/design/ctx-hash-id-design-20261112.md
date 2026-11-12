# ctx_hash_id 设计：内容绑定的上下文指纹（2026-11-12）

> **性质**：实施设计（讨论记录 + 终态方案，待批准后落地）
> **关联**：`loop-final-design-20261112.md`（统一引擎终态设计）·
> `loop-unification-design-20261111.md`（决策 D1-D9 与批次）·
> `loop-engine-inventory-20261111.md`（现状实测）
> **一句话**：给每次 LLM 调用一个**内容绑定**的上下文指纹
> `ctx_hash_id = trace_id_system_hash_tools_hash_c_digest`——
> 前一段回答「哪次 run」，后三段回答「这一轮模型看到了什么」。

---

## §0 需求（原始）

> 在上下文变化的时候，id 也要变；跟随上下文整体变化；
> id 最好跟当前上下文绑定；要有利于追溯；性能上不能有影响。

**三个诉求的拆分**：

| 诉求 | 落地 |
|---|---|
| 上下文变 → id 变 | c_digest 段内容级哈希 |
| 跟当前上下文绑定 | system / tools / messages 三分量全覆盖 |
| 追溯 | 四段复合，run 级 + 轮级双维度；落 trace / 事件 / result |
| 性能无影响 | system/tools 复用现成 hash；c_digest 实测 1.5 μs/条 |

---

## §1 现状盘点（2026-11-12 实测）

### 1.1 已有 id 机制（避免重复造轮子）

| id | 层 | 粒度 | 值 | 与本设计关系 |
|---|---|---|---|---|
| `session_id` | 会话 | 一次会话 | 外部给 | 上层维度，**不进** ctx_hash_id |
| `run_id` | gateway（fencing）| `{turn_id}:r0`，一个 turn 多次运行共用 | 外部给 | 编排维度，**不进** |
| `trace_id` | observability ContextVar | **per HTTP request / agent turn** | 随机 12 hex | **进**（作为第 1 段）|
| `system_prompt_hash` | agent_telemetry | 每次 LLM 请求 | sha256[:16] | **进**（第 2 段）|
| `tools_hash` | agent_telemetry | 每次 LLM 请求 | sha256[:16] | **进**（第 3 段）|
| `history_hash` | agent_telemetry | 每次 LLM 请求 | sha256[:16]，**长度级指纹** | **不进**（精度不足，见 §3.2）|
| `ctx_hash_id` | **新增** | 每次 LLM 请求（一轮）| 内容派生 63 字符 | — |

### 1.2 trace 层的 id 机制（实测）

`foundation/observability/trace.py`：

```
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)

def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]      # 12 字符，随机
```

**注释原文**（L5）：`trace_id (per HTTP request / agent turn)`。

**设置点**（全仓仅 2 处）：
- `interfaces/api/session/service.py:601` → `_trace_id.set(attempt.attempt_id or uuid.uuid4().hex[:12])`
- `kernel/agent/loop.py:1095` → **兜底**，但它在 `_run_loop_core`（legacy）内部

**消费点**：`event_store.py:166/307`（事件自动注入）、`logging_setup.py:107`（日志）、
`hanging_events.py`（落库）、`event_store.py` 的 trace 合并。

### 1.3 发现的现存缺陷（必须先修）

**实测**：统一引擎（skeleton）路径上 `trace_id` 是 `None`。

```
运行前 _trace_id: None
运行后 _trace_id: None
```

**原因**：兜底代码在 `_run_loop_core`（legacy）内，skeleton 走不到：

```python
# loop.py:1093-1096
from agentnodes.foundation.observability.trace import _session_id, _trace_id
if not _trace_id.get():
    _trace_id.set(uuid.uuid4().hex[:12])
```

**影响**：**U5 之后生产主路径是 skeleton**——直接用 `AgentLoop` 的调用（不经 API）
其日志与事件**没有 request 级标识**（`event_store.py:167` 注入时拿到 None）。
走 API 的有（service.py 先设 attempt_id），所以此前未被发现。

**修法**：把兜底补到 `AgentLoop.run/arun` 入口（U5.5 已统一为唯一入口）。
**顺带修好这个缺陷**。

**✅ 已完成（2026-11-12）**：新增 `AgentLoop._ensure_trace_context()`，在 `arun()`
入口调用一次；legacy `_run_loop_core` 内的旧兜底**删除**（避免两处事实源）。
配套网 `tests/test_trace_context_fallback.py`（5 条）。

**断言视角的重要细节（实测）**：ContextVar 的 `set` 在协程内只改**子 context**，
调用方（主上下文）读到的仍是原值：

```
async 入口（arun）：循环内部 ✅ 拿到 / 调用方 ❌ 看不到
sync 入口（run）：  循环内部 ✅ 拿到 / 调用方 ❌ 看不到（to_thread 隔离 context）
```

**「循环内部拿得到」正是兜底的目的**——事件注入点（`event_store.py:167`）
与日志格式化器（`logging_setup.py:107`）读的就是循环内部那个 context。
所以测试断言必须从**循环内部**读（用 `on_event` spy 捕获），而不是从调用方读。

### 1.4 trace 文件的粒度（实测）

```
同一个 AgentLoop 实例跑两次：
  run1 answer='答案1'  trace_path='.../trace.jsonl'
  run2 answer='答案2'  trace_path='.../trace.jsonl'
  同一个 trace 文件? True

trace.jsonl: 14 条记录（两次 run 混在一起）
  字段: type(14) ts(14) session_id(4) iteration(10) task(2) ...
  id 类字段: 只有 session_id
```

**结论**：`TraceWriter` 生命周期 = AgentLoop 实例 = 多次 run 共用一个文件。
**run 级已有 `trace_id` 承担**，不需要另设聚合 id（见 §5.2 讨论点 6）。

---

## §2 三个已有哈希分量的实测精度

`agent_telemetry.py:201-207`（现成代码）：

```python
system_prompt_hash = hashlib.sha256(system_prompt.encode()).hexdigest()[:16]
tools_hash        = hashlib.sha256(tools_json.encode()).hexdigest()[:16]
history_fingerprint = "|".join(
    f"{m.get('role', '?')}:{len(m.get('content', ''))}" for m in messages[1:]
)
history_hash      = hashlib.sha256(history_fingerprint.encode()).hexdigest()[:16]
```

### 2.1 `history_hash` 是**长度级**指纹——实测坐实

```
base    content='AAAA' → fingerprint='user:1|assistant:4' → hash=05679494487c1214
changed content='BBBB' → fingerprint='user:1|assistant:4' → hash=05679494487c1214   ← 完全相同
```

**内容变了、长度没变 → hash 不变**。

**这是「必须加内容级分量」的实测依据**（`history_hash` 不足以当身份用）。

> 附：本次评估中我一度用错用例（`好的` vs `换内容但长度相同` 长度其实不同），
> 导致误判「A 档没缺口」。用等长用例复验后结论成立。

### 2.2 组合形态的可分解性（实测）

```
基线                      2df6fab26c21a9fb_56b2d2d114580203_4a50e11513b22b69
system 变了               bb704fdc7ef4af9a_56b2d2d114580203_4a50e11513b22b69  → system 段变
tools 变了                2df6fab26c21a9fb_6084b64afec8a160_4a50e11513b22b69  → tools 段变
messages 变了             2df6fab26c21a9fb_56b2d2d114580203_41bb8449bad039cc  → messages 段变
messages 长度同内容变      2df6fab26c21a9fb_56b2d2d114580203_bef76c035739ba59  → messages 段变 ✅
```

**差异来源一眼可辨**——这是复合 id 相对单一哈希的核心优势。

---

## §3 设计

### 3.1 终态形态

```
ctx_hash_id = f"{trace_id}_{system_hash}_{tools_hash}_{c_digest}"

a1b2c3d4e5f6_2df6fab26c21a9fb_56b2d2d114580203_d29548f711cda1e3
└─ trace_id ─┘└─ system ──┘└─ tools ───┘└──── c ─────┘
   12 字符        16           16            16
   总长 = 63 字符，4 段全 hex（'_' 分隔无歧义，可反解）
```

| 段 | 长度 | 来源 | 语义 | run 内变化 |
|---|---:|---|---|---|
| 1 `trace_id` | 12 | ContextVar（需补兜底）| 哪次 run | **不变** |
| 2 `system_hash` | 16 | 复用 `_trace_llm_request` 现成值 | system 身份 | 不变 |
| 3 `tools_hash` | 16 | 复用现成值 | 工具集身份 | 不变 |
| 4 `c_digest` | 16 | **新算**：逐消息内容哈希的摘要 | 本轮上下文 | **每轮变** |

### 3.2 c_digest 的定义

```python
def compute_c_digest(messages: list[dict]) -> str:
    """本轮对话历史的**内容级**指纹（16 字符 hex）。

    **不含 messages[0]（system）**：system 已被独立分量覆盖，再哈希是冗余；
    且含了会让 system 段与 c 段同时变——差异定位时看不出是 system 变了
    还是历史变了。三段职责必须正交。

    **为什么逐条哈希而不是整体 dumps**：
    - 整体 JSON 化在长对话下成本随消息量线性增长；
    - 逐条 sha256 是 O(n) 次小哈希，实测 1.5 μs/条；
    - **不做增量**：compaction 会**替换** messages 列表（不是追加），
      增量缓存会失效——这是实测发现的重要约束。

    **内容级而非长度级**：history_hash 用 `role:len(content)` 指纹，
    实测 'AAAA'→'BBBB'（等长）时 hash 完全不变，不足以当身份用。
    """
    parts = []
    for m in messages[1:]:
        tc = m.get("tool_calls")
        parts.append(
            hashlib.sha256(
                f"{m.get('role','')}\0{m.get('content','') or ''}\0"
                f"{json.dumps(tc, sort_keys=True, default=str)}".encode("utf-8")
            ).hexdigest()[:16]
        )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
```

### 3.3 什么时候算

**每次 LLM 请求前**——那一刻 `messages` 恰好是「送进模型的那份输入」。

```
迭代 N 的 LLM 请求前：
  ctx_hash_id = f"{trace_id}_{system_hash}_{tools_hash}_{c_digest}"
  ctx.ctx_hash_id = ctx_hash_id        # LoopContext（轮级）
  result.ctx_hash_id = ctx_hash_id     # 最后一轮（run 级出口）
  trace 记录带 ctx_hash_id
  事件载荷带 ctx_hash_id
```

**轮级而非 run 级**：诉求是「上下文变化时 id 也变」——同一 run 内上下文逐轮变化，
id 必须逐轮变。

---

## §4 性能（实测）

### 4.1 c_digest 的成本

| 消息条数 | 耗时 | 每条 |
|---:|---:|---:|
| 10 | 0.022 ms | 2.23 μs |
| 100 | 0.165 ms | 1.65 μs |
| 500 | 0.749 ms | 1.50 μs |
| 1000 | **1.507 ms** | 1.51 μs |
| 2000 | 3.092 ms | 1.55 μs |

**线性，1.5 μs/条**。1000 条消息 = 1.5 ms，相对一次 LLM 请求（~300 ms）= **0.49%**。

真实场景更轻：每轮 messages 只增长 1-2 条，且每轮只算一次。

**system_hash / tools_hash 复用现成值 → 零额外成本**。

### 4.2 体积

| 落点 | 影响 |
|---|---|
| `trace.jsonl` 记录 | 无压力（那里本来就带完整 `system_prompt` 文本）|
| 事件载荷（SSE 推前端）| 63 字符/条 —— 见 §6 讨论点 3 |
| `LoopResult` | 无压力 |

---

## §5 用途 → 粒度 → 输入范围

### 5.1 四个用途

| 用途 | 需要什么 | 本方案满足？ |
|---|---|---|
| 追溯「这次 run」 | run 级键 | ✅ 第 1 段 |
| 追溯「这轮上下文」 | 轮级内容键 | ✅ 第 2-4 段 |
| prompt cache 命中率诊断 | 前缀级 | ✅ **保守下界**：同 id ⇒ 必同 prompt ⇒ cache 必命中；id 不同 ⇒ 前缀可能同（不误报）|
| 按 id 过滤 trace | 记录带 id | ✅ 每条记录带 |

### 5.2 粒度决策：轮级 + run 级由 trace_id 承担

**「run」的定义**：

> **run = 一次 `AgentLoop.run()` / `arun()` 调用 = 处理一条用户消息 = 一个 agent turn。**
> 一个 run 内有多轮 iteration，每轮一次 LLM 请求，上下文逐轮增长。

```
一次 run（= 一个 turn，共享同一个 trace_id）
  ├── iteration 1:  ctx_hash_id = A_a1b2..._sys_tools_c1
  ├── iteration 2:  ctx_hash_id = A_a1b2..._sys_tools_c2
  └── iteration 3:  ctx_hash_id = A_a1b2..._sys_tools_c3
```

| 层级 | 由谁负责 | 需要新建吗 |
|---|---|---|
| **run 级** | `trace_id`（已存在，request/turn 级）| ❌ 已有，不重复造 |
| **轮级** | `ctx_hash_id`（新增）| ✅ 本设计 |

**结论**：不做 run 级聚合 id（会与 trace_id 重复）。

### 5.3 输入范围决策

| 输入 | 决策 | 理由 |
|---|---|---|
| system prompt | **必进** | 换 system ⇒ 模型行为变 |
| tools schema | **必进** | 换工具 ⇒ 可用动作变 |
| messages 内容 | **必进** | 对话事实源 |
| messages role | **必进** | role 变语义变 |
| tool_calls | **必进** | 影响下一轮 |
| trace_id | **必进** | run 级归属（§5.2）|
| iteration | 不进 | 同上下文的第 N 轮与第 M 轮，「上下文」相同 |
| tokens_used / cost | 不进 | 用量不是上下文 |
| temperature / model | 不进 | 属 loop 外配置（`LLMConfig`）；如需可另设「采样指纹」|
| session_id | 不进 | 会话维度已有独立 id |

---

## §6 讨论记录与决策

### 已定（用户 2026-11-12 拍板）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | `c_digest` 长度 | **16**（与 A 档三个 hash 一致；总长 63）|
| 2 | c 是否含 `messages[0]`（system）| **不含**（三段职责正交）|
| 3 | c 内部算法 | **逐条哈希 → 摘要哈希**，不做增量（compaction 会让增量失效）|
| 4 | 碰撞 | 16 字符（复合形式碰撞更弱：三段独立，任一不同则 id 不同）|
| 5 | 事件载荷带不带 | **带** |
| 6 | 轮级 / run 级 | **轮级**；run 级已有 `trace_id` 承担 |
| 7 | 与 `history_hash` 的关系 | **保留 `history_hash`**（长度级 diff 用途，与内容级 id 互补）；`ctx_hash_id` **不复用**它的值 |
| 8 | 形态 | **`trace_id + system_hash + tools_hash + c_digest`（`_` 分隔）**——复合 id 自包含：拿到一个字符串就知道「哪次 run」+「这轮什么上下文」|
| 9 | **A. trace_id 兜底** | **✅ 已做**（2026-11-12）：`AgentLoop._ensure_trace_context()` + 5 条网；顺带修好「统一引擎路径日志/事件无 request 级标识」的现存缺陷 |
| 10 | **B. 事件载荷长度** | **完整 63 字符**（实测：序列化 +1.72 μs/事件；字节 +83.7%，但与既有的 `raw` 字段同量级——不是新负担级别）。所有事件统一带，不做 text_delta 特例 |
| 11 | **C. `result.ctx_hash_id` 取值** | **永远是当前状态的 ctx_hash_id**（每轮更新，不是结束时挑一轮）。无任何 LLM 请求（无上下文证据）时保持空串，不伪造 |

### 待定（继续讨论）

| # | 决策点 | 选项 | 我的倾向 |
|---|---|---|---|
| A | 是否先补 `trace_id` 兜底到统一引擎路径 | 补 / 不补（用占位符）| **补**——顺带修「主路径日志无 trace_id」的现存缺陷 |
| B | 事件载荷带完整 63 字符还是只带后 48 字符 | 完整 / 后 48 | 完整（自包含性优先）；若前端体积敏感再议 |
| C | `LoopResult.ctx_hash_id` 取最后一轮 | 最后一轮 / 首轮 | 最后一轮（最新状态）|

---

## §7 落地清单（待批准后执行）

| # | 落点 | 改动 | 文件 |
|---|---|---|---|
| 0 | **补 trace_id 兜底** | ✅ **已完成**：`AgentLoop._ensure_trace_context()`（入口调用；legacy 旧兜底已删）+ 5 条网 | `loop.py` + `tests/test_trace_context_fallback.py` |
| 1 | 计算函数 | ✅ 新增 `compute_c_digest(messages)` | `agent_telemetry.py`（与现成三 hash 同处）|
| 2 | 组合 | ✅ 新增 `compose_ctx_hash_id(trace_id, system_hash, tools_hash, c_digest)` | `agent_telemetry.py` |
| 3 | LoopContext | ✅ 加字段 `ctx_hash_id: str = ""` | `strategy/loop_context.py` |
| 4 | LoopResult | ✅ 加字段 `ctx_hash_id: str = ""`（**当前状态**语义）| `loop.py` |
| 5 | 每轮计算 | ✅ `trace_llm_request` 内算 + 挂 `loop._current_ctx_hash_id`；llm_call step 同步到 ctx 与 ctx.result（**一处覆盖两条引擎路径**）| `agent_telemetry.py` + `strategy/steps/llm_call.py` |
| 6 | trace 记录 | ✅ `llm_request` entry 加 `ctx_hash_id` | `agent_telemetry.py` |
| 7 | 其他事件 | ✅ `agent_telemetry.emit`（**所有事件的最终出口**）统一附加 | `agent_telemetry.py` |
| 8 | 网 | ✅ `tests/test_ctx_hash_id.py` **13 条** | 新测试文件 |

---

## §8 验收断言

| # | 断言 |
|---|---|
| 1 | 同一上下文两次算 → **同 id**（确定性）|
| 2 | messages 内容改（**长度不变**）→ **c 段变**（这是选内容级而非长度级的关键）|
| 3 | tools 改 → tools 段变；system 改 → system 段变 |
| 4 | **跨进程一致**：子进程算的 id 与主进程相同 |
| 5 | 4 段全 hex、`split("_")` 可反解出 4 段 |
| 6 | 每轮 id 随上下文变（iteration 1 ≠ iteration 2）；同一 run 内 trace_id 段不变 |
| 7 | trace 记录里 `llm_request` 带 `ctx_hash_id` |
| 8 | **统一引擎路径 trace_id 不再是 None**（顺带修 §1.3 的缺陷）|
| 9 | 性能：1000 条消息 c_digest < 5ms |

**实施结果（2026-11-12）**：13 条网全绿；全量 **7244 passed / 114 skipped / 0 failed**；
batch_gate ALL GREEN（7358 ≥ 7178）。

**实施中踩到的三个坑（都是测试自身的错，记录备查）**：

1. **段下标写错**——段序是 `0=trace_id 1=system 2=tools 3=c_digest`，
   我最初用 `b[1]` 指 system（实际是 tools 位）。
2. **helper 没把 system 传进去**——测「system 变了」时 helper 始终用固定的
   `SYS` 算 system_hash → id 完全不变 → 断言「system 段应变」失败。
3. **跨进程用例两边消息不同**——c_digest 跳过 `messages[0]`，子进程用
   `[user, assistant]`、主进程用 `[system, user, assistant]`，跳过的不是同一条。

**这三条的共同点**：测试没从**实现的实际契约**导出，而是照着自己脑里的
契约写——与 V4 教训同构（R-009/R-018）。

---

## §9 明确不做

| 不做 | 理由 |
|---|---|
| c_digest 增量计算 | compaction 替换 messages（非追加）→ 增量缓存失效 |
| run 级聚合 id | `trace_id` 已是 run 级，不重复造 |
| 一个 trace 文件拆成每 run 一个 | 要改 `TraceWriter` 生命周期 = 改 AgentLoop 语义，超出本批 |
| 采样参数（temperature/model）进 hash | 属 loop 外配置；如需可另设「采样指纹」|
