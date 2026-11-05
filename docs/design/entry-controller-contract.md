# 统一入口契约（v6）—— 协议端点 + mailbox — 2026-10-26

> 版本：v6（取代 v5.1 的 place()/WorkItem/五态回执设计——入口层塌进 Gateway，见 `platform-data-plane`/纪要线索 15）。
> 配套：架构图 v6.2（`architecture-diagram.html`）· queue_mode 定稿（`queue-mode-research-20261024.md`）· P-4 身份（`p3-p5-data-plane-design.md`）。
> 状态：**定稿，E1 实现依据**。实现 = `core/orchestration/contracts.py` + mailbox + 一致性 pytest。

---

## 1. 一句话与职责

**收下即进 mailbox，其余一切走事件流；回执只有 accepted / rejected(reason) 两态。**

| 职责 | 归属 | 明确不做 |
|---|---|---|
| 契约校验 + 幂等去重 + deadline 检查 | 协议端点（Gateway 内，薄·无状态·快同步） | 不做任何 turn 裁决 |
| mailbox（lane 串行 + steer 邮箱 + cap 20 + 溢出 summarize + 优先级老化） | mailbox | 只排序不裁决 |
| turn 准入 / run 签发 / fence | SessionController（A2） | 不见协议字段 |
| 事件扇出 / 订阅 | EventStore | 不产生、不改写事实 |

边界方法**快同步**（非阻塞、无 IO）；异步性只出现在事件流订阅——CLI 同步与 API asyncio 共用一份契约。

## 2. WorkRequest（唯一对外契约）

```python
@dataclass(frozen=True)
class WorkRequest:
    kind: WorkKind                  # chat / study / automation / director_propose
    source: SourceKind              # cli / api_chat / scheduler / mcp / director
    session_key: str                # 逻辑会话名（chat:main · study:momentum）
    payload: Mapping[str, Any]      # 按 kind 窄化（渠道 adapter 完成窄化与白名单过滤）
    queue_mode: QueueMode           # steer / followup / collect / interrupt
    budget: BudgetSpec | None       # None = 用会话默认
    idempotency_key: str | None     # 副作用安全重试（E5 启用去重）
    deadline: datetime | None       # 排队过期时刻，到点 → EXPIRED 事件
    execution: ExecutionSpec | None # 并行说明书字段位（B1 启用；v1 恒 None）
```

**payload 白名单不变量**：WorkRequest 不得携带任何协议概念（HTTP/SSE/CLI 细节在渠道 adapter 剥除）。

### 2.1 每源默认 queue_mode（定稿五行）

| 来源 | 默认 | 说明 |
|---|---|---|
| CLI / TUI | followup | 人不急时排队 |
| api_chat（含 WebUI） | steer | 业界 6/7 默认；v1 仲裁器=steer 转 followup（注入能力 A4 后开） |
| scheduler / automation | interrupt | 取消当前 turn 优先执行 |
| MCP | followup | 对外工具协议，保守排队 |
| director | followup | 提案不抢人的 run |
| （枚举位）collect | v1 拒绝 | UNSUPPORTED_QUEUE_MODE；启用条件见调研 §4 |
| 用户显式 cancel | interrupt | 唯一允许用户触发 interrupt 的口 |

### 2.2 steer 细则（五条，A4 启用后生效）
1. 注入点 = 下一次模型调用 / runtime 声明的安全边界
2. `injectable=false` → 显式转 followup + 事件可见（队列仲裁，非静默降级）
3. 注入只允许追加（目录区之后），不重排
4. steer mailbox FIFO
5. 同一安全点多条注入合并为一批渲染

## 3. 回执与会话解析

```python
@dataclass(frozen=True)
class Receipt:
    accepted: bool
    reason: RejectReason | None   # rejected 时必填
    session_id: str               # 惰性创建后回填的实际物理会话 id
```

- **两级拒绝**：入口拒绝（MALFORMED / UNSUPPORTED_QUEUE_MODE / IDEMPOTENCY_CONFLICT）无副作用直接返回；后续命运（准入/排队/降级/过期）**全部走事件流**，不再同步返回。
- **session 惰性创建**：首次提交自动物化 session（新建 `sessions/<id>.sqlite`），全局登记索引记 `session_key → session_id` 映射；回执带 session_id。
- **deadline**：排队项到点 → EXPIRED 事件；**排队项不表现为 executing run、不继承 run 超时**。

## 4. 身份（引用 P-4）

`session_id`(uuid 文件名) → `turn_id`=`{sid}#{n}`（控制器准入签发，E1 阶段不产生）→ `run_id`=`{turn_id}:r{m}`（1:N）→ 子 run/artifact 派生。**turn:run=1:N；同一 turn 至多一个活跃 run（不变量 8）**。幂等键与身份正交（E5）。

## 5. 边界不变量（一致性 pytest 验收）

1. 回执只有 accepted / rejected(reason)，无第三态
2. 入口拒绝无副作用（不建文件、不进 mailbox、不发事件）
3. 同幂等键同载荷 → 等价处理；同键异载荷 → IDEMPOTENCY_CONFLICT
4. collect → UNSUPPORTED_QUEUE_MODE 拒绝（枚举位保留）
5. WorkRequest 无协议字段（白名单断言）
6. rejected / EXPIRED / 降级必有事件，无静默
7. QUEUED 项到 deadline → EXPIRED，不继承 run 超时
8. 边界方法快同步（不 await 网络/磁盘大 IO；mailbox 操作 O(1)）
9. mailbox cap 20 溢出 → summarize 策略（拒绝点唯一）
10. payload 白名单（不变量 5 的断言实现）

## 6. 生命周期接线（谁在何时消费 mailbox）

E1 交付 mailbox **数据结构与端点**；消费者按刀号接入：A2 SessionController 从 mailbox 取队头准入；A4 开 steer 注入；E5 启用幂等去重存储。E1 阶段队列项由测试消费者驱动。

## 7. 与 v5.1 的差异清单

| v5.1 | v6 |
|---|---|
| place(WorkItem) 唯一写通道 | mailbox 直收（排序预留先于载荷） |
| AdmissionReceipt 五态 | 两态 accepted/rejected |
| handle_id（入口签发）/ 双签发外显 | 取消 handle；turn_id 由控制器准入签发（P-4） |
| ItemPhase 投影 | status 直接从 event_log fold |
| place() 的结构保证（Director 不得自行开 run） | 改述：Director 以 followup 提交 mailbox，唯一开 turn 路径 = admission |

## 8. 一致性测试清单（→ tests/test_entry_contracts.py）

回执两态 · 两级拒绝无副作用 · 幂等两分支 · collect 拒绝 · payload 白名单 · 事件必发 · deadline EXPIRED · 快同步 · cap 溢出 summarize · session 惰性创建回填 session_id。
