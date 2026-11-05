# Study 任务系统设计：autoresearch 模式的服务化核心

> 把 goal 账本 + autoresearch 引擎合并为「study」——量化研究领域的长程自动执行
> 核心任务模式。goal 保留为通用长程任务入口，study 作为量化的专用执行形态。
> 原则：尽量复用现有代码，study 层只做编排 / 持久化 / 事件 / 指标停止。

## 1. 定位

**study** = 目标驱动的自动策略研发：用户给出研究目标（如「研究动量因子」）
+ 验收指标（Calmar≥0.5 / Sharpe≥0.3 / MaxDD≤-15%）+ 预算，系统自动编排
autoresearch 研究循环（8-agent → 回测 → 评估 → 提交），多轮长程执行，
证据沉淀到 goal 账本，达标自动完成审计，预算耗尽则归因收尾。

**与 goal 的关系**：goal 是通用账本（objective / criteria / evidence / status）；
study 创建时 `replace_goal` 建账本，每轮回测落 evidence，达标后 completion audit
关账。study 独立 `studies` 表承载**执行状态**（execution_status / round /
heartbeat / workspace），零侵入 goal 表。goal 未来可驱动其他类型的长程任务。

**与 autoresearch 的关系**：autoresearch 是同步 CLI 循环（`cmd_autoresearch`
838 行），无持久化执行状态、无指标停止、崩溃从头跑、前端不可见。study 把它
**服务化**：创建即入队 → 后台跑循环 → 进度持久化 → 重启断点续跑 → 事件推
前端 → 指标达标自动收尾。

## 2. 架构总览

```
┌─ 入口层 ───────────────────────────────────────────────────┐
│  /study start <objective> [--workspace W] [--strategy S]  │
│  [--metric calmar>=0.5,sharpe>=0.3] [--budget ...]        │
│  POST /api/study/start  /api/study/{pause,resume,cancel}  │
└─────────────────────────┬────────────────────────────────┘
                          ▼
┌─ 编排层 core/study/scheduler.py（新）─────────────────────┐
│  StudyScheduler                                           │
│    · session 级串行队列（与 chat attempt 互斥）            │
│    · 创建 study 记录 → 创建 goal 账本(replace_goal)        │
│    · 入队 → 启动 executor                                  │
│    · study_* 事件发射 → event_bus → SSE                    │
│    · 重启恢复：scan queued/running → 重入队 + 从 runs/ 续跑 │
└─────────────────────────┬────────────────────────────────┘
                          ▼
┌─ 执行层 core/study/executor.py（新，薄）──────────────────┐
│  AutoresearchExecutor                                     │
│    while not (达标/预算/stagnation/max_rounds/paused/cancel)│
│      ──► run_research_round(workspace,strategy,study_ctx)  │
│      ──► 指标达标检测：metrics vs metric_targets           │
│      ──► budget 累积 + 强制                                │
│      ──► 暂停/取消检查点                                    │
│      ──► 事件 + heartbeat                                  │
│    达标 → GoalStore.complete_lite / audit → study complete │
└─────────────────────────┬────────────────────────────────┘
                          ▼
┌─ 复用层（不动）───────────────────────────────────────────┐
│  core/autoresearch.py            896 行工具函数            │
│  cli/commands/autoresearch.py    _spawn_agent / 8-agent    │
│  core/backtest.py                run_backtest_script       │
│  core/strategy_acceptance        AcceptanceConfig + decide │
│  core/goal/store.py              GoalStore 账本            │
│  core/goal/context.py            goal 上下文注入           │
│  api/session/event_bus           SSE 事件总线              │
└───────────────────────────────────────────────────────────┘
```

## 3. 复用清单（最大化复用现状资产）

| 现有资产 | 文件位置 | study 复用方式 | 改动 |
|---|---|---|---|
| autoresearch 单轮逻辑 | `cli/commands/autoresearch.py:165-530` | 抽取为 `run_research_round()` 可复用函数，CLI 与 executor 共用 | **唯一既有改动**（回归守护） |
| autoresearch 工具函数 | `core/autoresearch.py` 896 行 | 直接 import，零改动 | 无 |
| `_spawn_agent`（真 LLM/stub 双路径） | `autoresearch.py:510` | 复用（`run_agent_via_llm` + role_factory） | 无 |
| `run_backtest_script` | `core/backtest.py:237` | 复用（metrics 形状已定） | 无 |
| `strategy_acceptance` | `core/strategy_acceptance/__init__.py:77` | `metric_targets` 直接映射 `AcceptanceConfig` overrides，`decide()` 的 `hard_passed` = 达标信号 | 无 |
| `_append_backtest_evidence`（P3-D3） | `autoresearch.py:20` | 复用（session_id 改为真实 session 传入） | 参数化 |
| `_register_researcher_hypothesis` | `autoresearch.py:70` | 复用（同上） | 参数化 |
| GoalStore 账本 | `core/goal/store.py` | replace_goal + append_evidence + complete_lite/update_status | 无 |
| goal context 注入 | `core/goal/context.py` | executor 注入 agent prompt（已有） | 无 |
| event_bus → SSE | `api/session/event_bus_v2` | study_* 事件发射 | 无 |
| /goal 命令模式 + API 模式 | `chat.py:515` / `api/routers/goal.py` | /study 命令 + /api/study/* 完全套用 | 无 |
| workspace 结构 | `strategies/{name}/runs/` | 直接使用 | 无 |

**结论**：后端执行链 90% 已存在，study 层是编排 + 持久化执行状态 + 事件 +
指标停止条件的薄封装。

## 4. 核心处理流程

### 4.1 单轮抽取（唯一既有改动，低风险）

把 `cmd_autoresearch` 的 while 循环体（L165-530）提取为可复用单轮函数：

```python
# core/autoresearch.py 新增
def run_research_round(
    workspace_path: Path,
    strategy_name: str,
    round_num: int,
    *,
    run_dir: Path | None = None,         # None 则自动创建
    session_id: str | None = None,       # 传给 _append_backtest_evidence
    acceptance_config: AcceptanceConfig | None = None,  # None 用默认
    max_retries: int = 3,
    cooldown_base: float = 30.0,
    cooldown_jitter: float = 10.0,
    min_cooldown: float = 1.0,
    lazy_detection_interval: int = 10,
    keep_recent: int = 10,
    behavior: str | None = None,         # None 走 should_use_real_llm
    round_callbacks: dict | None = None, # on_agent_spawn/on_backtest/on_decide
) -> dict:
    """执行一轮 autoresearch，返回 RoundResult dict。

    RoundResult = {
        round_num, run_name, run_dir,
        metrics,           # Calmar/Sharpe/MaxDD/...
        verdict,           # keep|discard
        decision,          # AcceptanceDecision.to_dict()
        agent_outputs,     # 9 个 agent 的输出
        summary,           # generate_run_summary 结果
    }
    """
```

**参数化的两点**：
1. `session_id` — 用于 `_append_backtest_evidence` / `_register_researcher_hypothesis`
   （替换写死的 `autoresearch-{strategy}`）
2. `acceptance_config` — study 的 `metric_targets` 映射为 `AcceptanceConfig` overrides

**CLI 改造**：`cmd_autoresearch` 循环体改为调 `run_research_round()`，CLI 打印
逻辑下沉到回调或保留在 wrapper（行为不变，回归测试守护）。

### 4.2 AutoresearchExecutor 主循环

```python
# core/study/executor.py
class AutoresearchExecutor:
    async def run(self, study: StudyRecord):
        await self._emit("study_started", study_id, round=0)
        while True:
            # 1. 检查点：暂停 / 取消
            if self._is_paused: await self._wait_resume()
            if self._is_cancelled: break
            # 2. 执行单轮（同步 autoresearch -> to_thread）
            result = await asyncio.to_thread(
                run_research_round,
                workspace_path=study.workspace_path,
                strategy_name=study.strategy_name,
                round_num=study.current_round + 1,
                session_id=study.session_id,
                acceptance_config=self._acceptance_config(study),
                cooldown_base=study.cooldown_base,
                cooldown_jitter=study.cooldown_jitter,
                min_cooldown=study.min_cooldown,
            )
            study.current_round += 1
            store.update_round_heartbeat(study.study_id, study.current_round)
            # 3. 达标检测 + 覆盖 criterion
            if self._meets_targets(result["metrics"], study.metric_targets):
                self._cover_criterion(study, result)         # 指标达标覆盖标准
                await self._complete_goal(study, result)     # complete_lite / audit
                store.mark_study_status(study.study_id, StudyStatus.COMPLETE)
                await self._emit("study_completed", ...)
                break
            # 4. 预算执行 + stagnation
            if self._budget_exceeded(study):
                store.mark_study_status(study.study_id, StudyStatus.BUDGET_LIMITED)
                await self._emit("study_budget_limited", ...); break
            if result["decision"]["stagnation_triggered"] or \
               (study.max_rounds and study.current_round >= study.max_rounds):
                store.mark_study_status(study.study_id, StudyStatus.ERROR)
                await self._emit("study_failed", reason="stagnation/max_rounds"); break
            # 5. 事件 + 轮间 cooldown
            await self._emit("study_round", round=study.current_round,
                             run=result["run_name"], metrics=result["metrics"],
                             verdict=result["verdict"],
                             agent_statuses=result["summary"].get("agent_statuses", {}))
            await asyncio.sleep(self._round_cooldown(study))
        store.mark_study_stopped_at(study.study_id)  # heartbeat completed_at
```

**指标达标检测**（复用既有逻辑，不重复实现阈值比较）：

```python
def _acceptance_config(self, study):
    """metric_targets -> AcceptanceConfig overrides."""
    overrides = {}
    for target in study.metric_targets:
        if target["name"] == "calmar": overrides["hard_calmar_min"] = target["value"]
        if target["name"] == "sharpe": overrides["hard_sharpe_min"] = target["value"]
        if target["name"] == "max_dd": overrides["hard_max_dd_min"] = target["value"]
    return DEFAULT_CONFIG.with_overrides(**overrides)

def _meets_targets(self, metrics, targets):
    """与 _acceptance_config 的 hard_passed 等价比较。"""
    for t in targets:
        val = metrics.get(t["name"], -1e9)
        if t["op"] == ">=" and not (val >= t["value"]): return False
        elif t["op"] == "<=" and not (val <= t["value"]): return False
        elif t["op"] == ">" and not (val > t["value"]): return False
        elif t["op"] == "<" and not (val < t["value"]): return False
    return True
```

`AcceptanceConfig` 默认已含 `hard_calmar_min=0.5 / sharpe_min=0.3 /
max_dd_min=-0.15` —— 与用户最初提的指标目标完全对齐，即 metric_targets 不传时
走默认 `decide()` 阈值即可。

### 4.3 指标达标 → goal criteria 覆盖闭环

```
每轮 run_backtest_script → metrics
  └─► _append_backtest_evidence(session_id, run_name, metrics, strategist_output)
        └─► GoalStore.append_evidence → 账本 evidence +1，覆盖 criterion[0]
达标 → _meets_targets(metrics, metric_targets) == True
  └─► _cover_criterion(study, result)：
        余下 criteria 逐条标记 covered + 落最终 evidence
  └─► GoalStore.complete_lite(..., recap=...) → goal.status=complete
        或 update_status(COMPLETE, audit=...)（完整审计模式）
  └─► StudyStore.mark_study_status(study_id, COMPLETE)
  └─► study_completed 事件
```

**metric_targets 默认映射到 3 个标准 goal criteria**（来自 `default_goal_criteria`）+
追加研究指标项，使 progress_percent 也随达标向上推进。

### 4.4 断点续跑

- 重启后 scheduler 扫描 `studies WHERE execution_status IN ('running','queued')`
  + 内存守卫（仿 chat reload-recovery 模式：member `_active_studies` / session
  queue）筛掉重启僵尸
- running → 从 `runs/run_XXXX/` 最大编号续跑（`read_current_state` 已能
  推断 `total_runs`）
- 无 run → 从 round 1 重新开始

## 5. 数据模型

### 5.1 studies 表（goals.db 同库加表，迁移）

```sql
CREATE TABLE IF NOT EXISTS studies (
    study_id            TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    goal_id             TEXT,                    -- 关联 goal 账本
    objective           TEXT NOT NULL,
    executor_type       TEXT NOT NULL DEFAULT 'autoresearch',
                                            -- 'autoresearch' | 'workflow'
    workspace_path      TEXT NOT NULL,
    strategy_name       TEXT NOT NULL,
    metric_targets      TEXT,                    -- JSON: [{"name":"calmar","op":">=","value":0.5}]
    budget_token        INTEGER,
    budget_turn         INTEGER,
    budget_time_seconds INTEGER,
    cooldown_base       REAL NOT NULL DEFAULT 30.0,
    cooldown_jitter     REAL NOT NULL DEFAULT 10.0,
    min_cooldown        REAL NOT NULL DEFAULT 1.0,
    max_rounds          INTEGER,                 -- None = 无限
    lazy_detection_interval INTEGER NOT NULL DEFAULT 10,
    keep_recent         INTEGER NOT NULL DEFAULT 10,
    behavior            TEXT,                     -- None 走 should_use_real_llm；'static'/'varying'/'improving' 强制 stub
    execution_status    TEXT NOT NULL DEFAULT 'queued',
                                            -- queued | running | paused | error
                                            -- | complete | cancelled | budget_limited
    current_round       INTEGER NOT NULL DEFAULT 0,
    last_metrics        TEXT,                    -- JSON：最近一轮指标
    last_verdict        TEXT,                    -- keep | discard
    last_error          TEXT,
    heartbeat           TEXT,                    -- ISO，调度健康
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    completed_at        TEXT,
    FOREIGN KEY (goal_id) REFERENCES goals(goal_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_studies_session ON studies(session_id);
CREATE INDEX IF NOT EXISTS idx_studies_status ON studies(execution_status);
```

### 5.2 StudyRecord / StudyStatus（models.py）

```python
class StudyStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    BUDGET_LIMITED = "budget_limited"

@dataclass(frozen=True)
class StudyRecord:
    study_id: str
    session_id: str
    goal_id: str | None
    objective: str
    executor_type: str
    workspace_path: str
    strategy_name: str
    metric_targets: list[dict]   # 解析后的 list
    budget_token: int | None
    budget_turn: int | None
    budget_time_seconds: int | None
    cooldown_base: float
    cooldown_jitter: float
    min_cooldown: float
    max_rounds: int | None
    lazy_detection_interval: int
    keep_recent: int
    behavior: str | None
    execution_status: StudyStatus
    current_round: int
    last_metrics: dict | None
    last_verdict: str | None
    last_error: str | None
    heartbeat: str
    created_at: str
    updated_at: str
    completed_at: str | None
```

## 6. 事件协议（study_* → SSE）

前端 `metaHandlers.ts` 已预留 goal_* handler 结构，沿用相同形态：

| 事件 | payload | 时点 |
|---|---|---|
| `study_queued` | {study_id, session_id, objective} | 入队 |
| `study_started` | {study_id, round:0} | executor 启动 |
| `study_round` | {study_id, round, run, metrics, verdict, agent_statuses} | 每轮结束 |
| `study_evidence` | {study_id, evidence_id, criterion_id, run} | 每轮落证据 |
| `study_progress` | {study_id, covered, total, percent} | 覆盖变化 |
| `study_completed` | {study_id, goal_id, metrics, recap} | 达标完成 |
| `study_failed` | {study_id, error, reason} | error/stagnation |
| `study_budget_limited` | {study_id, used} | 预算耗尽 |
| `study_paused` | {study_id, round} | 暂停 |
| `study_resumed` | {study_id, round} | 恢复 |
| `study_cancelled` | {study_id} | 取消 |

## 7. StudyStore API（store.py）

```python
class StudyStore:
    def create_study(...) -> StudyRecord
    def get_study(study_id) -> StudyRecord | None
    def get_active_study(session_id) -> StudyRecord | None  # 当前 running/queued
    def list_studies(session_id=None, status=None, limit=50) -> list[StudyRecord]
    def update_execution_status(study_id, status, *, last_error=None, last_metrics=None, last_verdict=None) -> StudyRecord
    def update_round_heartbeat(study_id, current_round) -> None
    def update_last_metrics(study_id, metrics, verdict) -> None
    def mark_complete(study_id, completed_at) -> None
    def mark_stopped_at(study_id, completed_at) -> None  # 任意停止
    def list_active_studies() -> list[StudyRecord]  # running/queued（重启恢复）
    def delete_session_studies(session_id) -> int
```

## 8. StudyScheduler（scheduler.py）

```python
class StudyScheduler:
    def __init__(self, event_bus, session_service): ...
    _active_studies: dict[str, asyncio.Task]        # study_id -> task
    _session_queues: dict[str, asyncio.Queue]       # session_id -> queue
    _processing_sessions: set[str]
    _session_locks: dict[str, asyncio.Lock]         # 与 chat 互斥

    async def submit_study(self, study: StudyRecord) -> None:
        """入队 study 到 session 队列（与 chat attempt 互斥）。"""
    async def _run_session_loop(self, session_id) -> None:
        """串行消费 session 队列里的 study。"""
    async def _execute_study(self, study: StudyRecord) -> None:
        """建 executor → 跑 → 完成。"""
    def pause_study(self, study_id) -> None
    def resume_study(self, study_id) -> None
    def cancel_study(self, study_id) -> None
    def recover_on_startup(self) -> list[StudyRecord]:
        """扫 list_active_studies + 内存守卫 → 重入队。"""
```

**与 chat 队列互斥**：复用 session service 暴露的 session 锁注册 / 队列入
口（若 session_service 已有 `_session_queues` / `_processing_sessions`，study
命令共用同一 session 锁；否则新增轻量 `_session_study_locks` 与 chat 锁配合）。
关键约束：同一 session 同一时刻只跑一个长程 agent loop（chat 消息或 study
任一）。

## 9. API / 命令

### 9.1 API（api/routers/study.py）
```
POST /api/study/start
  body: {session_id, objective, workspace_path, strategy_name,
         metric_targets?, budget_token?, budget_turn?, budget_time_seconds?,
         max_rounds?, behavior?, cooldown_base?, cooldown_jitter?,
         min_cooldown?}
  → {study_id, goal_id, status:"queued"}

GET  /api/study/status?session_id=
  → {study, goal_snapshot, progress:{covered,total,percent},
     current_round, last_metrics, last_verdict}

POST /api/study/{study_id}/pause | resume | cancel

GET  /api/study/list?session_id=&status=&limit=
  → {studies:[...]}
```

### 9.2 chat slash 命令（chat.py，仿 /goal 命令拦截）
```
/study start <objective> [--workspace W] [--strategy S]
          [--metric calmar>=0.5,sharpe>=0.3]
          [--budget-token N] [--budget-turn N] [--budget-time S]
          [--max-rounds N] [--behavior static|varying|improving]
          [--cooldown S] [--jitter S] [--min-cooldown S]
/study status                          — 当前 study 状态
/study pause | resume | cancel
/study list [status]
/study help
```

## 10. 测试策略

| 测试文件 | 覆盖 | 说明 |
|---|---|---|
| `tests/test_autoresearch_round_added.py` | run_research_round 抽取回归 | stub `AUTORESEARCH_BEHAVIOR`；与原循环行为等价 |
| `tests/test_study_models.py` | StudyRecord / StudyStatus / 序列化 | |
| `tests/test_study_store.py` | studies 表迁移 + CRUD + list_active | |
| `tests/test_study_executor.py` | 单轮 mock + 达标停止 + 预算强制 + stagnation | `_spawn_agent` mock |
| `tests/test_study_scheduler.py` | session 互斥 / 重启恢复 / 事件发射 | mock executor |
| `tests/test_api_study.py` | API 形状 + 鉴权 | HMAC-SHA256（学评估 P1-1 经验） |
| e2e | 全链路：study start → 多轮 → 达标 → complete | `AUTORESEARCH_BEHAVIOR=improving` 模拟达标 |

**回归保护**：抽取 `run_research_round` 前后，跑既有 autoresearch 测试
（`tests/test_p3_integration.py` 等）确认行为不变。

## 11. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 单轮抽取破坏 CLI 行为 | 高 | 行为回归测试（stub mode）+ 降级方案：executor 直接调用 cli 私有函数（不抽取，牺牲复用） |
| session 互斥集成复杂 | 中 | 确认 session_service 是否暴露注册入口；若无，study 新增 `_session_study_locks` 与 chat 锁协调 |
| LLM 成本（8-agent × 多轮 × LLM） | 中 | 默认 budget 护栏 + `AUTORESEARCH_BEHAVIOR=stub` 跑 CI + 用户显式同意 |
| autoresearch 死循环 | 中 | max_rounds 默认值 + stagnation（已有，10 轮无 keep 自动停）+ study 层预算 |
| workspace 不存在/策略未初始化 | 低 | start 前校验 workspace / strategy 目录 |

## 12. 实施阶段

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 设计文档（本文档） | ✅ |
| 1 | 后端最小闭环（store/executor/scheduler/入口/测试） | ✅ |
| 1.1 | autoresearch 单轮抽取 + CLI 回归 | ✅ |
| 1.2 | core/study/{models,store}.py | ✅ |
| 1.3 | core/study/executor.py | ✅ |
| 1.4 | core/study/scheduler.py | ✅ |
| 1.5 | 入口（chat.py /study + api/routers/study.py） | ✅ |
| 1.6 | 测试 + e2e | ✅ |
| 2 | 执行中交互（directives 注入 / redirect 命令 / API） | ✅ |
| 3 | 监控迭代（MONITORING / NEEDS_REFRESH / drift 检测） | ✅ |
| 4 | 多 goal 并行 + 资源配额 | ⏸️ 暂缓 |
| 5 | 前端全量（StudyTab / 创建表单 / 进度面板 / 控制 / SSE） | ✅ |

### 提交记录

| Commit | 内容 |
|---|---|
| `f7ce168` | 设计文档 |
| `8ab5f44` | Phase 1 后端最小闭环（study 核心 + 单轮抽取 + 入口 + 测试） |
| `b4cdc61` | Phase 2 执行中交互（study_directives 表 / redirect / 注入） |
| `306ac0a` | Phase 3 监控迭代（MONITORING / NEEDS_REFRESH / drift） |
| `35eec5a` | Phase 5 前端全量（StudyTab / 创建表单 / 进度视图） |
| （SSE 接线）| 前端 study_* SSE handler + scheduler emitter 修复 |

## 13. 扩展点（为后续阶段预留）

- **executors 注册表**：StudyExecutor 协议（start/pause/resume/cancel/status），
  当前注册 AutoresearchExecutor；Phase 4 注册 WorkflowExecutor（包已有
  GoalWorkflowRunner）；未来可注册自定义 executor
- **指标目标模型**：`metric_targets` JSON 支持 `name/op/value` 三元组，可
  扩展 `threshold`/`window`/`confidence`（监控阶段用）；当前 op 支持
  `>=`/`<=`/`>`/`<`/`==`
- **预算模型**：token/turn/time 三维，未来可加 cost 美元预算、并发配额
- **事件 hooks**：scheduler 暴露事件订阅（观察者模式，已有 event_bus）
- **断点存储**：当前用 studies 表 + runs/ 目录恢复；未来可接 checkpoint_store
  （goal workflow 已有）

## 14. 文件清单

### 新增
```
src/strategy_research/core/study/
  __init__.py         — 导出
  models.py           — StudyRecord / StudyStatus
  store.py            — StudyStore SQLite
  executor.py         — AutoresearchExecutor（核心执行循环）
  scheduler.py        — StudyScheduler（队列 + 事件 + 恢复）
src/strategy_research/api/routers/study.py   — /api/study/*
tests/
  test_study_models.py
  test_study_store.py
  test_study_executor.py
  test_study_scheduler.py
  test_api_study.py
  test_autoresearch_round_added.py           — 抽取回归
docs/archive/undated/study-longhorizon-plan.md               — 本文档
```

### 修改（最小化）
```
src/strategy_research/core/autoresearch.py        — + run_research_round()
src/strategy_research/cli/commands/autoresearch.py — cmd 改调单轮函数（行为不变）
                                                   — _append_backtest_evidence 加 session_id 参数
                                                   — _register_researcher_hypothesis 同上
src/strategy_research/api/routers/chat.py          — + /study 命令拦截
src/strategy_research/api/__init__.py или main app   — 注册 study 路由
```

### 不动
```
core/goal/*                          账本不变
core/backtest.py                     run_backtest_script 不变
core/strategy_acceptance/*          复用不变
core/autoresearch.py 其余函数         工具函数不变
```