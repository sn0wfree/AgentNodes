# GoalWorkflow × Gateway 会话协调设计（session execution lease）

> **日期**：2026-11-18 · **状态**：设计定稿（裁决 2 前置），**未实施**
> **裁决依据**：`project-architecture-review-and-refactor-plan-20261118.md` §8.3 第 2 项
> **性质**：只读语义矩阵 + 设计。实施批次（迁移 GoalWorkflow、并发回归）须在本设计确认后单独进行。

## 0. 结论摘要

现状互斥（`SessionService.is_session_processing` / `mark_session_processing`）在 Gateway 接管聊天入口后**已实际空转**：chat 生产入口全部迁至 Gateway，而 Gateway 不认 legacy 标记——goal 与 Gateway 聊天**当前可并发**，双方都无感知。按裁决 2：在 **Gateway 建立带 ownership token 的原子 session execution lease**，chat 执行与 goal `start` 生命周期共用；等待语义保持现状（等待不改拒绝）；不复用 `SessionFence.claim()`、不拿 `chat_session_state()` 快照当互斥。

## 1. 现状语义矩阵（实测，2026-11-18）

### 1.1 标记的真实读写点（`kernel/session/service.py`）

| 行 | 角色 | 内容 |
|---|---|---|
| :82 | 结构 | `_processing_sessions: set[str]`（进程内存 set） |
| :298-299 | **chat 写** | `if session_id not in ...: add` + **顺带启动 consumer**（仅此路径启动） |
| :584 | **chat 清** | consumer `finally: discard`；:593-594 滞留项重启时重新 `add` |
| :363/:391 | **goal 用 API** | `is_session_processing` 读 / `mark_session_processing` 写 |
| :372-388 | 副作用 | `get_available_actions` 把 set 映射为 UI 的 cancel/resume/send |

`git grep` 实测：`is_session_processing` 的**全仓消费方只有 `apps/goal/workflow.py:509`**；`send_message` **生产零调用**（§12.2 已记录，/send_async 已切 Gateway）。

### 1.2 GoalWorkflow.start 的互斥序列（`apps/goal/workflow.py:507-524`）

```text
status="running" + emit workflow_start        ← 在获取互斥【之前】
while is_session_processing(sid): sleep(0.25)  ← 轮询，无超时上限
mark_session_processing(sid, True)             ← check-then-set，两步间无 await
try:    _start_inner(...)
except: 记 error、emit workflow_failed
finally: mark_session_processing(sid, False)   ← 异常/正常两路都释放 ✓
```

### 1.3 五维度判定

| 维度 | 现状判定 | 证据 |
|---|---|---|
| **并发（goal ↔ chat）** | ❌ **互斥已空转 + Gateway 侧完全无互斥** | chat 唯一生产入口在 Gateway；Gateway 的 `/send`、`chat_session_state`、`available_actions` **零处**读 `_processing_sessions`（全仓 grep 仅 workflow.py + service.py + 测试）。goal 占位期间 Gateway 聊天照常执行——**当前即可并发**，双方无感知 |
| **原子获取** | ⚠️ 隐式原子 | check-then-set 依赖"两步间无 await"的 asyncio 单线程实现细节；插一行 `await` 即出竞态窗口。需显式原子 API 固化 |
| **等待** | ⚠️ 无限轮询（但当前空转） | `sleep(0.25)` 无上限。因 `is_` 恒 False 而永不触发；若 legacy 入口未来复活，chat 卡死将使 goal **永久挂起**。按裁决：**保持等待语义（不改拒绝），超时上限留待后续裁决** |
| **取消** | ⚠️ 单向 | goal 有 `finally` 释放（✓ 异常安全）；但 **Gateway cancel 不知 lease、goal 无取消 API**——lease 建立后须定义 cancel 路径 |
| **异常清理** | ✓ 方向正确 | `workflow.py:521 finally` 两路释放；lease 迁移必须保留此形态 |
| **状态可观测** | ❌ 双状态源 | legacy set 只有 SessionService 自知；Gateway `chat_session_state()` 只报 active/pending/paused → UI 在 goal 运行期仍显示 "send" 可用 |
| **滞留风险** | ⚠️ 幽灵消息缺陷（附带发现） | goal 占位期间 `send_message` 照常 `queue.put`（:263）但 :298 不启动 consumer；goal `mark(False)` 只 discard、**不检查队列** → 消息滞留至用户再发一条才被带出消费 |

### 1.4 附带发现（不在本批修复范围，记录在案）

- `workflow_start` 事件在获取互斥**之前**发出（workflow.py:505）——互斥等待期已宣称 running；
- 上述幽灵滞留仅在 legacy 队列路径复活时可达；当前 send_message 无生产调用，属**潜伏缺陷**，删除队列批次（裁决 3）可一并消除该路径。

## 2. 设计：session execution lease（Gateway 持有）

### 2.1 接口

```python
# kernel/gateway/（新模块或并入现有协调层）
class SessionExecutionLease:
    def acquire(self, session_id: str, owner: str, *, wait: bool = False,
                poll_s: float = 0.25) -> str | None:
        """原子获取；返回 ownership token，失败/超时（wait=False）返回 None。"""
    def release(self, session_id: str, token: str) -> bool:
        """按 token 释放；token 不匹配（已被顶替/已释放）返回 False，不误放他人租约。"""
    def holder(self, session_id: str) -> str | None:
        """当前持有者 owner（只读观测，供状态展示，非互斥原语）。"""
```

- **原子性**：asyncio 单线程内 `dict` check-then-set 无 await → 显式满足（不依赖调用方"别插 await"）；实现处加断言注释固化此约束。
- **owner token**：`goal:<goal_id>` / `chat:<run_id>` 形式，release 校验 token 防"异常清理误释放他人"与"双重释放"。
- **不复用 `SessionFence.claim()`**（fencing.py:55 实测是"超越 activeRunId 的顶替"语义）；**不拿 `chat_session_state()` 快照当互斥**——lease 是独立原语，state 快照只用于展示。

### 2.2 挂钩点

| 挂钩 | 行为 |
|---|---|
| Gateway chat run 启动 | `acquire(sid, f"chat:{run_id}", wait=False)`——被 goal 占用时**按现聊天语义走排队**（mailbox 已有队列语义，不是拒绝） |
| Gateway chat run 收尾（含失败/取消） | `release(sid, token)`（run 结束路径统一释放） |
| `GoalWorkflow.start` | `while (token := lease.acquire(sid, "goal:...", wait=False)) is None: await sleep(0.25)`（**保持现状等待语义**）→ `try/finally lease.release(sid, token)` |
| `chat_session_state()` | 增补 `lease_holder` 字段（只读展示）；`available_actions` 在 goal 持有时**不显示 send**（现状 bug：goal 运行期 UI 可发——由状态可观测维度修复，属行为增强，实施批需单独回归） |
| 退出防滞留 | lease release 时若 mailbox 有 pending 且无 consumer → 唤醒消费（吸收 §1.3 幽灵滞留缺陷） |

### 2.3 与 legacy 的关系

- `is_session_processing` / `mark_session_processing` 在迁移完成前**保留**（goal 仍用，直至其调用点切到 lease）；切换后二者标注退役候选（R-004：先审计后删，删除归裁决 3 批次）。
- `SessionService` 队列/AgentLoop **本设计不触碰**（裁决 3）。

## 3. 实施批次（待确认后执行）

1. **B1 lease 原语**：`kernel/gateway` 新增 `SessionExecutionLease` + 单测（原子竞争 / token 校验 / 双重释放 / 并发 asyncio 任务交错）——零接线，独立提交。
2. **B2 chat 侧挂钩**：Gateway run 启动/收尾 acquire/release + `chat_session_state.lease_holder` + available_actions 感知 + 回归（现有 Gateway/API 测试全量 + 新增并发用例：goal 持有 → /send 排队；goal 释放 → 唤醒）。
3. **B3 GoalWorkflow 迁移**：`workflow.py:507-524` 切 lease（保持等待语义与 finally 形态）；`session_service` 注入点保留（其余职责不用）；并发回归（双 goal / goal×chat 交错 / 异常释放）。
4. **B4 legacy 标记退役标注**（不删除）：`mark_session_processing` 等标注退役候选，删除走裁决 3 批次。

## 4. 明确不做

- 不改等待为拒绝、不加超时上限（未经裁决的语义变更）；
- 不复用/修改 `SessionFence`；不动 mailbox 队列语义；
- 不删 `SessionService` 队列/AgentLoop（裁决 3，R-014/015/016 批次）；
- 不重开 Attempt 分层裁决。

## 5. 验证清单（实施各批）

- B1：单测覆盖竞争（两个 asyncio 任务同时 acquire 只成一个）、token 不匹配释放返回 False；
- B2：Gateway 队列/取消/恢复既有回归 + goal 占用期 /send 行为符合"排队不拒绝"；
- B3：goal×chat 并发回归（释放路径：正常/异常/超时占用）+ `workflow_start` 时序复查（§1.3 附带发现是否顺带修正需单独裁决）；
- 每批：`batch_gate.cmd` + 全量 pytest + 体量数字同步。
