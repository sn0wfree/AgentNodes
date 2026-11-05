# 统一入口设计 — 借鉴 OpenClaw 的 Gateway 模式

> 关联：`industry-benchmark-openclaw-20261008.md` · `orchestration-triad-design.md` · `architecture-v3-openclaw-inspired.md`
> 性质：讨论 + 提案（待拍板）

---

## 0. 结论

> **入口不统一，后面「1 控制面 + N runtime」就是空谈——因为每个入口都有自己的控制面。**
> 正确顺序：先统一入口，再统一控制面，最后换 runtime。

---

## 1. 现状：22 个启动点，15 个文件，8 种启动方式

### 实例化点计数

| 类 | 实例化处数 | 分布 |
|---|---|---|
| SwarmRuntime | 7 | cli/swarm · goal/workflow · mcp/server · swarm/runtime · … |
| AgentLoop | 7 | chat_loop · executor · loop(自身) · role_factory · … |
| AgentExecutor | 5 | langgraph_engine · swarm/runtime · workflow/agents · subagent_tool |
| AutoresearchRunner | 2 | study/runner · study/scheduler |
| SessionService | 1 | api/dependencies |

### 8 种启动方式（彼此不兼容）

| 入口 | 启动方式 | 并发控制 | 结果获取 | 事件出口 |
|---|---|---|---|---|
| CLI cmd_run | 直接调函数 | 无（同步） | 返回值 | print |
| CLI chat | `AgentLoop.run()` | 无 | LoopResult | print / trace |
| FastAPI /send | SessionService → create_task | asyncio.Queue | dict (poll) | event_bus.emit |
| FastAPI /study | bootstrap → scheduler | Semaphore | poll study 状态 | event_bus.emit |
| FastAPI SSE | event_bus → SSE | — | 流 | SSE |
| MCP tool | AgentExecutor | 无 | str | — |
| cron/automation | scheduler._loop | Semaphore | poll | event_bus.emit |
| subagent tool | AgentExecutor | 无 | str | — |

**后果**：每加一种来源（渠道/自动化/CLI 子命令），都要重写一遍「入队 + 并发 + 事件 + 结果」——这正是「各做各的」。

---

## 2. OpenClaw 的统一入口设计

OpenClaw 只有一个入口协议：

```
所有来源（渠道 20+ · CLI · Web Admin · Automations · WebChat · Nodes）
    ↓ 全部走同一条 WebSocket 连接
Gateway（单进程 · 单端口 18789 · 一个信任域）
    ↓ 同一套 typed 协议
Session Controller（唯一准入 owner · 队列模式按来源选）
    ↓ 同一条 agent RPC
Agent Runtime（可换 harness）
    ↓ 同一条事件流
Event Log（单一事实源）
```

### 它统一了什么（精确列出）

| 统一点 | OpenClaw 做法 | 我们的现状 |
|---|---|---|
| **传输** | 一种 WS 协议（typed req/res/event） | REST / WS / MCP / CLI 各走各的 |
| **会话身份** | `sessionKey` 统一跨渠道（一个 WhatsApp 消息和一个 CLI 命令是同一个 session 的两个 turn） | session_id / study_id / attempt_id 各自独立 |
| **工作提交** | **一种** `agent` RPC（唯一启动方式） | **8 种**（见上表） |
| **结果获取** | `agent.wait` 统一等待终态 | dict / LoopResult / str / poll / 流 |
| **事件流** | 统一 `event:agent` 流（assistant/tool/lifecycle 三通道） | event_bus.emit / SSE / trace 各写各的 |
| **并发** | Session Controller 统一准入（全局容量组 + 队列模式） | asyncio.Queue / Semaphore / 无（三套） |
| **幂等** | 副作用方法要求幂等键（短时去重缓存） | **无**（重试会重复） |
| **准入拒绝** | 结构性拒绝（并发满/预算尽/配对未通过） | 部分有（Queue 上限），部分没有 |
| **配对/身份** | 设备身份 + 配对审批 + 角色上限 | session_id / attempt_id NewType（无跨源身份） |

### 它没有统一的（**同样重要**）

| 没有统一 | 为什么 |
|---|---|
| 内部实现 | runtime 内部怎么跑是 runtime 自己的事 |
| 存储后端 | sqlite / 状态目录是实现细节 |
| channel 协议 | WhatsApp/Telegram 的接入由插件处理，入口统一但渠道实现自由 |
| 查询接口 | 只读查询不需要排队 |

---

## 3. 我们的统一入口设计

### 3.1 统一「工作提交」契约

```python
# core/orchestration/entry.py

@dataclass(frozen=True)
class WorkRequest:
    """所有来源启动一次工作的统一请求。"""
    kind: str                      # chat_message | study_round | dag_batch | adhoc_tool
    source: str                    # cli | api | mcp | webui | automation | subagent
    session_key: str               # 统一会话身份（跨来源）
    payload: dict                  # kind 特定内容
    queue_mode: str = "followup"   # steer | followup | collect | interrupt
    budget: BudgetLimits | None = None
    idempotency_key: str | None = None  # 副作用方法可安全重试


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    session_key: str
    status: RunStatus              # 6 个正交真值
    def wait(self, timeout_s: float = 30) -> "RunResult": ...


class WorkEntry(Protocol):
    """所有来源启动工作的唯一入口。"""
    def submit(self, req: WorkRequest) -> RunHandle: ...
    def subscribe(self, session_key: str) -> AsyncIterator[Event]: ...
    def cancel(self, handle: RunHandle) -> bool: ...
    def status(self, handle: RunHandle) -> RunStatus: ...
```

**`WorkRequest.kind` 决定 runtime**（经选择器）：

| kind | runtime | 说明 |
|---|---|---|
| `chat_message` | ReActRuntime | 一次对话回复 |
| `study_round` | ResearchRuntime | 一轮研究 |
| `dag_batch` | DagRuntime | 多 agent 批次 |
| `adhoc_tool` | ReActRuntime（受限 toolset） | 一次性工具调用 |

**`queue_mode` 由来源决定**（不是 kind）：

| 来源 | 默认 queue_mode | 理由 |
|---|---|---|
| CLI 命令 | `followup` | 排在当前工作后面 |
| API chat 消息 | `steer` 或 `followup` | 取决于 UX 设计 |
| API study 启动 | `followup` | 长批 |
| Automation / cron | `interrupt` | 预约的应该优先 |
| Director 的 propose | `followup` | 自主提案排在人给之后 |
| MCP tool call | `followup` | 排在当前轮后面 |

### 3.2 统一「观察」契约

```python
class WorkEntry(Protocol):
    ...
    def subscribe(self, session_key: str) -> AsyncIterator[Event]: ...
```

**事件不重放，缺口靠刷新**（OpenClaw 的明确选择）。客户端断线重连时，调 `status()` 拿当前快照，再从事件流续听。

**快照**：统一快照（items/dropped/providers_used/capabilities/events_ref）从 `ContextHub` 产出，**从事件 fold**（不建独立存储）。

### 3.3 统一「控制」契约

```python
class WorkEntry(Protocol):
    ...
    def cancel(self, handle: RunHandle) -> bool: ...
    def pause(self, handle: RunHandle) -> bool: ...
    def resume(self, handle: RunHandle) -> bool: ...
    def status(self, handle: RunHandle) -> RunStatus: ...
```

**取消语义**（对齐 OpenClaw）：取消立即撤销 run 的工具和审批权限；终态记账仍由 lifecycle owner 结算。

---

## 4. 迁移路径

| 阶段 | 内容 | 验收 | 估工 |
|---|---|---|---|
| **E1** | 写 `WorkRequest` / `RunHandle` / `WorkEntry` 协议 | 协议文件 + 单测 | 1d |
| **E2** | SessionService 的 send_message 改为 `submit(chat_message)` | 行为等价 | 1.5d |
| **E3** | scheduler 的 study 启动改为 `submit(study_round)` | 行为等价 | 1d |
| **E4** | CLI / MCP / subagent 改为走 `submit()` | 全部入口走统一路 | 1d |
| **E5** | 幂等键 + 去重缓存 | 重试不重复 | 0.5d |

**总估工 ≈ 5d**，但**每一步都可独立合入**（E1 先建协议，E2–E4 逐个迁移入口）。

---

## 5. 统一后的收益

| 维度 | 现状 | 统一后 |
|---|---|---|
| 加一个新来源 | 写一套「入队+并发+事件+结果」 | **一个 `WorkRequest` + 一个适配器** |
| 加一种新工作类型 | 改所有入口 | 加一个 `kind` + 一个 runtime 实现 |
| 重试安全 | 无（重复执行） | 幂等键 + 去重缓存 |
| 取消/暂停 | 三套语义 | 一套 |
| 事件流 | event_bus.emit / SSE / trace 各写各的 | 一个 `subscribe(session_key)` |
| 并发控制 | asyncio.Queue / Semaphore / 无 | SessionController 统一准入 |
| 审计 | 各入口各自记 | 从 `subscribe()` 流自动投影 |

---

## 6. 与 OpenClaw 入口的逐项对照

| OpenClaw 概念 | 我们的对应 | 状态 |
|---|---|---|
| Gateway（单进程 · 单端口 · 一个信任域） | FastAPI 进程 + SessionController | ⚠️ FastAPI 是入口之一，不是唯一入口 |
| `agent` RPC（唯一启动方式） | **8 种启动方式** | ❌ E1–E4 修 |
| `agent.wait`（统一等待终态） | 各自 poll / LoopResult / str | ❌ E1 修 |
| `sessionKey`（跨渠道统一会话身份） | session_id / study_id / attempt_id 各自独立 | ❌ E2/E3 修 |
| 事件流（assistant/tool/lifecycle 三通道） | event_bus / SSE / trace 各写各的 | ❌ E2/E4 修 |
| 幂等键（副作用方法可安全重试） | **无** | ❌ E5 修 |
| 「事件不重放，缺口靠刷新」 | event_store 有 replay 但 SSE 无此约定 | ⚠️ E2 补 |
| 队列模式（按来源选） | 仅 SessionService 有 Queue | ❌ E3 补 |

---

## 7. 一句话

> **入口不统一，一切收敛都是空谈**——因为每个入口自带一套控制面、一套并发控制、一套事件流。
> OpenClaw 的答案不是「把入口收窄」，而是**把「启动一次工作」变成一个协议**（`WorkRequest → RunHandle → Event`），
> 无论来源是 WhatsApp、CLI 还是 cron，走的都是同一条路。
>
> 对我们来说，这意味着：**先定 `WorkEntry` 协议（1d），再逐个迁移入口（4d）**——
> 这 5 天是「1 控制面 + N runtime」成立的前提，也是消除「各做各的」的根本手段。
