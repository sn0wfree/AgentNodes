# 批次 3 设计：移除 `apps/study` + `apps/autoresearch`，提取 Ticket/Assignment

> 状态：**设计（待实施）** · 2026-11-05
> 上游：`docs/design/agentnodes-split-plan-20261104.md` 阶段 2（AgentNodes 侧减法）
> 前序批次：`08303f04`（批次 2：goal/hypothesis/validation/portfolio/export）

---

## 0. 结论先行

| 问题 | 答案 |
|---|---|
| `apps/study` 能不能删？ | **能**，但必须先搬走 `ticket.py` + `ticket_sink.py` + 两张表的 CRUD |
| `Ticket`/`Assignment` 放哪？ | **`kernel/orchestration/`（L3）** —— 见 §2 的证据 |
| `apps/autoresearch` 能不能删？ | **能**，它只被 L1 的 `cli/commands/autoresearch.py` 驱动 |
| L3→L2 会不会涨？ | **不会**，实测当前 **0 条**（端口化已生效） |
| 删完 L1 要同步删多少？ | 5 个文件 + 6 处改动（见 §4） |

---

## 1. 实测依赖面（2026-11-05，AST 口径）

### 1.1 谁 import `apps/study` / `apps/autoresearch`

| 层 | 条数 | 结论 |
|---|---:|---|
| **L1 interfaces** | 51 | 主要出口：`routers/study.py`(31) · `routers/study_helpers.py`(4) · `routers/slash_commands.py`(5) · `cli/commands/autoresearch.py`(6) · `tier0_snapshot.py`(1) · 其他(4) |
| **L2 apps** | 6 | 全部在 `apps/capabilities.py`（注册门面） |
| **L3 kernel** | **0** | **端口化已生效**——kernel 一行都不 import study |
| **L4 foundation** | **0** | — |

**关键结论**：`apps/study` 与 kernel 的耦合**只剩 3 个 capability kind**
（`study.graph` / `study.store` / `study.scheduler` / `study.status` /
`study.bootstrap` / `study.ticket_sink`），走 `CapabilityRegistry.resolve()`。
kernel 侧全是惰性 resolve + try/except，kind 消失后运行时降级为 no-op。

### 1.2 Ticket/Assignment 的真实依赖者

| 消费者 | 用法 | 迁移影响 |
|---|---|---|
| `interfaces/tier0_snapshot.py:106` | `from apps.study.ticket import _ASSIGNMENT_TRANSITIONS, _TICKET_TRANSITIONS` | 改 import 路径即可 |
| `apps/capabilities.py:200` | `from apps.study.ticket_sink import TicketSink` → kind `study.ticket_sink` | 见 §3 |
| `apps/study/store.py` | 7 个 CRUD 方法 + 2 张表 DDL + 2 个 row→对象转换 | 见 §2.3 |
| `tests/test_ticket_assignment.py` | 7 处 `from apps.study.ticket_sink import TicketSink` | 改 import 路径 |
| `tests/test_production_wiring.py` | 断言 kind `study.ticket_sink` 存在 | 见 §3 |

**没有任何第三方消费者**——`Ticket`/`Assignment` 的全部使用面就是上面这些。

---

## 2. 裁定：`ticket.py` 搬进 `kernel/orchestration/`（L3）

### 2.1 为什么不是「留在 L2 的独立包」（`apps/ticket/`）

批次 3 的目标是**删掉 study**。如果 `ticket.py` 挪进 `apps/ticket/`，
它仍然是一个 L2 包，仍然要：

- 保留 `apps/capabilities.py` 里的 `study.ticket_sink` kind
- 保留 L1 的 `tier0_snapshot.py` → L2 的 import（L1→L2 合法，但没减少什么）
- 让「AgentNodes 只剩 agent 框架」这条目标**打折扣**——Ticket 是通用任务身份，
  不是研究业务

### 2.2 为什么是 L3：业务词密度实测

`ticket.py` 全文 325 行，业务词汇（策略/因子/回测/行情/研究/收益/风险…）**出现 0 次**。
逐字段核对：

| 字段 | 语义 | 是业务吗 |
|---|---|---|
| `ticket_id` | 任务标识 | 否 |
| `objective` | 「要做什么」的自由文本 | 否（泛化目标，见 §5.1 路线图） |
| `origin_session_key` | 来源会话 | 否 |
| `authorization_scope` | 授权范围（dict，结构由调用方定义） | 否 |
| `priority` | 0-9 优先级 | 否 |
| `status` | pending/active/ended | 否 |
| `superseded_by` | 被谁取代 | 否 |
| `fence_token` / `run_id` / `worker_session_id` | 执行绑定标识 | 否 |

`Assignment.report_event_data()` 产出的 payload 是
`reason` / `progress_pct` / `current_step` / `blockers`——通用进度语义。

**对比**：`apps/study/models.py` 有 `MetricTarget` / `StudyRecord` /
`strategy_name` / `workspace_path`——那才是业务。Ticket 不是。

### 2.3 连带要搬的存储层

`store.py`（1,808 行）里的 Ticket/Assignment 部分**必须一起搬**，否则
`kernel/orchestration/ticket.py` 会反过来依赖 L2（新的 L3→L2 违规）：

| 要搬的 | 位置 | 行数 |
|---|---|---:|
| 2 张表 DDL（`tickets` / `assignments` + 5 个索引） | `store.py::_create_ticket_tables` | ~60 |
| 7 个 CRUD 方法 | `store.py::upsert_ticket` / `get_ticket` / `list_tickets` / `create_assignment` / `update_assignment` / `get_assignment` / `list_assignments` | ~120 |
| 2 个 row→对象转换 | `store.py::_ticket_from_row` / `_assignment_from_row` | ~30 |
| `TicketSink` 全部 | `ticket_sink.py` | 229 |

**目标形态**：`kernel/orchestration/ticket_store.py`（新的 L3 模块）

```python
class TicketStore:
    """Ticket/Assignment 的 SQLite 持久化 + 事件发射。

    与 StudyStore 同库（goals.db），但**独立建表**——两张表只依赖
    foundation.storage.sqlite 原语，零业务字段。
    """
    def __init__(self, db_path: Path | None = None, *,
                 emit: Callable[[str, dict], None] | None = None): ...
    # CRUD（从 StudyStore 平移）
    def upsert_ticket(self, ticket) -> Ticket: ...
    def list_tickets(self, *, limit: int = 100) -> list[Ticket]: ...
    def create_assignment(self, assignment) -> Assignment: ...
    def update_assignment(self, assignment) -> Assignment: ...
    def list_assignments(self, ticket_id=None, *, limit=100) -> list[Assignment]: ...
    # 业务侧原来在 TicketSink 的编排逻辑（同属 L3，无需跨层）
    def admit(self, **kwargs) -> Assignment: ...
    def settle(self, *, assignment, status, reason=None) -> Assignment: ...
    def recover(self) -> list[Assignment]: ...
    def recover_and_mark(self, reason="restart") -> list[Assignment]: ...
```

**数据库路径**：沿用 `QUANTNODES_RESEARCH_GOAL_DB_PATH`（与 StudyStore 同库）。
库名带 `RESEARCH` 是历史包袱，但改它会破坏既有用户数据——留作后续重命名
（与阶段 2 的 `strategy_research` → `agentnodes` 包名重命名同批处理）。

### 2.4 capability kind 的去留

| kind | 处置 |
|---|---|
| `study.ticket_sink` | **改名为 `ticket.store`**（L3 自带，不再需要 apps 注册） |
| `study.graph` / `study.store` / `study.scheduler` / `study.status` / `study.bootstrap` | **随 study 一起删除** |

改名后 `apps/capabilities.py` 里那 6 条 L2→L3 也一并消失——
`apps/capabilities.py` 从 32 kind 降到 **26 kind**。

`interfaces/gateway_wiring.py::build_ticket_sink()` 改为直接构造 L3 类：

```python
def build_ticket_sink() -> Any | None:
    """构造 TicketStore（Ticket/Assignment 持久化）。

    纯 L3 模块，不再经 CapabilityRegistry —— 它已不是业务能力，
    是内核机制（与 CapacityGate / GenomeVersionStore 同类）。
    """
    from strategy_research.kernel.orchestration.ticket_store import TicketStore
    return TicketStore()
```

---

## 3. 移除范围

### 3.1 `apps/study`（36 文件 / 11,824 行）

| 文件 | 行 | 处置 |
|---|---:|---|
| `store.py` | 1,808 | Ticket 部分先搬走（§2.3），其余归档 |
| `runner.py` | 1,277 | 归档 |
| `scheduler.py` | 1,125 | 归档 |
| `phase_engine.py` | 958 | 归档 |
| `bootstrap.py` | 527 | 归档 |
| `scenario_router.py` | 405 | 归档 |
| `graph_preset.py` | 397 | 归档 |
| `stub_agents.py` | 341 | 归档 |
| `round_manifest.py` | 336 | 归档 |
| `ticket.py` | 325 | **搬 L3**（§2） |
| `models.py` | 312 | 归档 |
| `dag_planner.py` | 300 | 归档 |
| `run_summary.py` | 291 | 归档 |
| `loop_evolution.py` | 265 | 归档 |
| `graph.py` | 253 | 归档 |
| `ticket_sink.py` | 229 | **并入 L3 `ticket_store.py`** |
| `engine_common.py` | 227 | 归档 |
| `scoreboard.py` | 210 | 归档（批次 2 从 goal 迁入的 AEGIS 杠杆统计） |
| `monitor.py` | 208 | 归档 |
| `lazy_detect.py` | 139 | 归档 |
| `health.py` | 135 | 归档 |
| `graph_templates.py` | 123 | 归档 |
| `runner_aegis.py` | 99 | 归档 |
| `state_store.py` | 98 | 归档 |
| `director.py` | 93 | 归档 |
| `budget.py` | 89 | 归档 |
| `runner_knowledge.py` | 85 | 归档 |
| `state_utils.py` | 82 | 归档 |
| `contracts.py` | 80 | 归档 |
| `objective_policy.py` | 37 | 归档（批次 2 从 goal 迁入） |
| `method/` | 7 文件 1,1xx | 归档 |
| `__init__.py` | 75 | 归档 |

### 3.2 `apps/autoresearch.py`（1,364 行）

唯一消费者：`interfaces/cli/commands/autoresearch.py`（6 处 import）。
该 CLI 命令随 apps 一起删。**无 capability kind**——纯 CLI 直连。

### 3.3 L1 同步移除

| 文件 | 行 | 理由 |
|---|---:|---|
| `routers/study.py` | ~1,700 | 31 处 study import |
| `routers/study_helpers.py` | ~230 | 4 处 |
| `routers/slash_commands.py` | ~450 | 5 处 study import（`/study` 系列斜杠命令） |
| `cli/commands/autoresearch.py` | ~530 | 6 处 |
| `tier0_snapshot.py` | 155 | **保留**，只改 state_machine 采集路径 |

**改动但不删**：

| 文件 | 改动 |
|---|---|
| `interfaces/api/app.py` | 反注册 `study` / `slash_commands` 两个 router |
| `interfaces/tier0_snapshot.py` | `_collect_state_machine()` 改 import `kernel.orchestration.ticket`；`source` 字段改 `"kernel.orchestration.ticket"` |
| `interfaces/gateway_wiring.py` | `build_ticket_sink()` 直接构造 L3 类 |
| `apps/capabilities.py` | 删 6 个 kind + 6 个工厂函数 |
| `interfaces/api/routers/admin_users.py:210` | 删 `apps.study.store` 引用（见 §4） |
| `interfaces/api/routers/chat.py:691` | 删 `apps.study.engine_common` 引用（见 §4） |
| `interfaces/cli/__init__.py` | 反注册 `autoresearch` 命令 |
| `interfaces/mcp/server.py` | 删 study 相关工具（如有） |

---

## 4. 三个耦合点的实测裁定

### 4.1 `admin_users.py:210` → `apps.study.store._default_db_path`

**实际用法**（`user_data` 审计视图）：

```python
from strategy_research.apps.study.store import _default_db_path as _study_db_path
...
data["studies"] = conn.execute(
    "SELECT COUNT(*) FROM studies WHERE owner_session_id IN "
    "(SELECT id FROM sessions WHERE user_id = ?)", (user_id,)).fetchone()[0]
```

**裁定**：`_default_db_path` 只是「goals.db 的路径解析」——挪到 L3 的
`ticket_store.py`（同库同路径）。`studies` 这个计数是业务指标，随 study 消失，
替换为 **`tickets` / `assignments` 计数**（L3 表仍在，审计视图仍然有用）：

```python
data["tickets"] = conn.execute(
    "SELECT COUNT(*) FROM tickets WHERE origin_session_key IN "
    "(SELECT id FROM sessions WHERE user_id = ?)", (user_id,)).fetchone()[0]
data["assignments"] = conn.execute(
    "SELECT COUNT(*) FROM assignments WHERE worker_session_id IN "
    "(SELECT id FROM sessions WHERE user_id = ?)", (user_id,)).fetchone()[0]
```

### 4.2 `chat.py:691` → `apps.study.engine_common.ensure_study_session`

**实际用法**：SSE 连接时若 session 行不存在（404）且 id 以 `study_` 开头，
**自动重建 chat-session 行**，好让 SSE 能继续。

**实测 `ensure_study_session` 的实现**（`engine_common.py`，30 行）：
只做 `SELECT ... FROM sessions` + `INSERT OR IGNORE INTO sessions`，
用 sqlite3 直连。**零 study 语义**——名字里带 study 是历史包袱。

**裁定**：搬进 **`interfaces/api/session/web_session_db.py`（L1）**，
改名为 `ensure_session_row(db_path, session_id, title)`。它操作的是
`web_session_db` 自己管理的 `sessions` 表，本来就该住在那里。
`chat.py` 的调用改为本地 import（同包）。

### 4.3 `scripts/migrate_study_history.py` → `apps.study.engine_common`

一次性迁移脚本，随 study 一起删。

---

## 5. 降级策略（不静默跳过）

kernel 侧对 6 个 study kind 的 resolve 全部是**惰性 + try/except**，
kind 消失后运行时降级为 no-op。但需要**显式确认**每一处的降级语义：

| kernel 位置 | 降级后行为 | 是否可接受 |
|---|---|---|
| `loop._get_goal_store` | 已随批次 2 降级 | ✅ |
| `context_injector` | 同上 | ✅ |
| `swarm` 分支 | 同上 | ✅ |
| `gateway.recover` 的 ticket sink | 改成 L3 直接构造，不受影响 | ✅ |
| `SessionController` 的 `_ticket_sink` | 同上 | ✅ |
| **`scheduled_research`** | **见 §5.1（本批次最大连带影响）** | ⚠ |

### 5.1 `kernel/scheduled_research` 的连带影响（实测）

这 6 文件 / 1,194 行**整个子系统的主职就是「到点创建一个 study」**：

| 文件 | 行 | 与 study 的关系 |
|---|---:|---|
| `executor.py` | 316 | `_default_dispatch_study()` → `resolve("study.bootstrap")` 建 study + `scheduler.submit()`；`target == "autoresearch"` 走 subprocess |
| `cli.py` | 372 | `_build_study_scheduler()` → `resolve("study.scheduler")` + `resolve("study.store")`；`schedule run` 等 study 终态（`resolve("study.status")`） |
| `store.py` | 258 | `target TEXT DEFAULT 'study'` 列；与 study 同库（`goals.db`） |
| `models.py` | 123 | `target: str = "study"`；`study_params()` 把 config 映射成 study 创建参数 |
| 其余 2 文件 | ~125 | 时间计算 / 注册 |

**关键观察**：调度器**骨架是通用的**（cron 时间计算、`scheduled_jobs` 表、
asyncio 循环），只有**默认 dispatch 目标**绑死 study。
`store.py` 的 `target` 是自由字符串，没有 study 类型依赖。

**裁定：保留骨架，把 study 目标改成显式报错。**

```python
async def _default_dispatch_study(self, job) -> str:
    """study 目标已随批次 3 移除（2026-11-05）。

    不静默 no-op —— 那会让用户以为任务跑成功了。抛明确错误，
    调度记录留 failed 状态与原因。
    """
    raise RuntimeError(
        "scheduled job target='study' is no longer available: "
        "apps/study was removed in subtraction batch 3. "
        "Use target='command' (subprocess) or register a custom dispatcher."
    )
```

**为什么保留而不是整包删**：`scheduled_jobs` 表与 cron 计算是通用能力，
未来「定时跑 agent 任务」是框架该有的（路线图 §5.1 的泛化机制会用到）。
但**本批次不做泛化**——只做「显式报错」，把「target 注册表化」记入
`docs/design/agentnodes-split-plan-20261104.md` 的后续项。

**测试影响**：`test_scheduled_cli_runtime.py` / `test_scheduled_executor.py` /
`test_scheduler_gateway_path.py` / `test_stage3c_scheduler_migration.py`
四个文件里的 study 派发用例需改为断言「抛 RuntimeError」。

---

## 6. 测试处置

| 类别 | 数量 | 处置 |
|---|---:|---|
| 纯 study/autoresearch 测试 | ~26 | 归档 `testcase/tests/` |
| `test_ticket_assignment.py`（734 行） | 1 | **保留**，改 import 路径（测的是 L3 机制） |
| `test_production_wiring.py` | 1 | 改断言：kind `study.ticket_sink` → 直接构造 `TicketStore` |
| `scheduled_*` 4 文件 | 4 | study 派发用例改为断言「抛 RuntimeError」 |
| 混合测试（`test_study_api.py` 等） | ~3 | 归档（整个路由被删） |

**保留并改造的测试**必须覆盖：
1. `TicketStore` 的 CRUD（原 `StudyStore` 的 7 个方法）
2. 状态机（三态 / 七态 / 非法转移抛 `StaleTicketError`）
3. `admit` / `settle` / `recover_and_mark` 的编排语义
4. Tier 0 快照仍能采到状态机（`tier0_snapshot._collect_state_machine`）
5. `ensure_session_row` 搬到 `web_session_db` 后行为不变

### 6.1 逐文件清单（实测 30 个文件引用 study/autoresearch）

**归档（纯 study/autoresearch 面）**：

| 文件 | 行 |
|---|---:|
| `test_study_api.py` | 923 |
| `test_study_actions.py` | 869 |
| `test_ticket_assignment.py` | 734 → **保留改造** |
| `test_study_guidance.py` | 345 |
| `test_agent_event_wiring.py` | 340 |
| `test_hitl_gate_graph.py` | 459 → 需逐项看 |
| `test_scheduled_executor.py` | 299 → 改造 |
| `test_scheduled_cli_runtime.py` | 284 → 改造 |
| `test_study_objective.py` | 288 |
| `test_study_v2_path_params.py` | 266 |
| `test_hanging_events.py` | 257 |
| `test_study_dump.py` | 234 |
| `test_stage3c_scheduler_migration.py` | 218 |
| `test_study_round_detail.py` | 218 |
| `test_study_hitl_respond.py` | 218 |
| `test_dag_planner.py` | 210 |
| `test_r3_session_domain.py` | 207 |
| `test_param_evolution.py` | 189 |
| `test_bg_backtest.py` | 186 |
| `test_r4_data_plane.py` | 174 |
| `test_dag_planner_llm.py` | 154 |
| `test_branch_contracts.py` | 113 |
| `test_scheduler_gateway_path.py` | 105 |
| `test_spawn_agent_loop_strategy.py` | 99 |
| `test_method_domain.py` | 83 |
| `test_gateway_singleton.py` | 82 |
| `test_lifespan_recover.py` | 82 |
| `test_study_engine_dispatch.py` | 60 |
| `test_production_wiring.py` | 226 → **保留改造** |
| `test_scripts_arch.py` | 443 → 不动 |

> 上表的行数合计约 8,600 行，但**不是全部归档**——标「改造」的 5 个保留。
> 剩余 25 个文件在实施时逐个确认：只要测试的主体是 study 编排就归档，
> 主体是平台机制（如 `test_r3_session_domain` 测的是 session 串行域）就改引用。

---

## 7. 验收标准

| 项 | 目标 |
|---|---|
| `apps/study/` 目录 | 不存在 |
| `apps/autoresearch.py` | 不存在 |
| `kernel/orchestration/ticket.py` | 存在，业务词密度 **0** |
| `kernel/orchestration/ticket_store.py` | 存在，含 CRUD + 编排 |
| **L3→L2** | 仍为 **0** |
| L1→L2 边数 | 91 → 约 **40**（-51） |
| 能力注册 | 32 → **26 kind** |
| `check_dangling_imports` | 0 悬空 |
| `sync_architecture_diagram --write` | 全 `[OK]` |
| `test_ticket_assignment.py` | 全绿（改 import 后） |
| `test_layer_matrix` | 棘轮基线不升 |

---

## 8. 执行顺序（每步可独立验证）

```
0. 本设计文档（doc first）                          ← 当前
1. 建 kernel/orchestration/ticket.py       ← 从 apps/study/ticket.py 平移
   └─ 门禁：L3→L2 仍 0
2. 建 kernel/orchestration/ticket_store.py ← 从 ticket_sink.py + store.py 抽取
   └─ 门禁：L3→L2 仍 0
3. 搬 ensure_session_row → interfaces/api/session/web_session_db.py（L1）
   └─ 测试：test_chat_sse / test_web_session 全绿
4. 改 tier0_snapshot + gateway_wiring + capabilities + admin_users + chat
   └─ **安全点**：test_ticket_assignment 全绿 = 提取正确
5. 删 apps/study + apps/autoresearch
   └─ 门禁：0 悬空
6. 删 L1 出口（5 文件）+ 改 app.py / cli/__init__ / mcp
   └─ 门禁：0 悬空 + 收集 0 error
7. scheduled_research：study target 改显式报错 + 改 4 个测试
8. 归档测试 + 全量回归
9. sync_architecture_diagram.py --write + 版本史 v10.0
10. commit
```

**第 4 步是安全点**：到这里 Ticket/Assignment 已完全独立于 study，
`test_ticket_assignment.py` 全绿就证明提取正确。之后删 study 只是减法。

**回滚点**：每步一个 commit。第 5 步之后若发现漏改，`git revert` 单个 commit
即可回到「study 还在、Ticket 已独立」的状态。

---

## 9. 不在本批次范围

- **泛化版「目标/准则/证据」机制**：路线图 §5.1（`docs/design/agentnodes-split-plan-20261104.md`）
- **`graph_shape` Tier 1 组件**：结构性变更，用户已决定延后
- **包名重命名** `strategy_research` → `agentnodes`：阶段 2 第 5 步
- **`scheduled_research` 的 study executor 语义**：本批次只做「报明确错误」，
  不做替代实现
