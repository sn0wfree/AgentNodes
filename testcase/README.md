# testcase/ — 拆分过程中的金标准快照

> 用途：AgentNodes 做减法时，**被移除的业务代码不直接丢弃**，而是先归档到这里，
> 作为后续可复用的测试案例与对拍基准。
>
> 决策（用户，2026-11-04）：全部作金标准快照保留；按「可运行的端到端样本」组织。

---

## 0. 为什么要有这个目录

拆分后 AgentNodes 只剩 agent 框架，业务能力（回测/因子/数据/研究）全部移走。
但那些代码里有大量**有价值的测试资产**：

- **可运行样本**：一份 YAML 配置 + 小份 OHLCV + 一个因子实现 → 端到端验证
  框架的 tool 注册、回测工具接口、DataStore 接缝是否还能接线
- **金标准输出**：回测期望指标、IC 序列、因子值矩阵 → 框架侧改动后的对拍基准
- **契约测试**：8 个 data_source loader 的统一契约、DataStore Protocol
- **边界用例**：HITL 审批、预算超限、compaction 触发条件等

保留它们，等于给「框架 + 业务」之间的**接缝**留了一套回归网。

---

## 1. 目录约定

```
testcase/
├── README.md              本文件：约定与索引
├── golden/                金标准快照（原样副本，按原路径结构镜像）
│   ├── apps/              被移除的 apps 包完整副本
│   ├── foundation_utils/  被移除的 business utils 副本
│   ├── interfaces/        被移除的业务路由/CLI 命令副本
│   └── templates/         被移除的业务模板副本
├── samples/               可运行的端到端样本（从 golden 裁剪出的最小集）
├── tests/                 被移除的测试文件（保留原样，不被 pytest 收集）
└── fixtures/              可复用的测试数据（合成 OHLCV、YAML、期望输出）
```

### 关键约束

| 约束 | 说明 |
|---|---|
| **不被 pytest 收集** | `pyproject.toml` 的 `testpaths = ["tests"]`，`testcase/` 天然排除。**不要**把它加进 testpaths。 |
| **不进 ruff/mypy 门禁** | 快照代码不修 lint；在 `[tool.ruff.lint.per-file-ignores]` 里整体豁免。 |
| **可独立运行** | `samples/` 下每个样本自带 README，说明如何单独跑通。 |
| **镜像原路径** | `golden/apps/study/runner.py` 对应原 `src/strategy_research/apps/study/runner.py`，便于反查。 |

---

## 2. 每个样本的形态

`samples/<name>/` 目录下必须有：

```
samples/<name>/
├── README.md       做什么、怎么跑、验证什么、期望看到什么
├── run.py          可执行入口（或 pytest 文件）
├── input/          输入资产（YAML / CSV / 因子代码）
└── expected/       金标准输出（JSON，供对拍）
```

`run.py` 的**自检模式**：跑完自动与 `expected/` 对拍，输出 PASS/FAIL。
这样即使业务侧代码已移走，也能用 golden 副本复现。

---

## 3. 归档映射表

| 减法步骤 | 原路径 | 归档到 | 状态 |
|---|---|---|---|
| 2a | `tests/test_*.py`（170 个 business-only） | `testcase/tests/` | 待执行 |
| 2b | `foundation/utils/` 8 个业务文件 | `testcase/golden/foundation_utils/` | 待执行 |
| 2b | `foundation/utils/{covariance,ic_utils,risk_parity}.py`（死代码） | `testcase/golden/foundation_utils/dead/` | 待执行 |
| 2c | `interfaces/api/routers/{study,goal,hypothesis,validation,slash_commands}.py` 等 | `testcase/golden/interfaces/` | 待执行 |
| 2d | `interfaces/cli/__init__.py` 业务子命令段 | `testcase/golden/interfaces/cli/` | 待执行 |
| 2e | `apps/` 全包（609 文件） | `testcase/golden/apps/` | 待执行 |
| 2g | `kernel` 13 处接缝改动 | 不归档（改的是框架代码，git 历史可查） | — |

---

## 4. 与 git 的关系

- 这些快照**进 git**：它们是交付物的一部分，不是运行时垃圾。
- 体积控制：`apps/alpha_zoo/`（468 文件 / 25,669 行）是**生成产物**，
  归档时只保留**生成器 + 3 个代表性因子**，其余靠生成器重建。
  详情见 `golden/apps/alpha_zoo/README.md`。
- 需要完整历史时，`git show pre-split-20261104:src/strategy_research/apps/...` 可取回。

---

## 5. 索引（随归档进度更新）

### 批次 1（2026-11-04）：`alpha_zoo`

| 归档项 | 内容 | 说明 |
|---|---|---|
| `golden/apps/alpha_zoo/` | 880 文件 / 27,263 行 | loader(511) · yaml DSL 求值器(325) · AST→YAML 转换器(1,011) · 算子库(238) · 6 个代表性因子 · **408 个 YAML 定义** · README |

### 批次 2（2026-11-05）：goal / hypothesis / validation / portfolio / export

| 归档项 | 内容 | 说明 |
|---|---|---|
| `golden/apps/goal/` | 19 文件 / 6,147 行 | GoalStore 六表模型 · workflow 981 行 · CLI 371 行 · checkpoint/event_bus/dag_renderer |
| `golden/apps/hypothesis/` | 6 文件 / 1,459 行 | 假设登记 + 验证器（bootstrap/monte_carlo/walk_forward） |
| `golden/apps/validation/` | 9 文件 / 801 行 | 参数验证 · Monte Carlo · Bootstrap CI · Walk-Forward |
| `golden/apps/portfolio/` | 6 文件 / 619 行 | 组合构建 · 相关性 · 风险平价 |
| `golden/apps/export/` | 2 文件 / 302 行 | pine / tdx / vnpy 导出 |
| `golden/interfaces/` | 8 文件 / 2,604 行 | routers(goal/hypothesis/validation/workflow) · cli(slash_goal/export) · tui(dag_view/workflow_worker) |
| `tests/` | 35 个测试文件 | goal/hypothesis/validation 全量测试 + 6 个依赖已删包的混合测试 + 3 个 workflow 路由 API 测试（`test_workflow_approval_api.py` / `test_workflow_control_api.py` / `test_workflow_ownership.py`，测已移除的 `/api/goal/workflow/*` 面） |

### 批次 3（2026-11-05）：`study` / `autoresearch`

| 归档项 | 内容 | 说明 |
|---|---|---|
| `golden/apps/study/` | 35 文件 / ~10,700 行 | 调度器(1,125) · runner(1,277) · phase_engine(958) · store(1,594) · bootstrap(527) · graph_preset(397) · scenario_router(405) · models(312) · method/(7 文件) 等 |
| `golden/apps/autoresearch.py` | 1 文件 / 1,364 行 | 研究轮次编排 + 10 个角色 stub |
| `golden/interfaces/` | 4 文件 / ~2,900 行 | routers(study · study_helpers · slash_commands) · cli(autoresearch) |
| `tests/` | 29 个测试文件 | study 全量测试 + 依赖已删包的混合测试 |

**两个通用件迁到 L3（不归档）**：
- `kernel/orchestration/ticket.py`（325 行）：Ticket/Assignment 状态机
- `kernel/orchestration/ticket_store.py`（505 行）：持久化 + admit/settle/recover

**两个「名字带 study、实现零 study 语义」的通用件归位（不归档）**：
- `ensure_study_session` → `interfaces/api/session/web_session_db.py::ensure_session_row`
- `_default_db_path` → `kernel/orchestration/ticket_store.py::default_db_path`

**一个通用命令组保留**：`routers/slash_commands.py` 的 3 个通用 handler
（`/compact` / `/clear` / `/help`）迁到 `routers/slash_common.py`；
`/study` 系列（~400 行）随 study 移除。

**调度器骨架保留**：`kernel/scheduled_research/`（6 文件 1,194 行）不归档——
cron 计算 + `scheduled_jobs` 表 + asyncio 循环是通用能力；
只有 `target='study'` 失效，改为**显式抛错**（不静默 no-op）。

**两件残件并入 study 未归档**（仍在 `apps/study/`）：
- `objective_policy.py`（37 行）：拒绝实盘下单目标 —— scheduler.replace_objective 仍需
- `scoreboard.py`（210 行）：AEGIS 杠杆 Beta 后验 —— 消费者仅 phase_engine

**泛化重建路线图**：`docs/agentnodes-split-plan-20261104.md` §5.1
（objective + criteria + evidence 模式，去研究化后重建）。

