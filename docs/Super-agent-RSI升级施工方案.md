# Super-agent + RSI 升级施工方案

> **文档定位**：本文是"施工图纸"，描述三阶段改造的详细设计（数据模型、状态机、文件清单、事件契约、测试策略、回滚方案）。
>
> **上游约束**（必读且以之为准，冲突时优先级更高）：
>
> 1. `architecture-diagram.html` v8.4（结构 / 接缝 / 状态）
> 2. `docs/architecture-discussion-record` §3 / ADR-000（接缝口径：物理口径，每语句计 1）
> 3. `docs/delivery-summary` §17（棘轮基线）
>
> **下游消费者**：`docs/superagent-rsi-upgrade-goal.md`（裁判规则）。冲突裁决：goal > 本文；上游 > 本文。
>
> **本文不含**：验收命令（在 goal §4）、硬约束定义（在 goal §2）、非目标（在 goal §7）。本文只回答"怎么做"。

---

## 0. 文档定位

| 问题 | 答案 |
|---|---|
| 为什么要升级 | 把"turn 是一次执行权"升级为"Ticket 是持久化任务身份"，接通 RSI Tier 1 闭环 |
| 升级的边界 | 不破 v8.4 接缝规则；不升棘轮基线；不动 `AgentLoop` 内部 Mixin |
| 谁是裁判 | `docs/superagent-rsi-upgrade-goal.md` |
| 谁是图纸 | 本文 |
| 冲突怎么办 | 图 v8.4 > ADR-000 > delivery-summary §17 > goal > 本文 |
| 阶段不可越界 | 每阶段只动"文件清单"内列出的文件，超出需提案 |

---

## 1. 现状诊断

### 1.1 Super-agent 概念映射

| Super-agent 概念 | v8.4 对应物 | 状态 | 差距 |
|---|---|---|---|
| Router | ② Gateway + SessionController + mailbox | ✅ 已建成 | 不感知 Ticket/Assignment |
| Trigger | WorkRequest（①） | ✅ 已建成 | 无 |
| Thread / Subscription | `home_session_key` 语义 + R3 跨 kind 串行域 | ✅ 语义已承担 | 键本身未建（**非目标**，R3 已承担语义） |
| Ticket | turn（准入的执行权） | ⚠ 语义存在，非持久化实体 | run 崩溃即丢 |
| Assignment | run（`{sid}#n → :r0`） | ✅ 有 run_id + fence | 无结构化状态 |
| Worker | ③ GraphEngine 的一次 turn 执行 | ✅ | react 节点 = AgentLoop 平移 |
| 报告机制 | 事件流（EmitEvent） | ✅ 唯一事实源 | 未结构化（六态） |
| Worker Slot 管理 | 容量组闸 | ⏳ 未建 | 并行派发会打爆 |
| 端口回调注册表 | CapabilityRegistry | ⏳ 未建 | 46 条 L3→L2 违规待清 |
| RSI 基因组 | Tier 0/1/2 | ⏳ 框架有，Tier 1 通道空 | 无 holdout、无 EvolutionAgent |

### 1.2 关键缺口（三处，对应三阶段）

1. **`turn` 不持久化**：run 崩溃后 turn 丢失，无法恢复"哪些任务在等"。
2. **`kernel` 直连 `apps`**：46 条 L3→L2 违规挡住"evolution 读研究数据"的路（H1）。
3. **Tier 1 通道空**：有档位无车道，没有"提案-验证-回滚"的闭环。

### 1.3 改造原则

- **嫁接，不重写**：`AgentLoop` / `GraphEngine` / `SessionController` / `EventStore` 的内部逻辑不动，只在边界加"报告 / 端口 / 门控"。
- **研究域放 app**：所有新增业务组件（Ticket / EvolutionAgent / holdout）放 `apps/*`，通过 9 触点白名单用平台能力。
- **每阶段独立可验收、可回滚**：阶段之间不共享未验收的产物。

---

## 2. 阶段一：Ticket / Assignment 实体化

### 2.1 目标

把"一次 turn 准入"从**运行时概念**升级为**持久化实体**：

- `Ticket` = 任务身份（"要做什么"），持久化，可恢复
- `Assignment` = 执行绑定（"谁在做、做到哪"），可重入
- 每次 Assignment 状态变更 → `assignment.report` 事件 → 进入 EventStore（H6）

### 2.2 数据模型

**`Ticket`（`src/strategy_research/apps/study/ticket.py`）**

```python
@dataclass(frozen=True)
class Ticket:
    ticket_id: str              # UUID4
    objective: str              # 任务目标
    origin_session_key: str     # 来源会话（仅记录，语义由 R3 串行域承担）
    authorization_scope: dict   # 授权范围（工具白名单、预算上限等）
    priority: int               # 优先级（0-9，越大越优先）
    status: TicketStatus        # pending / active / ended
    superseded_by: str | None   # 被哪个 ticket 取代（保留审计）
    created_at: str             # ISO8601
    updated_at: str             # ISO8601


class TicketStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    ENDED = "ended"
```

**`Assignment`（同文件）**

```python
@dataclass(frozen=True)
class Assignment:
    assignment_id: str          # UUID4
    ticket_id: str              # FK → Ticket
    run_id: str                 # {sid}#n → :r0 格式
    worker_session_id: str      # 执行会话
    status: AssignmentStatus    # 见 2.3
    fence_token: str            # 继承 turn 的 fence 语义
    report_payload: dict        # 最近一次 report 的 payload
    started_at: str
    ended_at: str | None


class AssignmentStatus(str, Enum):
    PENDING  = "pending"     # 创建，未开始（不 emit report）
    RUNNING  = "running"     # 执行中（emit report）
    WAITING  = "waiting"     # 等外部事件（emit report）
    READY    = "ready"       # 待人工审核（emit report）
    BLOCKED  = "blocked"     # 被阻塞（emit report）
    FAILED   = "failed"      # 失败（emit report）
    DROPPED  = "dropped"     # 被丢弃（emit report）
```

**口径说明**：goal §3 提到的"Assignment 六态" = running/waiting/ready/blocked/failed/dropped，这 6 个会 emit report。pending 是创建时态，不 emit。本文以此口径为准。

### 2.3 状态机

**Ticket 状态机**

```text
pending ──准入──> active ──结束──> ended
   │                │
   └──取消──> ended └──被取代──> ended (superseded_by 记录)
```

**Assignment 状态机**

```text
pending ──开始执行──> running
                        │
        ┌───────────────┼───────────────┬──────────────┬──────────────┐
        ▼               ▼               ▼              ▼              ▼
     waiting         ready           blocked         failed        dropped
        │               │               │              │              │
        └───────────────┴───────────────┴──────────────┴──────────────┘
                                │
                                ▼
                              ended（终态）
```

非法转移：抛 `StaleTicketError`（已在 `tests/test_goal_models.py` 里有同类断言，复用）。

**关键约束：**

- `Ticket.pending → active`：SessionController 准入时触发
- `Assignment.pending → running`：GraphEngine 开始执行时触发
- `Assignment.running → {waiting/ready/blocked/failed/dropped}`：LoopLifecycleMixin 结束路径触发
- `Assignment.* → ended`：Assignment 终态写入，不 emit 额外事件

### 2.4 与 turn / run / fence 的关系

| 概念 | 关系 |
|---|---|
| turn | 一次准入 = upsert Ticket（幂等）+ create Assignment（pending）。turn 结束时 Assignment 进入终态 |
| run | run_id 写入 Assignment.run_id；fence_token 从 run 继承到 Assignment；被超越的 Assignment 产物不落库（fence 校验沿用现有 R1 逻辑） |
| fence | Assignment 继承 fence 语义；fence 校验失败时 Assignment → dropped，不写业务状态表 |

**幂等性**：同一个 ticket_id 多次准入，只保留一行 Ticket，Assignment 每次创建新行（同一 Ticket 可多次 Assignment，对应重试）。

### 2.5 为什么放 apps/study/ 而非 kernel/

- `Ticket.objective` / `authorization_scope` 携带研究域业务身份（H3 定义 Tier 0 含"方法论"，研究目标属于 Tier 1 范围）
- H1 规定 kernel 不得 import apps；如果 Ticket 放 kernel，阶段三 EvolutionAgent 读 Ticket 时必然加重 L3→L2 违规
- 放在 `apps/study/ticket.py` 后，kernel 侧只通过 `CapabilityRegistry.resolve("ticket_store")` 读，不直接 import

### 2.6 文件清单

**新增（计划）**

- `src/strategy_research/apps/study/ticket.py`
- `tests/test_ticket_assignment.py`

**修改**

- `src/strategy_research/apps/study/store.py`：加 tickets / assignments 两表 + CRUD（走 `foundation/storage/sqlite.py` 原语）
- `src/strategy_research/kernel/gateway/session_controller.py`：准入路径追加 upsert Ticket + create Assignment，fence 语义不变
- `src/strategy_research/kernel/runtimes/graph_engine.py`：settle 路径追加 emit `assignment.report`
- `src/strategy_research/kernel/gateway/gateway.py`：订阅 `assignment.report`，管理 Worker Slot（不读 Ticket 表）

### 2.7 事件契约

**`assignment.report`（写入 event_log，H6）**

```json
{
  "type": "assignment.report",
  "run_id": "sess-abc#3:r0",
  "assignment_id": "asg-uuid",
  "ticket_id": "tkt-uuid",
  "status": "running|waiting|ready|blocked|failed|dropped",
  "payload": {
    "reason": "string | null",
    "progress_pct": 0.0,
    "current_step": "string",
    "blockers": []
  },
  "ts": "2026-11-04T10:30:00Z"
}
```

**`ticket.upserted`（新增，可选）**

```json
{
  "type": "ticket.upserted",
  "ticket_id": "tkt-uuid",
  "objective": "string",
  "status": "pending|active|ended",
  "ts": "2026-11-04T10:29:00Z"
}
```

### 2.8 Gateway 的 Worker Slot 管理

**职责边界**：Gateway 只订阅 `assignment.report`，不读 Ticket 表。根据 report 的 status 决定释放/保留 Worker Slot。

| report.status | Slot 动作 |
|---|---|
| running | 保留（首次遇到时分配） |
| waiting | 释放（进入等待，不占 slot） |
| ready | 释放（等待人工审核） |
| blocked | 释放 |
| failed | 释放 |
| dropped | 释放 |

Slot 释放后：进入 waiting 的 Assignment 仍存在于表里；Ticket 仍是 active。用户/审核后重新准入 = 新 Assignment，Ticket 不变。

### 2.9 测试策略

**`tests/test_ticket_assignment.py`（新增）**

| 用例 | 断言 |
|---|---|
| test_ticket_state_machine | pending → active → ended 合法；pending → ended 合法；active → pending 抛 StaleTicketError |
| test_assignment_state_machine | 六个 report 态之间合法；ended → running 抛 StaleTicketError |
| test_ticket_upsert_idempotent | 同 ticket_id 两次 upsert，只一行 |
| test_assignment_inherits_fence | Assignment 的 fence_token = turn 的 fence；被超越时 Assignment → dropped |
| test_report_six_states_emitted | 六态各 ≥1 条 assignment.report 事件可 replay |
| test_fence_regression | 复用 `tests/test_r1_fence_wiring.py` 全绿 |
| test_no_new_l3l2 | `check_dangling_imports.py` 输出 L3→L2 = 46（不增加） |

### 2.10 回滚方案

- revert 本阶段全部提交
- tickets / assignments 表可保留为空（无副作用）
- Gateway 的 report 订阅路径回退到"只写事件不管理 slot"的旧行为

---

## 3. 阶段二：CapabilityRegistry + 容量组闸

### 3.1 目标

- 建成 CapabilityRegistry：`register(kind, factory)` + `resolve(kind)`，作为 9 触点白名单的唯一注册表
- 把 46 条 L3→L2 端口化清零：kernel 下所有 `from apps.xxx` 改为 `CapabilityRegistry.resolve("xxx")`
- 建成容量组闸：chat 宽 / study 窄 / cron 隔离，满→排队不拒绝

### 3.2 CapabilityRegistry 设计

**位置**：`src/strategy_research/kernel/agent/capability/registry.py`

```python
class CapabilityRegistry:
    """kernel 侧访问 apps 的唯一入口，H1 接缝规则的实现载体。"""

    def register(self, kind: str, factory: Callable[[], Any]) -> None:
        """注册一个 capability factory。重复注册抛 KeyError（fail-fast）。"""

    def resolve(self, kind: str) -> Any:
        """解析 capability。未注册抛 CapabilityNotFound。"""

    def list_kinds(self) -> list[str]:
        """列出已注册 kind（用于门禁审计）。"""
```

**注册时机**：在 apps 的入口装配阶段注册（例如 `apps/study/__init__.py` 的 `register_capabilities(registry)`），kernel 启动时通过 Gateway 的装配路径拿到已注册的 registry。

**禁止**：kernel 内部任何直接 `from apps.xxx import yyy` 新增代码（H1）。

### 3.3 46 条 L3→L2 端口化批次

基线（`docs/delivery-summary` §17）：L3→L2 = 46 条。

**批次划分原则：**

- 按 kind 分组（同一 capability 的多个 import 一次清）
- 每批 ≤8 处
- 每批独立提交，每批完成后基线同步下调

**批次占位表**（具体位置由 agent 实测后填，本文只给方法论）：

| 批次 | 预估 kind | 预估数量 | 验收 |
|---|---|---|---|
| B1 | study_store | ~8 | `check_dangling_imports.py` L3→L2 = 38 |
| B2 | goal_store | ~8 | L3→L2 = 30 |
| B3 | journal / scoreboard | ~8 | L3→L2 = 22 |
| B4 | hypothesis_store | ~8 | L3→L2 = 14 |
| B5 | study_scheduler | ~8 | L3→L2 = 6 |
| B6 | 剩余杂项 | ~6 | L3→L2 = 0 |

**每批步骤：**

1. `grep -rn "from strategy_research.apps" src/strategy_research/kernel/` 找出该批位置
2. 在 `apps/xxx/__init__.py` 加 `register_capabilities(registry)`
3. kernel 侧改为 `self._registry.resolve("kind")`
4. 跑 `pytest tests/` 确认无回归
5. 下调 `tests/test_layer_matrix.py` 的棘轮基线
6. 提交

若某批导致测试红：revert 该批，记 `agent.progress`，继续下一批。

### 3.4 容量组闸设计

**位置**：`src/strategy_research/kernel/gateway/capacity.py`

**三类池：**

| 池 | 上限 | 来源 | 说明 |
|---|---|---|---|
| chat | 宽（TODO：实测后填，建议 32） | 配置 | 交互式会话，用户体验优先 |
| study | 窄（= ExecutorBridge 进程池上限） | TODO：读 ExecutorBridge 默认值 | 研究任务，受 CPU 约束 |
| cron | 隔离（建议 2） | 配置 | 定时任务，独立池避免饿死 |

**行为：**

- 请求进入时先查池容量，满则进入 mailbox 排队（不拒绝，符合 v8.4 "全系统唯一拒绝点 = cap 20"）
- 池内 running 不抢占
- 池空出后按 mailbox 顺序唤醒

**与 mailbox 的关系**：容量闸是准入闸，不是队。等待发生在 mailbox。

### 3.5 棘轮基线同步

每清一批，`tests/test_layer_matrix.py` 的基线数字同步下调。门禁本身不变（只降不升）。

### 3.6 文件清单

**新增（计划）**

- `src/strategy_research/kernel/agent/capability/__init__.py`
- `src/strategy_research/kernel/agent/capability/registry.py`
- `src/strategy_research/kernel/gateway/capacity.py`
- `tests/test_capability_registry.py`
- `tests/test_capacity_gate.py`

**修改**

- 逐批修改 kernel 下 46 处 L3→L2 import（每批 ≤8 处，独立提交）
- `apps/*/__init__.py`：加 `register_capabilities(registry)`（每个被端口化的 app 包）
- `tests/test_layer_matrix.py`：棘轮基线同步下调

### 3.7 测试策略

**`tests/test_capability_registry.py`（新增）**

- register 幂等性（重复注册 fail-fast）
- resolve 未注册抛 `CapabilityNotFound`
- `list_kinds` 覆盖所有端口 kind
- 注册顺序无关

**`tests/test_capacity_gate.py`（新增）**

- chat 池满 → 请求进 mailbox，不拒绝
- study 池满 → 同上
- cron 池独立（study 满不影响 cron）
- running 不抢占
- 池释放后按顺序唤醒

**回归**

- `tests/test_gateway_behavior.py` 全绿（容量闸不改变 Gateway 外部契约）

### 3.8 回滚方案

- 某批端口化导致测试红 → revert 该批，基线不动
- 若整个阶段失败 → revert 全部，基线回到 46
- 容量闸可独立 disable（配置开关），不影响其他功能

---

## 4. 阶段三：RSI 闭环

### 4.1 目标

接通 Tier 1 基因组的提案-验证-回滚闭环，且 EvolutionAgent 只能在只读 holdout 上验证提案。

### 4.2 holdout 抽样（`apps/evolution/holdout.py`）

**位置**：`src/strategy_research/apps/evolution/holdout.py`

**职责**：从 EventStore 的永久事件流中抽取"未参与当前 genome_version 生效周期"的轨迹，冻结为只读 holdout 集。

**接口（只读，H5）：**

```python
def sample(task_signature: str, seed: int, limit: int = 20) -> list[Trajectory]:
    """从 event_log 抽样符合条件的轨迹。
    同一 (task_signature, seed) 结果可复现。
    """
```

**禁止**：无 append / update / delete 方法（H5）。

**抽样算法（详见附录 C）：**

1. 从 event_log replay 出所有 `assignment.report` 且 `status ∈ {failed, dropped}` 或 `status = ready` 的轨迹
2. 按 `task_signature` 过滤
3. 排除当前 `genome_version.activated_at` 之后的轨迹（防止奖励黑客）
4. `random.Random(seed).shuffle(trajectories)` + 取前 `limit` 条
5. 冻结为 `HoldoutSet`（不可变）

**存储**：`foundation/storage/holdout/`（走 ⑥ 存储原语，不新建存储通道）

### 4.3 EvolutionAgent（`apps/evolution/evolution_agent.py`）

**定位**：一个受限的 AgentLoop（react 节点），只在 Assignment 结束后触发，不参与常规 Worker 执行。

**工具集（只读 + 提案，全部经 CapabilityRegistry.resolve）：**

- `read_journal`：读研究账本
- `read_scoreboard`：读 Lever 统计
- `read_validation`：读历史验证结果
- `read_holdout`：调用 `holdout.sample()`（只读）
- `propose_genome_update`：生成 GenomeProposal（不直接生效）

**输出**：`GenomeProposal`（见 4.4），写入 `genome_proposals` 表。

**禁止**：EvolutionAgent 不得直接写 SkillMemory / ConfigHub / 任何 Tier 0 字段（H3）。

### 4.4 genome_proposals（`apps/evolution/genome_proposals.py`）

**表结构：**

| 字段 | 类型 | 说明 |
|---|---|---|
| proposal_id | str | UUID |
| target_component | str | 目标组件（仅限 Tier 1：prompt / skill / 图形状） |
| change | dict | 具体变更（结构化） |
| evidence_refs | list[str] | 证据引用（journal_id / event_id） |
| status | str | draft / validating / approved / rejected / rolled_back |
| created_at | str | ISO8601 |
| validated_at | str \| None | ISO8601 |

**Tier 0 拒绝**：validation_gate 必须拒绝 `target_component ∈ Tier0Set` 的提案（H3）。

### 4.5 validation_gate（`apps/evolution/validation_gate.py`）

**职责**：在隔离 Worker slot 中回放 holdout，比较指标，决定提案是否生效。

**流程：**

1. 收到 GenomeProposal（status = draft）
2. 在隔离 slot 中启动一个"回放 Worker"，加载当前 genome_version + 提案，跑 holdout
3. 比较指标：必须不劣于当前基线（阈值 ±20%，超出暂停通道，escalate）
4. 通过 → status = approved，genome_version 递增
5. 连续 3 次失败 → status = rolled_back，emit `genome.rollback` 事件，genome_version 回退

**隔离 slot 来源**：阶段二的容量组闸（study 池的独立 slot，不抢占 running）。

**只读**：回放过程不写业务表，只写 `validation_runs` 表（审计用）。

### 4.6 genome_version 与 ConfigHub

**位置**：`src/strategy_research/kernel/orchestration/config_hub.py`

**新增 genome_version 层：**

```python
@dataclass(frozen=True)
class GenomeVersion:
    version_id: str
    tier0_frozen: dict       # Tier 0 只读快照
    tier1_active: dict       # Tier 1 生效版本（prompt / skill / 图形状）
    tier2_self_modifiable: dict  # Tier 2 研究产物（strategy.py 等）
    activated_at: str
    activated_by_proposal: str | None
```

**规则：**

- **Tier 0 冻结**：`tier0_frozen` 只读，EvolutionAgent 不得修改（H3）
- **Tier 1 生效**：`tier1_active` 由 validation_gate 通过后更新
- **Tier 2 可自改**：研究产物（如 `strategy.py`）由研究域自改，不经过 validation_gate（但仍需事件记录）

### 4.7 SkillMemoryResolver

**位置**：`src/strategy_research/apps/evolution/skill_memory.py`（或 `config_hub.py` 内新增）

**职责**：装配期根据 `task_signature` 返回当前生效的 harness 配置。

```python
def resolve(task_signature: str) -> EffectiveHarnessConfig:
    """返回当前生效的 prompt_version / tool_set / compaction_config。
    读 genome_version.tier1_active，不读 Tier 0。
    """
```

**装配期调用点**（只加调用，不改内部逻辑）：

- `src/strategy_research/kernel/agent/prompt_builder.py`：`PromptBuilderFactory.get(role)` 时调用
- `src/strategy_research/kernel/agent/tools.py`：ToolRegistry 构造时调用
- `src/strategy_research/foundation/compact_config.py`：CompactConfig 构造时调用

**禁止**：不得在 AgentLoop 内部调用 SkillMemoryResolver（避免侵入内部逻辑）。

### 4.8 回滚机制

- **提案级回滚**：status = rolled_back，genome_version 回退到上一版本
- **版本级回滚**：`genome_version.rollback_to(version_id)`，写 `genome.rollback` 事件
- **触发条件**：连续 3 次 holdout 验证失败（阈值 ±20%）
- **事件**：`genome.rollback`，写入 event_log

### 4.9 文件清单

**新增（计划）**

- `src/strategy_research/apps/evolution/__init__.py`
- `src/strategy_research/apps/evolution/holdout.py`
- `src/strategy_research/apps/evolution/evolution_agent.py`
- `src/strategy_research/apps/evolution/genome_proposals.py`
- `src/strategy_research/apps/evolution/validation_gate.py`
- `src/strategy_research/apps/evolution/skill_memory.py`
- `foundation/storage/holdout/`（目录，走 ⑥ 存储原语）
- `tests/test_evolution_gate.py`
- `tests/test_holdout_readonly.py`

**修改**

- `src/strategy_research/kernel/orchestration/config_hub.py`：加 genome_version 层
- `src/strategy_research/kernel/agent/prompt_builder.py`：装配期调用 SkillMemoryResolver.resolve
- `src/strategy_research/kernel/agent/tools.py`：ToolRegistry 构造时调用
- `src/strategy_research/foundation/compact_config.py`：CompactConfig 构造时调用

### 4.10 测试策略

**`tests/test_holdout_readonly.py`（新增）**

- `holdout.py` 无 append/update/delete 方法（`hasattr` 断言）
- `sample(task_signature, seed=N)` 三次结果一致
- 抽样结果不包含当前 `genome_version.activated_at` 之后的轨迹

**`tests/test_evolution_gate.py`（新增）**

- `target_component ∈ Tier0Set` → validation_gate 拒绝
- `target_component ∈ Tier1Set` → 进入 validating
- holdout 通过 → status = approved，genome_version 递增
- 连续 3 次失败 → status = rolled_back + `genome.rollback` 事件
- 隔离 slot 不抢占 running

---

## 5. 跨阶段约束对照表

| 约束 | 阶段一落地 | 阶段二落地 | 阶段三落地 |
|---|---|---|---|
| H1 接缝规则 | Ticket 放 `apps/study/`；kernel 不新增 `from apps.` | 46 条 L3→L2 清零 | EvolutionAgent 放 `apps/evolution/`；经 CapabilityRegistry.resolve |
| H2 棘轮基线 | L3→L2 = 46（不增加） | L3→L2 = 0（逐批下调） | L3→L2 = 0（保持） |
| H3 Tier 0 冻结 | — | — | validation_gate 拒绝 Tier 0 提案 |
| H4 9 触点白名单 | Gateway 订阅 report（触点内） | CapabilityRegistry 是触点唯一注册表 | EvolutionAgent 工具全部经 registry |
| H5 holdout 写保护 | — | — | `holdout.py` 无写方法 |
| H6 事件流唯一事实源 | `assignment.report` 写 event_log | 端口化不绕过 EventStore | `genome.rollback` 写 event_log |

---

## 附录 A：事件 schema 全集

### `assignment.report`（阶段一）

见 §2.7。

### `ticket.upserted`（阶段一，可选）

见 §2.7。

### `genome.proposal.created`（阶段三）

```json
{
  "type": "genome.proposal.created",
  "proposal_id": "prop-uuid",
  "target_component": "prompt_version",
  "evidence_refs": ["journal-123", "event-456"],
  "ts": "2026-11-04T11:00:00Z"
}
```

### `genome.rollback`（阶段三）

```json
{
  "type": "genome.rollback",
  "from_version": "v3",
  "to_version": "v2",
  "reason": "3 consecutive holdout failures",
  "ts": "2026-11-04T11:30:00Z"
}
```

### `agent.progress`（goal §5 规定，非本方案定义）

见 goal §5。

### `agent.escalation`（goal §6 规定，非本方案定义）

见 goal §6。

---

## 附录 B：46 条 L3→L2 基线快照（待实测）

当前基线（`docs/delivery-summary` §17）：

- L3→L2 = 46
- L4→L2 = 2
- L4→L3 = 4
- L3→L1 = 2
- L2→L1 = 1

46 条的具体位置：本文不列（避免与代码现状脱节）。agent 启动阶段二时执行：

```bash
python scripts/arch/check_dangling_imports.py
```

输出会列出所有 L3→L2 违规的具体文件、行号、import 语句。按 §3.3 批次划分规则分批处理。

---

## 附录 C：holdout 抽样算法与 seed 策略

### 抽样算法

```python
def sample(task_signature: str, seed: int, limit: int = 20) -> HoldoutSet:
    # 1. 从 event_log replay 出所有 assignment.report
    reports = event_store.replay(types=["assignment.report"])

    # 2. 过滤候选轨迹
    candidates = [
        r for r in reports
        if r.payload.get("task_signature") == task_signature
        and r.status in {"failed", "dropped", "ready"}
    ]

    # 3. 排除当前 genome_version 生效周期内的轨迹
    current = config_hub.get_genome_version()
    candidates = [
        r for r in candidates
        if r.ts < current.activated_at
    ]

    # 4. 固定 seed 抽样（可复现）
    rng = random.Random(seed)
    rng.shuffle(candidates)

    # 5. 冻结为只读 HoldoutSet
    return HoldoutSet(trajectories=candidates[:limit], seed=seed)
```

### seed 策略

- 调用方必须显式传 seed（不允许默认值），避免隐式不确定性
- validation_gate 内部使用 `seed = hash(proposal_id) % 10000`，保证同一提案的多次验证使用同一 seed
- 测试使用固定 seed（如 42），断言三次结果一致

### task_signature 定义

```python
def compute_task_signature(objective: str, role: str, tool_set: frozenset[str]) -> str:
    """稳定哈希：objective 类型 + role + 工具集。"""
    h = hashlib.sha256()
    h.update(objective.strip().lower().encode())
    h.update(role.encode())
    h.update(",".join(sorted(tool_set)).encode())
    return h.hexdigest()[:16]
```

**稳定性要求**：同一 `(objective, role, tool_set)` 多次调用结果一致。

---

## 附录 D：新文件路径清单（计划新建，本方案落盘后全部可 `ls` 确认）

| 路径 | 阶段 | 说明 |
|---|---|---|
| `src/strategy_research/apps/study/ticket.py` | 一 | Ticket / Assignment 数据模型 |
| `src/strategy_research/kernel/agent/capability/__init__.py` | 二 | 包入口 |
| `src/strategy_research/kernel/agent/capability/registry.py` | 二 | CapabilityRegistry |
| `src/strategy_research/kernel/gateway/capacity.py` | 二 | 容量组闸 |
| `src/strategy_research/apps/evolution/__init__.py` | 三 | 包入口 |
| `src/strategy_research/apps/evolution/holdout.py` | 三 | holdout 抽样 |
| `src/strategy_research/apps/evolution/evolution_agent.py` | 三 | EvolutionAgent |
| `src/strategy_research/apps/evolution/genome_proposals.py` | 三 | 提案表 |
| `src/strategy_research/apps/evolution/validation_gate.py` | 三 | 验证门控 |
| `src/strategy_research/apps/evolution/skill_memory.py` | 三 | SkillMemoryResolver |
| `foundation/storage/holdout/` | 三 | holdout 存储目录 |
| `tests/test_ticket_assignment.py` | 一 | 阶段一测试 |
| `tests/test_capability_registry.py` | 二 | Registry 测试 |
| `tests/test_capacity_gate.py` | 二 | 容量闸测试 |
| `tests/test_evolution_gate.py` | 三 | 演化门控测试 |
| `tests/test_holdout_readonly.py` | 三 | holdout 写保护测试 |

---

## 附录 E：本方案不覆盖的内容

以下内容明确不在本方案（见 goal §7）：

- 独立宿主（v8.4 已判定延后）
- 分库（EventStore 单库足够）
- `home_session_key` 键（R3 已承担语义）
- AgentLoop 内部 Mixin 重构
- T3 沙箱、独立 cpu 节点（R5 判定不做）
- ⑤ 研究域已有业务组件重构

如需启动上述任一项，需单独提案，不在本方案范围内。

---

**文档版本**：v1.0（2026-11-04）
**作者**：架构讨论沉淀
**上游**：architecture-diagram.html v8.4 / ADR-000 / delivery-summary §17
**下游**：docs/superagent-rsi-upgrade-goal.md
**变更记录**：首次落盘；后续变更记入 `docs/architecture-diagram-changelog.md`
