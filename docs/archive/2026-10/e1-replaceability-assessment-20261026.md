# E1 可替换性评估 — 2026-10-26

> 问题：E1 新增的 `core/orchestration/` 能替换现有哪些代码？
> 方法：勘查现有实现 → 逐项比对职责 → 判定「可替换 / 只能部分 / 不可替换」+ 替换刀号。
> 原则：**E1 不改动现有代码**（纯增量）；替换一律留到对应刀号，且每步可回退。

---

## 1. 勘查到的现有实现

| 现有实现 | 位置 | 职责 |
|---|---|---|
| **Steering inbox** | `core/agent/loop.py:775-800`（`inject/ainject/_drain_inbox`，asyncio.Queue） | 运行中注入消息，在每轮 LLM 调用前 drain（mid-loop steering） |
| **Session 队列** | `api/session/service.py:73/193/251/276`（`_session_queues: dict[str, asyncio.Queue]` + `_process_session_queue`） | per-session FIFO 串行处理消息 |
| **Steering 相关测试** | `tests/test_steering_inbox.py`（P2-B） | 上述 inbox 的行为测试 |
| **预算评估** | `core/agent/strategy/budget.py`（`BudgetLimits/BudgetUsage/evaluate_budget`，tokens→cost→time） | run 级三维预算（含成本） |
| **写入限流** | `core/session/rate_limiter.py`（滑动窗口，80000 条/秒） | **写入**限流，非 LLM 调用限流 |
| **seq 生成器** | `core/seq_generator.py`（per-session 单调 seq，内存+锁） | 消息 seq 分配（event_log 的 seq 前身） |
| **event_store** | `core/agent/event_store.py` | 事实流（E1 不碰，A2/0.4 才动） |

## 2. 逐项判定

### 2.1 Steering inbox → **可被 mailbox 替换（B1/A4 阶段）**
- **重叠度：高**。inbox 的语义（运行中注入、下一次 LLM 调用前生效、FIFO）= 我们的 steer 细则 1/4。
- **差异**：inbox 是**进程内 asyncio.Queue、无队列模式、无 cap、无 deadline、无降级事件、无跨 turn 类型**。
- **替换条件**：A4（ContextHub 注入通道）实现后，`Mailbox` 的 steer 分支提供 `drain_for_run(run_id)` 接口 → inbox 删除。
- **风险**：inbox 被 AgentLoop 直接持有（`self._inbox`），替换需要 A1 引擎改造同步。
- **判定**：✅ 可替换，**留到 A1/A4**，不现在动。

### 2.2 Session 队列 → **可被 mailbox 替换（E2/A2 阶段）**
- **重叠度：高**。`_session_queues` 做的正是 mailbox 的核心职责：per-session 串行 FIFO。
- **差异**：现实现只有 FIFO（无 interrupt/steer/cap/老化/事件），且与 `_process_session_queue` 协程紧耦合。
- **替换条件**：A2 SessionController 就位后，service 层改为 `mailbox.submit(request)` + 消费 `poll_due()`。
- **风险**：`_process_session_queue` 里嵌了 SSE 推送与错误处理逻辑（不只是排队），替换需要把这些迁到事件流（本就是 0.2 的目标）。
- **判定**：✅ 可替换，**留到 E2/A2**，是四个入口迁移之一。

### 2.3 预算评估 → **并存，语义互补（A1）**
- **重叠度：部分**。`budget.py` = **run 级**（tokens/cost/time，含 USD 成本）；`budget_gate.py` = **session/chat 层**（tokens/calls/wallclock，无成本）。
- **关键差异**：`budget.py` **有 cost_usd 维度**（需要 model 价格表）；我们的没有——**成本维度是有价值的，不该丢**。
- **结论**：❌ **不替换，改为分层**：A1 时让 `budget_gate` 委托 `budget.py` 做 run 级三维检查，`budget_gate` 只保留 session 层缺省合并。两者合并为一条调用链，而不是互相替换。
- **判定**：⚠️ 部分整合，A1 定接线。

### 2.4 写入限流 → **不替换（不同层）**
- **不重叠**：`rate_limiter.py` 限制的是**写入数据库**的速率（保护 DuckDB/sqlite），不是 LLM 调用。
- 我们的 LLM RPM/TPM 限流在 ProviderHub client 层（A1），与它无关。
- **判定**：❌ 不替换，各自保留。

### 2.5 seq 生成器 → **可被 P-4/event_log 的 seq 替换（0.4 阶段）**
- **重叠度：概念高、实现不同**。`seq_generator` 是**消息** seq（per session，内存、重启归零、靠 UNIQUE 索引兜底）；我们要的是 **event seq**（event_log 内单调，DB 权威）。
- **差异**：一个是「防止 seq 冲突的安全网」，一个是「事件顺序的权威」。语义不同但**同一个概念轴**。
- **替换条件**：0.4 数据面落地（每 session 分库 + event_log 权威 seq）后，`seq_generator` 退化为兼容层或删除。已有 `backfill_seq.py` 脚本可复用为迁移工具。
- **判定**：✅ 可替换，**留到 0.4**。

### 2.6 不可替换但有对照价值的

| 现有 | 判定 |
|---|---|
| `event_store.py`（事实流） | E1 不碰；0.4 才改存储形态（分库），接口保留 |
| `sse_buffer.py` / `bridge_v2.py`（SSE 双轨） | 0.2 目标，与 E1 无关 |
| `loop.py` 的 7 mixin / 9 step | A1/B1 迁移为 GraphEngine 节点 |
| `core/session/db.py` | 0.4 拆分（session 库 + 全局库） |

## 3. 替换路线（按刀号汇总）

| 刀号 | 替换动作 | 替换对象 | 回退方式 |
|---|---|---|---|
| **A1** | `budget_gate` 接线 `budget.py`（分层，不删旧） | 无删除 | 断开接线 |
| **A2** | Session 队列 → mailbox | `api/session/service.py` `_session_queues` | 保留旧路径开关 |
| **A4** | Steering inbox → mailbox steer 分支 | `loop.py` `_inbox` | 保留 inbox 字段 |
| **0.4** | 消息 seq → event_log 权威 seq | `core/seq_generator.py` | 兼容层保留 |
| **E2** | 四入口改走 EntryPoint | routers/scheduler/cli/mcp | 逐入口切换 |

## 4. 替换的净收益（量化）

| 替换 | 删除代码量（估） | 收益 |
|---|---|---|
| Session 队列 → mailbox | ~80 行（service.py 队列逻辑） | 队列语义统一，获得 cap/老化/interrupt |
| Steering inbox → mailbox | ~30 行（loop.py inbox） | steer 有事件可见、有降级语义 |
| seq_generator → event_log | ~100 行 + 一个脚本 | 单一顺序权威，去掉「重启归零靠索引兜底」的脆弱点 |
| **合计可删** | **~210 行** | 三个「同一概念的两套实现」归一 |

## 5. 重要发现（本次勘查新增）

1. **现有系统已有 steering inbox**（P2-B 实现）——这不是新需求，是**已有能力的收敛**。A4 替换时行为要对齐现有测试（`test_steering_inbox.py` 30+ 用例）——**这是替换的验收基线，比我新写的测试更权威**。
2. **现有预算有 cost 维度**（USD），我们的没有——**不应丢掉**，A1 分层整合。
3. **seq_generator 的重启归零缺陷**在文档里被明确承认（"On process restart, counters reset to 0"），靠 UNIQUE 索引兜底——**这正是 P-1 事件 seq 要解决的问题**，替换有实质收益而非文档搬家。

## 6. 结论

**E1 的替换面清晰且全部留到正确刀号**：
- 三个「同一概念两套实现」可归一（session 队列 / steering inbox / seq 生成器），净删约 210 行
- 预算不是替换而是分层整合（保留 cost 维度）
- 写入限流不同层，保留

**当前 E1 代码零替换、零删除——纯增量，符合「每步可回退」纪律。**
