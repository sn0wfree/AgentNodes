# 定时研究 × study 联动设计（scheduled_research → study）

> 版本：v1（2026-08-14）· 状态：设计定稿 · 实现：S1-S8 分步
> 前置：`docs/archive/undated/study-longhorizon-v2-design.md`（study v2 权威设计）
> 关联：`core/scheduled_research/*`（原独立定时研究系统）

## 1. 背景与目标

### 1.1 现状问题

`scheduled_research` 是独立于 study 的定时系统：

| 项 | 现状 | 问题 |
|---|---|---|
| 触发执行 | 默认 dispatch = subprocess `agentnodes autoresearch` | 走旧 CLI 单轮循环，无执行状态持久化、无断点续跑、无指标停止、前端不可见——与 v2 study 全部能力失联 |
| 存储 | JSON 文件 `~/.agentnodes/scheduled_jobs.json` | 与项目 SQLite 存储风格脱节，无事务/外键 |
| 运行模式 | 独立后台线程 + 独立 event loop | 无法直接调用 async `StudyScheduler.submit` |
| 入口 | 仅 CLI | 无 API、无 server 生命周期集成 |

### 1.2 目标

1. **定时触发 → study**：job 到点后在**进程内**创建 study（objective=prompt，config 透传
   metric_targets/budget/guidance_md/monitor_interval_seconds）并自动执行
2. **存量统一**：全部现有 job 迁移到 `target='study'`（保留 `'autoresearch'` 兜底分支）
3. **存储统一**：`scheduled_jobs` 表迁入 goals.db（与 StudyStore 同库），JSON 幂等迁移
4. **模式统一**：执行器 asyncio-first（主 loop task），CLI 单 loop 兼容
5. **入口补全**：`/api/schedule/*` CRUD + run 端点 + server lifespan 集成

## 2. 架构总览

```
┌─ 入口层 ──────────────────────────────────────────────────────┐
│  POST /api/schedule/*（CRUD/run）                             │
│  CLI: agentnodes schedule create|list|show|run|start │
└──────────────────────┬────────────────────────────────────────┘
                       ▼
┌─ 持久层：core/scheduled_research/store.py（SQLite 重写）──────┐
│  scheduled_jobs 表（goals.db 同库）                           │
│  migrate_from_json()：~/.agentnodes/scheduled_jobs.json → 表 │
└──────────────────────┬────────────────────────────────────────┘
                       ▼
┌─ 调度层：ScheduledResearchExecutor（asyncio-first）───────────┐
│  start(loop) → create_task(tick loop)                        │
│  tick → is_due(job) → dispatch(job)                          │
│    └─ dispatch_fn 默认 = study 桥（bootstrap 共享编排）        │
└──────────────────────┬────────────────────────────────────────┘
                       ▼
┌─ 执行层：core/study/bootstrap.py（共享编排，新）──────────────┐
│  create_and_queue_study(...)                                  │
│    = create_study + replace_goal + _init_study_dir           │
│      + StudyScheduler.submit                                 │
│  （api/routers/study.py 的 study_start 同源复用）              │
└──────────────────────┬────────────────────────────────────────┘
                       ▼
        StudyScheduler → AutoresearchRunner（v2 全链路）
```

## 3. 数据模型

### 3.1 `scheduled_jobs` 表（goals.db 同库）

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id           TEXT PRIMARY KEY,
    workspace        TEXT NOT NULL,
    strategy_name    TEXT NOT NULL,
    prompt           TEXT NOT NULL DEFAULT '',
    cron             TEXT NOT NULL DEFAULT '',
    interval_ms      INTEGER NOT NULL DEFAULT 0,
    next_run_at      REAL NOT NULL,
    created_at       REAL NOT NULL,
    last_run_at      REAL,
    last_run_id      TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    config           TEXT NOT NULL DEFAULT '{}',   -- JSON: metric_targets/budget/guidance_md/monitor_interval_seconds
    max_rounds       INTEGER NOT NULL DEFAULT 1,
    target           TEXT NOT NULL DEFAULT 'study',  -- 'study' | 'autoresearch'
    owner_session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_status ON scheduled_jobs(status);
```

### 3.2 模型扩展（models.py）

```python
@dataclass
class ScheduledResearchJob:
    id: str = ""
    workspace: str = ""
    strategy_name: str = ""
    prompt: str = ""
    cron: str = ""
    interval_ms: int = 0
    next_run_at: float = 0.0
    created_at: float = 0.0
    last_run_at: float | None = None
    last_run_id: str | None = None   # v2 语义：study_id
    status: JobStatus = JobStatus.PENDING
    config: dict = field(default_factory=dict)
    max_rounds: int = 1
    target: str = "study"            # 'study' | 'autoresearch'
    owner_session_id: str | None = None

    def study_params(self) -> dict:
        """config → StudyStartRequest 兼容参数字典（桥接用）。"""
        return {
            "metric_targets": self.config.get("metric_targets"),
            "budget_token": self.config.get("budget_token"),
            "budget_turn": self.config.get("budget_turn"),
            "budget_time_seconds": self.config.get("budget_time_seconds"),
            "guidance_md": self.config.get("guidance_md"),
            "monitor_interval_seconds": self.config.get("monitor_interval_seconds"),
            "cooldown_base": self.config.get("cooldown_base", 30.0),
            "cooldown_jitter": self.config.get("cooldown_jitter", 10.0),
            "min_cooldown": self.config.get("min_cooldown", 1.0),
            "max_rounds": self.config.get("max_rounds", self.max_rounds),
            "behavior": self.config.get("behavior"),
            "keep_recent": self.config.get("keep_recent", 10),
            "lazy_detection_interval": self.config.get("lazy_detection_interval", 10),
        }
```

## 4. 核心流程

### 4.1 定时触发 → study（dispatch 桥）

```
tick（60s，主 loop）
  └─ job.is_due(now) → job.status=RUNNING, last_run_at=now → dispatch(job)
      └─ bootstrap.create_and_queue_study(
             owner_session_id=job.owner_session_id,
             objective=job.prompt,
             workspace_path=job.workspace,
             strategy_name=job.strategy_name,
             **job.study_params(),
         )
      └─ 成功：job.last_run_id = study_id；status=COMPLETED
      └─ 失败：status=FAILED；config["last_error"]=str(exc)
      └─ 周期性：next_run_at = cron 下一触发 / now + interval_ms
```

- `dispatch_fn` 保持注入点（测试可 stub），默认指向 study 桥
- 桥必须是 async（调 async submit）——executor asyncio-first 后天然满足

### 4.2 共享编排（core/study/bootstrap.py）

把 `api/routers/study.py` 的 `study_start` 主体抽为共享函数：

```python
def create_and_queue_study(
    *,
    owner_session_id: str,
    objective: str,
    workspace_path: str | Path,
    strategy_name: str,
    executor_type: str = "autoresearch",
    metric_targets: list[dict] | None = None,
    budget_token: int | None = None,
    budget_turn: int | None = None,
    budget_time_seconds: int | None = None,
    cooldown_base: float = 30.0,
    cooldown_jitter: float = 10.0,
    min_cooldown: float = 1.0,
    max_rounds: int | None = None,
    behavior: str | None = None,
    monitor_interval_seconds: int | None = None,
    guidance_md: str | None = None,
    scheduler: StudyScheduler | None = None,
) -> dict:
    """创建 study（账本+自治目录）+ 入队；返回 {study_id, goal_id, status}。"""
```

- workspace/strategy 校验（存在性、路径穿越防护、minimal strategy 引导）随之**下移**到 core
- `_init_study_dir`（v2 §6 自治目录引导）从 api 层移入 bootstrap
- 返回 dict 供 API 直接序列化；调度桥只取 study_id
- API 保留 IDOR 鉴权在路由层，编排层不碰 request

### 4.3 executor asyncio-first

```python
class ScheduledResearchExecutor:
    def __init__(self, store, tick_interval=60.0,
                 dispatch_fn: Callable[[ScheduledResearchJob], Awaitable[None]] | None = None):
        ...
    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """loop 传入 = 主 loop task 模式（server）；None = 自建线程 loop（CLI 兼容）。"""
        if loop is not None:
            self._task = loop.create_task(self._run_loop())
        else:
            # 线程模式（旧行为保留给 CLI）
    def run_once(self, job_id: str) -> bool: ...
    async def dispatch_async(self, job): ...
```

- tick 中 `await self._dispatch_async(job)`（dispatch 成功才更新 next_run_at，防重触发）
- 旧线程模式与 asyncio 模式共用 `_tick`（线程模式经 `run_in_executor` 调同步 `_dispatch` 壳）

### 4.4 CLI 改造

- `schedule create`：加 `--metric calmar>=0.5,sharpe>=0.3`（逗号分隔）、`--budget-turn N`、
  `--budget-token N`、`--budget-time S`、`--guidance-file PATH`、`--monitor-interval S`
  （写入 config）；默认 `target='study'`；保留 `--max-rounds`（映射到 config.max_rounds）
- `schedule start`：asyncio 单 loop——内建 `StudyScheduler(StudyStore(), session_service=None)`
  （NullEmitter）+ `ScheduledResearchExecutor.start(loop=主loop)`，Ctrl+C 统一停机
- `schedule run <id>`：asyncio.run → dispatch 一次（走 study 桥）

### 4.5 API + server 生命周期

- `api/routers/schedule.py`：`/api/schedule/list|create|show|delete|cancel|run`
  - create 带 `owner_session_id`（IDOR：创建者只能操作自己的 job，或 job 无 owner 时仅管理员/CLI）
  - 复用 `StudyStartRequest` 同款字段校验
- `api/app.py` lifespan：启动时 `ScheduledResearchStore().migrate_from_json()` +
  `executor.start(loop=主loop)`；shutdown 时 `executor.stop()`；启动即 `recover_stale_running()`

## 5. 兼容与迁移

| 项 | 策略 |
|---|---|
| 存量 JSON | 启动迁移：读 JSON → 写表 → rename `.migrated`（幂等，重放无害） |
| 存量 job target | 统一 `'study'`（决策：全部统一到 study） |
| `'autoresearch'` 兜底 | 保留 subprocess dispatch 分支（防御性，新 job 不再产生） |
| CLI 线程模式 | 保留 `start(loop=None)` 路径，`cmd_schedule_start` 改走 asyncio 单 loop |
| API 行为 | `/study/start` 路由签名不变（内部改调 bootstrap），test_study_api 全绿守护 |

## 6. 测试策略

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_scheduled_jobs_store.py` | SQLite CRUD + JSON→SQLite 迁移幂等 + 损坏 JSON 兜底 |
| `tests/test_scheduled_executor.py` | tick 到点 dispatch（async stub）、next_run_at 更新、recover、stop |
| `tests/test_schedule_api.py` | 端点形状 + IDOR 鉴权 |
| `tests/test_schedule_to_study.py` | job → study 全链路（stub behavior，短 interval 断言跑完 + last_run_id） |

回归：`test_study_api.py`（bootstrap 抽取守护）；全量回归（不含 test_session_triggers.py）。

## 7. 风险与对策

| 风险 | 等级 | 缓解 |
|---|---|---|
| study_start 重构改变行为 | 中 | 编排层抽核心逻辑，路由层只留鉴权/序列化；test_study_api 回归 |
| uvicorn 多 worker 双触发 | 低 | 沿用现状（tick 无进程锁），文档标注；部署单 worker |
| 迁移重放/损坏 | 低 | `.migrated` 标记 + 损坏文件 rename `.corrupt-<ts>`（沿用旧逻辑） |
| CLI 线程→asyncio 行为变化 | 低 | 主流程等价；Ctrl+C 保持干净退出 |
| study 桥并发（同 job 短 interval 重入） | 低 | is_due 窗口 + dispatch 后立即更新 next_run_at |

## 8. 实施步骤

| 步骤 | 内容 | 验收 |
|---|---|---|
| S1 | 设计文档（本文档） | ✅ git commit `5820eb0` |
| S2 | models.py 扩展 + store.py SQLite 重写 + 迁移 | ✅ `3379671`（41 tests） |
| S3 | bootstrap.py 抽取 + study.py 改调 | ✅ `6fbcd10`（study 系 1145 tests 回归） |
| S4 | executor.py asyncio-first + study 桥 | ✅ `27102f9`（55 tests） |
| S5 | cli.py 改造 | ✅ `c408789` |
| S6 | api/routers/schedule.py + app.py lifespan | ✅ `856aef6`（12 tests） |
| S7 | 测试补全 + ruff + 全量回归 | ✅ `4a2c751`；全量 **10769 passed / 0 failed**（auth_tokens 单测 flaky 环境串扰，单独跑通过） |

### 实施记录（S2-S7 落地要点）

- **S2**：`scheduled_jobs` 表进 goals.db（`AGENTNODES_GOAL_DB_PATH` 同 env）；
  `migrate_from_json` 幂等（`.migrated` 标记 / `.corrupt-<ts>` 损坏兜底）；存量 target 统一 'study'；
  旧 `save()`/`DEFAULT_STORE_PATH` 删除（无外部引用）
- **S3**：`core/study/bootstrap.py` 承接 API 层全部创建编排——`validate_workspace_strategy`（含路径穿越防护）、
  `init_study_dir`（v2 §6 自治目录引导）、`create_study_record`（同步）、`create_and_queue_study`（async 便捷版）；
  `study_start` autoresearch 分支改调，保留 create_task+log_task_exception 模式（测试守护）
- **S4**：executor 双模式（`start(loop)` task 模式 / 无 loop 线程模式）；`dispatch_fn` 支持 sync/async；
  `_dispatch_by_target` 按 `job.target` 分派（study 桥 / subprocess 兜底）；last_run_id=study_id
- **S5**：CLI `create` 加 `--metric/--budget-*/--monitor-interval/--guidance-file`；`run`/`start` 改
  asyncio 单 loop + 内建 `StudyScheduler`（NullEmitter），`run` 等待 study 终态
- **S6**：`/api/schedule/{create,list,show,cancel,delete,run}` 全 IDOR（owner_session_id 匹配，CLI job 不可 API 变更）；
  lifespan 启动定时守护 + JSON 迁移；create 校验 workspace 存在 + cron
- **S7**：CLI job 无 owner → dispatch 回退 `cli:{job_id}`（owner 非空约束）；cron 周期 job dispatch 后重置 PENDING + 重排

## 9. 文件清单

### 新增
```
src/agentnodes/core/study/bootstrap.py   — 共享编排 create_and_queue_study
src/agentnodes/api/routers/schedule.py   — /api/schedule/*
tests/test_scheduled_jobs_store.py
tests/test_scheduled_executor.py
tests/test_schedule_api.py
tests/test_schedule_to_study.py
docs/archive/undated/scheduled-study-link-plan.md               — 本文档
```

### 修改
```
core/scheduled_research/models.py    — +target/owner_session_id/study_params
core/scheduled_research/store.py     — SQLite 重写 + migrate_from_json
core/scheduled_research/executor.py  — asyncio-first + study 桥
core/scheduled_research/cli.py       — create 参数 + start/run asyncio 单 loop
core/scheduled_research/__init__.py  — 导出补全
api/routers/study.py                 — study_start 改调 bootstrap
api/app.py                           — lifespan 接线 + 迁移
```

### 不动
```
core/study/{runner,scheduler,store,state_store,round_manifest}.py   — v2 执行链零改动
core/goal/*                                                          — 账本零改动
core/autoresearch.py / core/backtest.py                              — 引擎零改动
```
