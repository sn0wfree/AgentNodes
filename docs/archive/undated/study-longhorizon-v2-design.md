# Study 长程任务系统设计文档 v2（微 session 自治架构）

> 版本：v2（2026-08） · 状态：设计定稿 · 实现：M0-M8 分步
> 前身：`docs/archive/undated/study-longhorizon-plan.md`（v1，autoresearch 服务化）
> 本文档为 v2 唯一权威设计来源，v1 与之冲突处以本文档为准。

## 目录

1. [背景与目标](#1-背景与目标)
2. [架构总览](#2-架构总览)
3. [核心概念模型](#3-核心概念模型)
4. [单身份设计（study_id = 会话身份）](#4-单身份设计study_id--会话身份)
5. [并行调度](#5-并行调度)
6. [study 自治目录](#6-study-自治目录)
7. [引擎路径参数化](#7-引擎路径参数化)
8. [round/run 模型与继承链](#8-roundrun-模型与继承链)
9. [每轮产物规范](#9-每轮产物规范)
10. [轮间评审循环](#10-轮间评审循环)
11. [外部信息收集](#11-外部信息收集)
12. [todos 机制](#12-todos-机制)
13. [guidance 人类判断点](#13-guidance-人类判断点)
14. [hypotheses 存储迁移（SQLite）](#14-hypotheses-存储迁移sqlite)
15. [Monitor 永续机制](#15-monitor-永续机制)
16. [事件清单](#16-事件清单)
17. [API 端点](#17-api-端点)
18. [风险与对策](#18-风险与对策)
19. [任务规划 M0-M8](#19-任务规划-m0-m8)
20. [附录](#20-附录)

---

## 1. 背景与目标

### 1.1 定位

**study** = 目标驱动的长程自动策略研发任务：用户给出研究目标 + 验收指标 +
预算，系统让 LLM **多轮自行迭代升级**（每轮 9-agent 研究链 + 回测 + 评估），
直至达标、预算耗尽或用户终止。任务可**永续运行**（完成后进入监控，漂移自动
修复），可**多任务并行**。

### 1.2 目标（v2 新增/强化）

| 目标 | 说明 |
|---|---|
| 多任务真并行 | 一个进程内多个 study 协程同时推进，互不干扰 |
| 任务自治 | 每个任务的一切产物在自己目录 `study/<id>/`，不共享策略目录 |
| 可跟踪可复盘 | 每轮沉淀结构化交接（manifest）+ 人类可读总结（summary.md/journal.md） |
| 信息不孤岛 | 轮间评审 + 外部信息收集，持续丰富知识储备与 idea |
| 方向不偏离 | 轮间评审检测与任务目标的偏离度，自动自我修正 |
| 人类判断点 | guidance.md 双层设定，每轮注入；可选硬校验 |
| 永续 | 达标 → 监控 → 漂移 → 自动修复 → 稳定 |

### 1.3 设计原则

1. **引擎不动**：`autoresearch.py`/`backtest.py` 研究轮子只做**路径参数化**
   （默认参数 = 旧行为），CLI/单轮路径零影响
2. **不兼容旧数据**：v2 起新 study 全部走新架构；旧 study 数据不迁移不兼容
3. **信息即产物**：每轮结束必须留下（a）机器交接 manifest（b）单轮总结
   summary.md（c）追加归档 journal.md
4. **决策留痕**：verdict/否决原因/评审结论/guidance 命中全部记录并注入下一轮

---

## 2. 架构总览

```
┌─ 入口层 ──────────────────────────────────────────────┐
│  POST /api/study/start（gates_yaml/guidance_md 可选） │
│  POST /api/study/{id}/{pause,resume,cancel,directive} │
│  GET  /api/study/status|list|{id}/summary|rounds|…    │
└───────────────┬───────────────────────────────────────┘
                ▼
┌─ 调度层：StudyScheduler（core/study/scheduler.py）────┐
│  每 study 一个微 session（study:{id}）                │
│  per-session 队列消费者 create_task（真并行）         │
│  全局 semaphore 限流（SR_STUDY_MAX_CONCURRENT=3）     │
└───────────────┬───────────────────────────────────────┘
                ▼
┌─ 执行层：AutoresearchRunner（core/study/runner.py）───┐
│  每轮：继承策略 → 9-agent 链 → 回测 → 评估 → verdict  │
│  → 三产物落盘 → ★轮间评审 → ★信息收集 → ★todos 更新  │
│  → monitor loop（达标后）                            │
└───────────────┬───────────────────────────────────────┘
                ▼
┌─ 自治产物：<workspace>/study/<study_id>/ ─────────────┐
│  guidance.md / state.json / journal.md / todos.md     │
│  / knowledge.md / results.tsv / baseline/             │
│  / rounds/round_NNNN/{manifest.json, summary.md,      │
│    run_XXXX/{strategy.py, agents/, metrics.json,…}}   │
└───────────────────────────────────────────────────────┘
```

---

## 3. 核心概念模型

| 概念 | 层级 | 定义 |
|---|---|---|
| **study** | 任务 | 一次长程研发任务；`session_id` = `study_id`（单身份，§4） |
| **round** | 任务层 | 一次完整研究迭代：继承→9-agent→回测→评估→verdict→交接；`rounds/round_NNNN/` |
| **run** | 引擎层 | 一次回测执行单元 + 完整产物；`round_NNNN/run_XXXX/`（每轮内独立编号） |
| **review** | 任务层 | 轮间综合评审：偏离度 + info_gap + todo_updates |
| **collect** | 任务层 | 外部信息收集（**轮首缺口检查为主触发** + 评审 info_gap / K 轮兜底），沉淀 knowledge.md |
| **todos** | 任务层 | 任务级子任务清单（todos.md），评审维护，每轮注入 |
| **guidance** | 任务层 | 全局+任务双层设定/人类判断点，每轮注入 |
| **manifest** | 产物 | 每轮机器可读交接（下一轮注入源） |

### 3.1 生命周期状态机

```
queued → running ⇄ paused
           │
           ├─→ complete ──→ monitoring ──→ needs_refresh ──→ running（自动修复）
           │                   │  (drift)          │(修复达标)
           │                   └──────▲────────────┘───→ monitoring
           ├─→ budget_limited / early_stopped / error
           └─→ cancelled（running 重启后由 recover 重入队恢复）
```

状态定义沿用 `core/study/models.py:StudyStatus`，v2 新增语义：
- `monitoring`：达标后周期回测验证（runner 移植 executor.py 的 monitor loop）
- `needs_refresh`：monitor 发现 drift → 自动修复轮（≤3 轮）→ 回 monitoring；
  修复无效停留 needs_refresh 等用户
- 连续 3 次评审 deviation=high → 自动停止（reason=repeated_deviation，可恢复）
- **停止语义**：repeated_deviation / review_failed 自动停止均落入 `error`
  状态 + reason 字段（沿用现有可 resume 语义，**不新增状态**）
- **resume 重置**：用户手动 resume 时 `continuous_deviation` 归零（防恢复后
  立即再次触发停止）；review_failed 停止 resume 时 `review_fail_count` 同理

### 3.2 断点续跑与恢复（v1 §4.4 保留 + v2 恢复锚点）

recover_on_startup（`scheduler.py:145`）保留：扫描 queued/running → 重入队。
v2 目录重构后恢复锚点改为 **state.json 的 `last_completed_round`**（不再从
runs/ 最大编号推断）：

| 崩溃时机 | 残留 | 恢复点 |
|---|---|---|
| 半轮中（run 已建，无 manifest） | `round_N/run_XXXX/` 残留 | 从 round_N 重跑；残留目录**忽略保留**（只读），**重跑时 run 编号取轮内 max+1**（写 run_0002…，残留 run 不再参与继承/TSV 归因） |
| 轮末（manifest 完整，review 未做） | 完整产物 | 从 round_{N+1} 开始；**评审不补跑**（轮后动作，丢一轮评审可接受） |
| round_N 完成后 | 完整三产物 | 从 round_{N+1} 开始 |
| 达标后 monitor 阶段 | state.json | 重建 monitor task（§15.2 R6.4） |

**state.json 缺失兜底**：恢复时文件缺失/损坏 → 回退 DB `current_round`（再缺
→ 从 0 起）；仅警告不阻断。

决策：D3=半轮残留忽略保留；D4=恢复后首轮不强制评审/收集（恢复即正常新
round，评审循环自然走）。

**单一事实来源（A4）**：state.json 为任务态**权威**（last_completed_round /
best_metrics / last_keep_run_dir / continuous_deviation），DB 仅镜像
（execution_status / heartbeat 供列表与恢复扫描）；写序：先 state.json 后 DB。

---

## 4. 单身份设计（study_id = 会话身份）

### 4.1 动机

v1 中 study 绑定用户 chat session（session_id），导致：事件/权限与 chat 纠缠、
同 session 多 study 需互斥、SSE 无法按任务隔离。v2 采用**单身份设计**：
`studies.session_id` 直接 = `study_id`（`_id("study")` 生成，形如
`study_7f2d9a`）——**不引入微 session 概念、不建 sessions 行、无前缀拼接**。

**关键事实（M0 证实）**：SSE/EventBus 按 session_id 字符串键路由
（`sse_buffer`/`_subscribers`），**不查询 sessions 表**——事件频道不要求
sessions 行存在，因此无需为 study 创建会话行。

### 4.2 设计（单身份）

| 项 | 设计 |
|---|---|
| session id | `studies.session_id` = `study_id`（create_study 内部一次写入，无回填、无前缀、无额外行）——**执行身份 + 事件频道 + goal 隔离域 三合一** |
| owner_session_id | = 创建者 chat 会话——**归属用途**：`get_active_study`/`list_studies` 按它查（v1「按会话查我的研究」语义保留）+ **IDOR 校验**（`_verify_study_ownership` 按 owner 查 sessions 表） |
| 事件隔离 | study 事件 emit 到 `session_id`（=study_id）频道——scheduler/runner 直接使用 `study.session_id`，零改动 |
| SSE | 前端详情页 `useSSE(study_id)`（下期前端）；连接自带 event_log 重放（last_event_id）；不依赖 sessions 行 |
| 与 chat 互斥 | 天然解耦：`_processing_sessions` 按 session key；chat 会话与 study_id 键不同，无互斥 |
| 账本写入 | goal 写路径 **session 解耦**（§8.5 E0）：`_require_mutable_goal` 不校验 session——runner 传 study_id 即可通过；evidence 落库强制 goal.session_id、journal/evidence 查询按 goal_id |
| 与 chat 隔离 | study 的 goal 挂 study_id 域 → **chat agent 上下文（`get_current_goal_context` 按 chat 会话查）不感知 study 的 goal**——期望隔离 |
| 记录 | study 不写 chat messages（directive 仍走 `study_directives` 表） |

### 4.3 兼容性

- 旧 study（v1，session_id=chat 会话）：owner_session_id 回填=chat 会话 →
  `get_active_study`/`list_studies` 按 owner 查询后 v1 语义自动保留；事件
  仍发 chat 会话（旧行为）；v1 记录只读展示，不保证 v2 新功能
- 前端无需过滤会话列表（无微会话行，StudyTab 按 owner 查询）

---

## 5. 并行调度

### 5.1 现状（v1）

`StudyScheduler` per-session 队列 + 单消费者串行；`_run_one_study` 内
`mark_session_processing` 与 chat 互斥。

### 5.2 v2 设计（`core/study/scheduler.py`）

| 项 | 设计 |
|---|---|
| 并行模型 | `_session_loop` 消费队列 → `asyncio.create_task(self._run_one_study(id))` **不等待** |
| 全局限流 | `asyncio.Semaphore`，大小 `SR_STUDY_MAX_CONCURRENT`（默认 3，env 可配） |
| 资源锁 | **取消 per-strategy 锁**（study 自治后无共享文件） |
| 原子创建 | run 目录 `mkdir(exist_ok=False)` 循环重试（防并行序号竞争） |
| 任务管理 | `_active_tasks` 登记 executor + monitor task；cancel/shutdown 时统一清理 |
| 消费者生命周期 | study 终态后队列清理；shutdown 标志退出循环 |

**保留机制声明（v1 → v2，代码已在）**：
- **轮间 cooldown 保留**（`runner.py:295-298`，`get_cooldown_seconds(base*2, jitter*2, min*2)`）：并行化后仍承担限流职责；cooldown 在 semaphore 槽位内等待（占用并发配额）
- **heartbeat 保留**（studies 表 + `update_round_heartbeat`）：纳入 M1 `_active_tasks` 健康管理，防僵尸 task（长任务崩溃后心跳停更可被 recover 识别）

### 5.3 与 chat 的协作

- study 不占用 chat session 处理槽（单身份天然解耦）
- **互斥机制保留**：`_run_one_study_locked` 的 `is_session_processing` +
  `mark_session_processing` 逻辑不动——单身份后互斥键 = `study_id`：同一
  study 防重入（重复 submit / recover 并发时第二个等待）；与 chat 会话
  不同键，互不阻塞（隔离与互斥同时成立）
- `mark_session_processing(study_id)` 保留调用（兼容代码路径），实际不
  影响任何 chat 会话

---

## 6. study 自治目录

### 6.1 目录结构（权威定义）

```
<workspace>/study/<study_id>/
├─ guidance.md            # 全局模板初始化的任务设定 + 人类判断点（每轮注入）
├─ state.json             # 任务级状态：status/round/baseline_best/last_keep_run_dir/
│                         #   best_metrics(仅 keep)/runs 列表/budget 用量/连续偏离计数
├─ journal.md             # 追加式轮次归档（一页览全貌，含否决标记+原因）
├─ todos.md               # 任务级子任务清单（评审维护）
├─ knowledge.md           # 外部信息储备 + 新 idea（collector 沉淀，追加式）
├─ results.tsv            # 本 study 指标历史（★含 round 列）
├─ baseline/
│    └─ strategy.py       # 初始策略（创建时生成；round_1 起点；全 discard 兜底）
└─ rounds/
    └─ round_NNNN/        # 研究轮次（NNNN 从 0001 起，study 内递增）
        ├─ manifest.json  # 机器交接（下一轮注入源）
        ├─ summary.md     # 单轮总结（人类可读）
        └─ run_XXXX/      # 回测运行（每轮内 XXXX 从 0001 起）
            ├─ strategy.py          # 当 run 执行的策略（快照=当轮活跃文件）
            ├─ agents/<name>.json   # 9-agent 记录
            ├─ metrics.json / run.log / run_card.md / factor_failures.json
            └─ …
```

### 6.2 创建引导（study start 时）

1. `mkdir -p study/<id>/{baseline,rounds}`
2. 初始策略来源：`strategies/<strategy_name>/strategy.py` 存在 → 复制为
   `baseline/strategy.py`（用户已有策略作为起点）；否则生成默认最小模板
   （复用 `_create_minimal_strategy` 逻辑）。baseline 是第一轮与全 discard
   兜底的起点
3. `results.tsv` 初始化表头（含 round 列）
4. guidance.md 初始化：全局 `study/guidance.md` 模板存在则复制（`{study_id}`
   占位符替换），`StudyStartRequest.guidance_md` 优先
5. todos.md 初始化：创建引导时生成模板文件（头部 + 空待办区）；
   round_1 评审时由 reviewer 填充初始拆解（§12.2）
6. knowledge.md 初始化：创建引导时生成空文件（头部模板）——与 todos.md
   一致，创建起即可 GET，首次收集时追加内容
7. state.json 初始化：`baseline_best`（= baseline 起步时的 0/空）、
   `last_keep_run_dir: null`、`continuous_deviation: 0`、
   `budget_used_turns: 0`、`budget_used_time_s: 0`（runner 内存态恢复依赖）

### 6.3 与 strategies/ 的关系

- `strategies/<name>/` 保留给 CLI 单次研究（默认参数路径），study 不再读写
- `strategy_name` 降级为标签（命名用途），不决定产物路径

---

## 7. 引擎路径参数化

### 7.1 目标

研究轮子（autoresearch/backtest）从「硬编码 `strategies/<name>/`」变为
「显式目录参数」；**默认参数 = 原拼接**（CLI/既有测试零影响）；study 传自己
的目录。

### 7.2 硬编码点清单（改造范围）

| 文件:行 | 函数 | 改造 |
|---|---|---|
| `core/autoresearch.py:142` | `read_current_state` | 参数拆为 `strategy_file` + `results_tsv` **双来源**（默认 = `ws/strategies/<name>/strategy.py` 与 `…/runs/results.tsv`，保持旧行为） |
| `core/autoresearch.py:1458` | `run_research_round`（单轮） | 同上 |
| `core/autoresearch.py:1715` | `_create_run_dir` | 加 `runs_dir` 参数（CLI: `strategies/<name>/runs`；study: `rounds/round_NNNN`） |
| `core/autoresearch.py:1788` | `run_researcher_phase` 等三阶段 | 同上 |
| `core/backtest.py:172-207` | `update_results_tsv` | 表头加 `round` 列 + `(round, run)` 复合匹配（R2.4） |
| `core/backtest.py:275/388/400/539/597` | 回测链（`run_backtest_script`/`run_backtest_from_yaml`/…） | 加 `strategy_dir` 参数 |
| `core/study/runner.py:545` | `_maybe_load_previous_summary` | 改为读本 study 上轮 manifest（回退旧逻辑删除） |
| `core/agent/sandbox.py:173` | `PathWhitelist.write_roots` | 已支持参数注入——study 场景注入 `("study",)` 根（或按 session 精确路径） |

**工具层硬编码全清单（M0 新发现，决策=全部参数化）**：strategist 白名单含
`run_backtest`（role_factory.py:41），工具内部硬编码 `strategies/<name>/`。
**统一参数化方案**（非移除工具）：`ToolContext` 增加三个可选字段
`strategy_dir` / `runs_dir` / `results_tsv`（回退逻辑=默认旧拼接，CLI 零影响）：

| # | 位置 | 工具 | 硬编码 | 参数化 |
|---|---|---|---|---|
| T1 | `builtin_tools/__init__.py:589` | ConfigLoadStep | `ws/strategies/<name>/config.yaml` | `ctx.strategy_dir` 回退旧 |
| T2 | `builtin_tools/__init__.py:546-548` | RunBacktestTool artifacts | `runs/<name>/<run>/…` | `ctx.runs_dir` 回退旧 |
| T3 | `builtin_tools/__init__.py:713` | EngineRunStep → `run_backtest_from_yaml` | strategy_name 派生 | 透传 strategy_dir/results_tsv/runs_dir |
| T4 | `builtin_tools/__init__.py:1118` | list_history | `strategies/<name>/runs/results.tsv` | `ctx.results_tsv` 回退旧 |
| T5 | `builtin_tools/__init__.py:2530` | drawdown_analysis | `strategies/<name>/runs` | `ctx.runs_dir` 回退旧 |
| T6 | `builtin_tools/__init__.py:2699` | benchmark_comparison | `strategies/<name>/runs` | `ctx.runs_dir` 回退旧 |
| T7 | `data_tools.py:701` | check_data | `strategies/<name>/config.yaml` | `ctx.strategy_dir` 回退旧 |
| T8 | `display_tools.py:324` | show_report | `ws/runs/<name>/<run>` | `ctx.runs_dir/<run>` 回退旧 |
| T9 | `display_tools.py` | show_chart | 读 run 产物（M2 确认） | 同上 |

**注入链**：`run_agent_via_llm`/`build_agent_loop` 加 `strategy_dir`/`runs_dir`/
`results_tsv` 参数 → `AgentLoop` 存储 → loop.py:1132 构造 `ToolContext` 透传 →
工具读 `ctx.*`（None 回退旧路径 = CLI 行为不变）。backtest.py 主链
（run_backtest_script/run_backtest_from_yaml）同步加同三参数。

### 7.3 关键实现细节

**read_current_state 双目录拆解**（R2.1）：
- strategy.py 与 results.tsv 在 study 场景分属不同位置（strategy.py 在本轮
  `run_XXXX/`，results.tsv 在 `study/<id>/` 根）
- 参数拆为 `strategy_file` + `results_tsv` 两个来源（CLI 传同一目录保持旧行为）

**TSV round 列与复合匹配**（R2.4）：
- `results.tsv` 表头追加 `round` 列（**尾部追加 index 13**——保护既有
  parts[3]=calmar / parts[11]=status 索引，CLI 旧行留空兼容）
- 行匹配键改为 `(round, run)` 复合（backtest.py `update_results_tsv` append
  模式 + runner.py `_update_results_tsv` 原位改 verdict 模式**双点改造**）
- `read_current_state` 的列索引解析同步更新（CLI 路径 round 列留空兼容）
- **每 run 一行**：轮内重试/恢复重跑产生的新 run 各占一行（复合键天然支持，
  M2 实现时与 CLI 行为核对）

**strategist 写文件**：
- 白名单：`PathWhitelist(write_roots=("strategies","templates","memory","logs","study"))`
- study 场景在 prompt 注入「当前策略路径」= `study/<id>/rounds/round_N/run_XXXX/strategy.py`
  （正常轮 run_0001；**恢复重跑轮取轮内 max+1**，见 §3.2）

---

## 8. round/run 模型与继承链

### 8.1 执行流

```
round_N 开始：
  0. ★轮首知识缺口检查（§11.1）：研究焦点 vs knowledge.md 检索
     → 有缺口则 collector 收集（在注入栈之前，知识注入依赖其结果）
  1. 确定起点：adopted_run = 最近 keep run（verdict=keep 的最后 run）
     无 keep → baseline/strategy.py（全 discard 兜底）
  2. 跨 round 复制：adopted_run/strategy.py → round_N/run_XXXX/strategy.py
     （run 编号 = 轮内 max+1，正常轮 run_0001，恢复重跑轮 run_0002…；
      manifest 记录 inherited_from）
  3. strategist 基于它修改（write_file 覆盖同文件）
  4. 回测执行 run_XXXX/strategy.py（同步骤 2 的编号）→ 结果写
     study/<id>/results.tsv（round 列）
  5. 评估 → verdict（keep/discard）+ reason
  6. 落盘三产物（manifest/summary.md/journal.md）
  7. ★轮间评审 → ★信息收集（按需）→ ★todos 更新
```

### 8.2 继承规则（定稿）

| 场景 | 下一轮起点 |
|---|---|
| 本轮 keep | 本轮 run（adopted_run 更新） |
| 本轮 discard | **回滚**：不继承本轮改动；adopted_run 保持最近 keep（无则 baseline） |
| 全部 discard | baseline/strategy.py |

**stagnation × 全 discard 联动**：连续 N 轮 verdict=discard（默认 N=5，
`SR_STUDY_MAX_DISCARD` 可配）→ 视为 stagnation 停止
（reason=stagnation_discard_streak）——避免「全部 discard → baseline 兜底」
被无限重试；与决策层 `stagnation_triggered`（`runner.py:262-266`）并行判断，
任一触发即停。

### 8.3 否决信息（定稿）

- **记录**：manifest 的 `verdict_reason`/`rejected_reason`（来自 decide/attribution/
  anti_overfit 结论）；journal.md discard 行带 `❌ 否决：<原因>` 标记；
  summary.md 含「本轮被否决 + 原因」段落
- **注入**：下一轮 researcher prompt「上轮否决原因」段（与 journal_context 并列）
  ——LLM 知道为何否决、有增量信息、避免重复犯错

### 8.4 指标口径（配套修正）

- `best_metrics` / prompt 注入的 best 只统计 **keep run**（discard 轮结果
  即使指标高也不算——防把被否决结果当 baseline）
- `state.json` 维护 `best_metrics` 与 `last_keep_run_dir`

### 8.5 goal 账本闭环（v1 §4.3 保留 + keep 口径修正）

v1 的 goal 账本闭环保留（代码已在 `runner.py`）：`_register_hypothesis`（L440）、
`_check_regression`（L443）、`_archive_rejected`（L448，否决归档到账本）、
`_build_journal_context`（L455，账本 journal 上下文注入）、`_complete_goal`
（L486-515，达标 → 未覆盖 required criteria 逐条 append_evidence →
`complete_lite` 关账）。v2 引入「指标达标但 verdict=discard」场景（gate 硬校验
/过拟合否决）后需明确：

| 项 | 设计 | 决策 |
|---|---|---|
| E0 统一 goal 流程 | **一条 replace_goal 流程**，调用方传参数得不同行为：`session_id`=隔离域（chat 会话 / study_id）+ `supersede: bool=True`（同域旧 active 是否作废）。chat 传 `supersede=True`（1:1，v1 不变）；study 传 `supersede=False`（study_id 域 1 study 1 goal，多 study 并行互不干扰）。`_require_mutable_goal` 已解耦（保留 expected_goal_id / status 状态机校验） | 决策 D + Q2：唯一索引 `idx_goals_one_current_per_session` 保留（域内 1 active 硬保证；study_id 域天然 1:1） |
| E1 只落 keep 账 | **evidence + goal 完成**严格 keep 口径（discard 轮即使指标达标也不落账）；**hypothesis 每轮注册**（researcher 阶段、verdict 前——否决轮作为负面证据保留）；archive_rejected 否决轮归档 | **D2=是（keep 口径）** |
| E2 达标判定口径 | 达标 = meets_metric_targets ∧ verdict=keep ∧ 硬校验通过，三者同时才触发 goal 完成 | **D1=否（硬校验否决时不 complete，继续迭代）** |
| E3 journal 并存 | 账本 journal（goal 视角）与文件级 journal.md（任务视角）**并存不合并**，prompt 注入两段并列；journal 行 session_id=study_id（元数据），查询按 goal_id | — |

**已知边界（已评估）**：goal 写路径不再校验 session——防护 = goal_id 不可枚举
+ `expected_goal_id` 匹配 + API 层 IDOR（`_fetch_session_owned` 按 owner 校验）；
chat `/goal` 建新 goal 会 supersede 同 chat 域的旧 goal（v1 行为保持）；
chat agent 上下文不感知 study 的 goal（`get_current_goal_context` 按 chat
会话查，study 的 goal 隔离在 study_id 域——期望隔离）。

---

## 9. 每轮产物规范

### 9.1 manifest.json（机器交接，下一轮注入源）

```json
{
  "round": 7,
  "inherited_from": "round_6/run_0001",
  "adopted_run": "round_6/run_0001",
  "run_name": "run_0001",
  "status": "rejected",
  "hypothesis": {"text": "…", "levers": ["vol_adj_mom"], "predicted_affected": ["calmar"], "novelty": "pass"},
  "strategy_changes": [{"param": "FACTOR_EXPRS", "old": [], "new": ["vol_adj_mom"]}],
  "metrics": {"calmar": 1.2, "sharpe": 0.8, "max_dd": -0.15, "vs_prev": {"calmar": "+0.3"}, "vs_baseline": {"calmar": "+0.5"}},
  "verdict": {"decision": "discard", "reason": "过拟合：样本外衰减 40%"},
  "rejected_reason": "…",
  "agent_summaries": {"researcher": "…", "strategist": "…", "risk_controller": "…"},
  "failures": [{"factor": "…", "error": "…", "suggested_fix": "…"}],
  "attribution": {"lever": "momentum", "metric": "calmar", "outcome": "regressed"},
  "gates": [{"id": "risk-max-dd", "result": "pass", "enforced": true}],
  "lessons": "给下一轮的经验提示",
  "next": {"suggested_focus": "…", "open_questions": [], "blockers": []},
  "review": {"deviation": "low", "info_gap": false, "todo_updates": [], "next_focus": "…"},
  "budget": {"turns_used": 42, "time_used_s": 3600, "total": {"turns": 100, "time_s": 7200}}
}
```

### 9.2 summary.md（单轮总结，`render_round_markdown` 纯模板渲染）

```
# Round 7 总结 · 2026-08-12 14:03
## 本轮做了什么     （hypothesis + 各环节摘要）
## 本轮修改         （strategy_changes 表：项/旧值/新值）
## 结果             （指标 vs 上轮 vs baseline）
## 裁决             （verdict + 原因；discard 含否决说明）
## 人类判断点       （guidance gates 命中情况）
## 下一轮建议       （next + review.next_focus）
```

### 9.3 journal.md（追加式归档，`append_journal_md`）

- 任务根目录，每轮 append 一行式摘要：
  `## Round 7 [keep ✓] 假设… 改动… Calmar 0.9→1.2 · [summary.md 链接]`
- discard 行：`## Round 7 [discard ❌] 否决：<原因> …`
- 首次写入初始化头部（任务信息/创建时间/guidance 说明段引用）

### 9.4 落盘时序（manifest 两段式）

runner 轮末：
- **第一段（轮末）**：agents 已写 → verdict 确定 → `append_round`（DB）+
  manifest **主体**（round/继承/假设/改动/metrics/verdict/gates/next）→
  summary.md → journal.md 追加 → state.json 更新（先 state.json 后 DB；
  文件先写，DB 失败仅警告不阻塞）
- **第二段（评审后，§10.2）**：review 结论（deviation/info_gap/next_focus）
  → 覆盖更新 manifest `review` 段 + state.json（continuous_deviation 等）
  → DB `update_round` 镜像（方法与 append_round 成对，见 §20.4）

---

## 10. 轮间评审循环

### 10.1 动机

长程任务方向漂移与信息孤岛：多轮后可能偏离目标（跑偏）或知识过期（孤岛）。
每轮间隙引入**综合评审**（LoopX 的 gate/monitor 思想），判断偏离度、管理
todos、触发外部信息收集。

### 10.2 流程

```
round_N 三产物落盘后：
  ↓
① 评审（study_reviewer agent，轻量单轮）
    输入：objective + metric_targets + 本轮 manifest/verdict + todos.md
          + knowledge.md（近期）+ 指标历史（keep 口径）
          + **上轮 review 结论 + continuous_deviation 历史**（修正回路
            连续性：上轮 high 是否好转可判断）
    输出 JSON：
      deviation: "low|medium|high"
      deviation_reason
      info_gap: bool          # 是否触发信息收集
      topics: []              # 收集主题（info_gap 时）
      todo_updates: [{action: "add|update|done|abandon", id, title, note}]
      next_focus
  ↓
② 信息收集（评审 info_gap **补充触发** + K 轮兜底——主触发在轮首 §11.1）
    collector agent（web_search/read_url 白名单）按 topics 搜索
    → 增量追加沉淀 knowledge.md（来源标注 + 摘要 + 新 idea）
  ↓
③ todos.md 应用：todo_updates → 后端模板渲染写回（结构稳定）
  ↓
④ 状态更新：
    deviation=high → continuous_deviation += 1；注入「修正指令」
                     （≥3 → 自动停止 reason=repeated_deviation，可恢复）
    else → continuous_deviation = 0
  ↓
⑤ 下一轮注入：上轮否决原因 + review 结论（deviation/next_focus/todo 变更）
   + todos.md 全文 + knowledge.md 近期条目
```

- 评审/收集/todos 应用均在 `_run_one_study` 循环内**同步**执行 → 占用
  semaphore 并发配额（防任务数翻倍）
- 评审输出即第 9.4 节的「第二段」manifest 更新来源

### 10.3 评审失败降级

reviewer 输出非法/超时 → 跳过评审继续（日志记录）；连续 2 次失败 → 停止
（防无人值守死循环），reason=review_failed。

### 10.4 事件

`study_review {study_id, round, deviation, info_gap}`、
`study_knowledge_update {study_id, entries_added, dropped}`、
`study_todos_updated {study_id, updates}`

---

## 11. 外部信息收集

### 11.1 触发机制（轮首缺口检查为主）

**每轮开始**（研究链之前，§8.1 步骤 0）执行一次收集动作：

```
round_N 轮首：
  ① 缺口检查（零 LLM，规则化）：
     输入：objective + 上轮 manifest.next_focus（研究焦点）
     动作：与 knowledge.md 活跃条目做相似度检索（复用 hypothesis 相似度逻辑）
     输出：gap_topics（现有知识未覆盖的主题）+ study_knowledge_check 事件
  ② 有 gap_topics → collector agent（一轮 LLM）按缺口收集 → 增量追加
     无 gap_topics → 跳过（零成本）
  ③ 兜底触发（主触发遗漏时）：
     · 轮末评审 info_gap=true（§10.2 ②，补充轮首盲区——轮中新主题）
     · 距上次实际收集 ≥ K 轮（K=`SR_STUDY_COLLECT_INTERVAL` 默认 5，强制防漏）
```

| 项 | 设计 |
|---|---|
| 执行者 | collector agent（复用 researcher 工具集：web_search/read_url/list_history），轻量单轮 |
| 输入 | objective + gap_topics + knowledge.md 现有条目（防重复） |
| 输出 | 结构化条目列表：`{topic, source_url, summary, idea, relevance, collected_at}` |
| 沉淀 | **增量追加** `knowledge.md`（条目式 + 时间戳 + 来源） |
| 注入 | 每轮注入**近期 N 条**（默认 5，防上下文膨胀）；全文文件可 read_file 按需读 |
| 失败降级 | collector 异常/超时 → 跳过本轮收集 + **不算 K 轮计数**（下轮仍触发）；连续 2 次失败 → 同评审降级（§10.3）停止 |
| 主题兜底 | 强制收集时 gap_topics 为空 → 用 objective 作为主题 |

### 11.2 防重复闭环（三层防线）

```
收集前（轮首缺口检查）→ 检索已有知识，只收缺口（防重复收集）
     ↓ 收集后（写入时）
相似度过滤：与现有条目 sim > 阈值 → 丢弃并计数 dropped（复用 hypothesis
    相似度逻辑——收集-写入-压缩全链路防重复）
     ↓ 长期运行（膨胀时）
压缩时（§11.3）：规则合并 + agent 语义合并重写（二次去重机会）
```

### 11.3 压缩机制（方案 B 先行，后续演进方案 C）

**触发条件**（规则化，任一即触发，可配）：

```
条目数 > SR_STUDY_KNOWLEDGE_MAX_ENTRIES（默认 100）
或 文件体积 > SR_STUDY_KNOWLEDGE_MAX_SIZE（默认 64KB）
```

**执行流程**：

```
① 规则预筛（零 LLM）：相似度合并候选 + 明确低价值候选（relevance=low 且
   >30 天）→ 先做纯规则去重，降输入量
② compress agent（轻量一轮，同 reviewer 协议）：
   输入：objective + 去重后条目（分批，防超上下文，MapReduce 式）+ 注入窗口要求
   输出：合并后的紧凑条目集（重写摘要 / 合并相似主题 / 保留 idea 高价值项）
③ 原子替换：knowledge.md.tmp → rename（防崩溃半写）
④ 淘汰条目归档：knowledge-archive.md 追加（append-only，审计可追溯）
⑤ 事件：study_knowledge_compacted {removed, merged, kept, size_before, size_after}
```

- **执行时机**：轮首缺口检查之前（压缩后检索基线更新，避免压缩打断研究链）
- **失败降级**：agent 输出非法 → 仅应用规则预筛结果（部分压缩）+ 下轮重试；
  连续 2 次失败 → 暂停压缩（warn），不阻断研究
- **升级路径（方案 C，v2 不实现）**：分层存储（MemGPT 式）——knowledge.md
  只保留活跃注入区，旧条目进 `knowledge-archive/` 按主题分文件 + 检索层；
  触发信号：压缩过于频繁（如每 5 轮一次）时启用（§20.5 扩展点）

### 11.4 knowledge.md 格式

```markdown
# 知识储备与 Idea 池
<!-- 外部信息收集沉淀 · 追加式 · 每轮注入近期条目 -->

## 2026-08-12 · momentum 因子新研究（relevance: high）
- 来源：https://… 
- 摘要：…
- idea：将 vol_adj_mom 与量价背离信号结合
```

---

## 12. todos 机制

### 12.1 文件与语义

`todos.md`：任务级子任务清单（跨轮持久，区别于 manifest.next 的轮级建议）。

```markdown
# 任务子任务清单（评审维护）

## 待办
- [ ] todo-001 建立数据质量基线（创建于 round_1）
- [ ] todo-003 验证量价背离因子（创建于 round_4，评审 round_5 升级为高优）

## 进行中
- [x] todo-002 回测框架验证（创建于 round_1，round_2 完成）

## 已放弃
- [ ] todo-004 深度强化学习方向（round_3 放弃：数据不足）
```

### 12.2 生命周期

| 阶段 | 动作 |
|---|---|
| 文件初始化 | 创建引导时生成模板（头部 + 空待办区，§6.2 步骤 5）——文件自创建起存在，前端可随时读取 |
| 初始拆解 | round_1 评审时，reviewer 按 objective 生成初始拆解（3-8 条）写入待办区 |
| 每轮评审 | reviewer 输出结构化 `todo_updates`（add/update/done/abandon + note） |
| 应用 | 后端模板渲染写回 todos.md（格式稳定可校验；非法指令丢弃并警告） |
| 注入 | 每轮 researcher/strategist prompt 携带 todos.md 全文（任务拆解上下文） |

---

## 13. guidance 人类判断点

### 13.1 文件结构（双层合一）

```markdown
---
gates:
  - {id: risk-max-dd, metric: max_dd, op: ">=", value: -0.15, enforce: true, action: reject}
  - {id: turnover-limit, metric: turnover, op: "<=", value: 3.0, enforce: false, action: warn}
---
# 研究指引（每轮自动注入）

## 决策规则（人类判断点）
1. 任意策略 MaxDD 不得超过 -0.15，超限本轮改动直接拒绝。

## 偏好
- 优先验证动量/波动率类因子；因子表达式保持可解释性。

## 任务文档说明（需要时用 read_file 按需读取）
- `study/<study_id>/journal.md`：全任务轮次归档（追加式，一行式摘要）。想快速了解任务整体进展、做长程规划时读取。
- `study/<study_id>/rounds/round_NNNN/summary.md`：单轮详细总结（本轮假设/修改/结果/裁决）。需要回顾具体某轮细节时读取。
- `study/<study_id>/todos.md`：任务子任务清单。查看待办与进度时读取。
- `study/<study_id>/knowledge.md`：外部信息储备。需要外部情报时读取；文件超阈值会自动压缩（旧条目归档至 knowledge-archive.md）。
- 每轮产物默认已通过上下文注入，无需重复读取；以上文件仅在信息不足时按需读取。
```

- **frontmatter**（YAML）：gates 硬校验字段（metric/op/value/enforce/action）
- **正文**（markdown）：注入 LLM 的规则/偏好/文档说明
- 双层加载：`load_guidance(workspace, study_id)` → per-task
  `study/<id>/guidance.md` 优先，缺失回退全局 `study/guidance.md`
- **合一原则**：任务文件始终是单一 guidance.md（frontmatter gates + 正文）；
  CLI `--guidance-file` / `--gates-file`（§17.1）是**输入源**：gates-file 提供
  frontmatter、guidance-file 提供正文，各自缺失回退全局模板，合并生成任务
  文件——不改变文件层合一结构

### 13.2 注入

- 位置：runner `_run_one_round` 开头读取 → 渲染为「## 人类判断点」段 →
  `current_state["human_guidance"]` → `build_agent_prompt` 渲染（`applies_to`
  过滤：frontmatter 可配 agent 名单，默认全部）
- 每轮自动注入（持久指引，无需人工）

**注入优先级（v1 Phase 2 directives 保留）**：人工即时指令（directive，
`study_directives` 表，最高）> guidance.md 持久规则 > manifest 交接信息
（inherited_from/next/否决原因）> 账本 journal。directives 仍走既有注入链
（runner 已实现），guidance 与之并列注入（不替换）。

### 13.3 硬校验

- 时机：verdict 前（evaluation 后）
- 逻辑：`enforce:true` 的 gate，`check_violations(gates, last_metrics)` 命中 →
  强制 verdict=discard + manifest `gates[]` 记录 `enforced:true, result:"violated"`
- metric 命名与 results.tsv 列名对齐（max_dd/sharpe/calmar/…）
- **metric 缺失处理**：gate 引用的 metric 不在本轮结果中 → skip + warn（不
  误杀；如 turnover 未计算时 turnover-limit gate 不生效）

### 13.4 端点

`GET /study/{id}/guidance`；创建时 `StudyStartRequest.guidance_md` 可选覆盖
（存储到 `study/<id>/guidance.md`）

---

## 14. hypotheses 存储迁移（SQLite）

### 14.1 背景

`hypotheses.json`（`~/.quantnodes-research/`）全局 JSON 数组，read-modify-write
并发丢更新（两个并行 study 的 researcher 阶段可能互相覆盖记录）。
`HypothesisStore`（`core/hypothesis/store.py`）已有完整 SQLite 实现（WAL +
`BEGIN IMMEDIATE` 写事务 + FTS5），但 **registry 层未接线**（`_store` 只在
`__init__` 创建，14 个方法无 `_store` 分支）。

### 14.2 改造（M5）

1. **接线**：`core/hypothesis/registry.py` 的 14 个方法
   （create/update/list/get/search/derive/link/unlink/contradicts/link_goal/
   link_backtest/list_by_goal/list_children/list_contradictions）增加
   `if self._store is not None: return self._store.xxx(...)` 分支
2. **默认启用**：`api/app.py::create_app` 顶部
   `os.environ.setdefault("HYPOTHESIS_USE_SQLITE", "1")`
3. **不迁移**：旧 JSON 不导入（文件保留但不读），SQLite 从空开始
4. **并发保证**：进程内 RLock + `BEGIN IMMEDIATE`（跨协程/进程安全）
5. 测试：接线覆盖、并发 create 无丢失、与 JSON 模式行为一致

---

## 15. Monitor 永续机制

### 15.1 现状

monitor 逻辑只在旧 `executor.py`（L407-532）；生产 runner（scheduler 实际使用）
未实现——v2 移植到 `AutoresearchRunner`。

### 15.2 设计（M7）

```
达标（**E2 语义**：meets_metric_targets ∧ verdict=keep ∧ 硬校验通过）→ COMPLETE
  → 若 monitor_interval_seconds > 0：
  状态 → MONITORING（emit study_monitoring_started）
  monitor loop（后台 task）：
    每 interval 秒：run_backtest_script(action="monitor")
      → 执行「最后 keep run」的 strategy.py（state.json.last_keep_run_dir）
      → 比 metric_targets（**monitor 判定仅指标目标，不含 verdict/gates**——
         gates 是研发期约束）：
        达标 → emit study_monitor_check{meets_targets:true} → 继续等待
        漂移 → emit study_drift_detected → 状态 NEEDS_REFRESH
          → ★自动修复：完整 _run_one_round（round 递增，计入 budget）
             达标（同 E2 口径）→ 回 MONITORING；连续 3 轮无效 → 停留
             needs_refresh 等用户
  支持 pause/resume/cancel（ControlToken 复用）
```

- monitor 检查的 round 归属：修复轮 = 正常 round（rounds/ 目录 + 三产物 + 评审）
- `study_monitor_check_failed`（回测异常 → 继续下一轮检查）
- **recover**：MONITORING 状态重启 → 重建 monitor task（原 recover 未覆盖，
  补入 `recover_on_startup`，scheduler 惰性幂等接线）

### 15.3 预算

- monitor 检查（仅回测）不计 turns
- 修复轮计 turns/time（`_account_round_budget` 沿用）
- **修复轮预算耗尽** → 停留 needs_refresh 等用户（reason=budget_limited 记录）
- **修复轮不走 discard-streak 联动**（§8.2 N=5 停止仅用于研发轮——修复轮
  已有 ≤3 轮上限）

---

## 16. 事件清单

### 16.1 完整事件总表（v2 定稿，5 组 24 个）

> 现状实测：runner（生产路径）已 emit started/paused/resumed/round/
> round_rejected/completed/early_stopped/failed/executor_stopped；缺
> budget_limited/cancelled/monitor 全家（旧 executor.py 有）。

**① 生命周期组**

| 事件 | payload | 时点 | 状态 |
|---|---|---|---|
| `study_queued` | {study_id, session_id, objective} | 入队 | 补（v1 设计未实现） |
| `study_started` | {study_id, round} | executor 启动 | 已有 |
| `study_paused` / `study_resumed` | {study_id, round} | 暂停/恢复 | 已有 |
| `study_cancelled` | {study_id} | 取消 | 补 runner（executor 已有） |
| `study_early_stopped` | {study_id, round, reason} | 提前停止（stagnation/偏离） | 已有 |
| `study_completed` | {study_id, goal_id, metrics, round, recap} | 达标完成（E2 全条件） | 已有 |
| `study_failed` | {study_id, error, reason} | error | 已有 |
| `study_executor_stopped` | {study_id, reason} | 任意终态收尾 | 已有 |

**② 轮次组**

| 事件 | payload | 时点 | 状态 |
|---|---|---|---|
| `study_round` | {study_id, round, run, metrics, verdict, agent_statuses} | 每轮结束（**前端轮卡片**） | 已有 |
| `study_round_rejected` | {study_id, round, reason} | 否决轮 | 已有 |
| `study_phase` | {study_id, round, phase, status} | 轮内实时进度（**前端时间线**） | 新增 |
| `study_review` | {study_id, round, deviation, info_gap} | 评审完成 | 新增 |
| `study_todos_updated` | {study_id, updates} | todos 应用 | 新增 |

决策 **D5=并存**：`study_round` 是轮次结果（轮卡片），`study_phase` 是轮内
实时进度（时间线），二者语义不同不合并。

**③ 账本组**（决策 D6=补）

| 事件 | payload | 时点 | 状态 |
|---|---|---|---|
| `study_evidence` | {study_id, evidence_id, criterion_id, run} | 每轮落 evidence（仅 keep 轮，E1） | 补（v1 设计未实现） |
| `study_progress` | {study_id, covered, total, percent} | criteria 覆盖变化 | 补（v1 设计未实现） |
| `study_budget_limited` | {study_id, used} | 预算耗尽 | 补 runner |

**④ 监控组**（旧 executor 已有 → 全部移植 runner）

| 事件 | payload | 时点 | 状态 |
|---|---|---|---|
| `study_monitoring_started` | {study_id, interval} | 进入监控 | 移植 |
| `study_monitor_check` | {study_id, meets_targets, metrics} | 周期检查 | 移植 |
| `study_monitor_check_failed` | {study_id, error} | 检查异常 | 移植 |
| `study_drift_detected` | {study_id, metrics} | 漂移 | 移植 |

**⑤ 知识组**

| 事件 | payload | 时点 | 状态 |
|---|---|---|---|
| `study_knowledge_check` | {study_id, round, gap_topics, collected} | 轮首缺口检查结果 | 新增 |
| `study_knowledge_update` | {study_id, entries_added, dropped} | 收集沉淀 | 新增 |
| `study_knowledge_compacted` | {study_id, removed, merged, kept, size_before, size_after} | 压缩完成 | 新增 |
| `study_directives_consumed` | {study_id, count} | directives 消费 | 移植（executor 已有） |

### 16.2 注册

`api/session/event_v2.py` EventType registry 注册全部 `study_*`（当前透传未校验）。

---

## 17. API 端点

| 端点 | 方法 | 状态 |
|---|---|---|
| `/api/study/start` | POST | 既有（+ `guidance_md` 字段） |
| `/api/study/list` `/status` | GET | 既有 |
| `/api/study/{id}/pause\|resume\|cancel\|directive\|directives` | POST/GET | 既有 |
| `/api/study/{id}/summary` | GET | 既有（recent_rounds 改读 study_rounds 表） |
| `/api/study/{id}/rounds?offset&limit` | GET | **新增**（study_rounds 表分页） |
| `/api/study/{id}/journal` | GET | **新增**（读 journal.md 文件，单一事实来源） |
| `/api/study/{id}/rounds/{round}/summary_md` | GET | **新增** |
| `/api/study/{id}/guidance` | GET | **新增** |
| `/api/study/{id}/todos` | GET | **新增** |
| `/api/study/{id}/knowledge` | GET | **新增** |

所有权校验沿用 `_verify_study_ownership` / `_fetch_session_owned`。

**M 分配**：rounds/journal/summary_md 端点属 M3；todos/knowledge 属 M4；
guidance 属 M6。

### 17.1 CLI /study 命令（chat.py，v2 参数扩展）

现有（`chat.py:904-913`）：`/study start "<objective>" [--workspace W]
[--strategy S] [--metric calmar>=0.5,sharpe>=0.3] [--budget-turn N]
[--budget-time S] [--max-rounds N]` + `/study status|list|pause|resume|
cancel|redirect|help`。

v2 新增（决策 **D9=路径方式**，chat 传大段 YAML/markdown 不友好）：

| 参数 | 语义 |
|---|---|
| `--guidance-file <workspace 相对路径>` | 任务级 guidance.md 正文从文件加载（服务端读） |
| `--gates-file <workspace 相对路径>` | frontmatter gates 部分从文件加载（YAML），可与 guidance-file 独立使用 |
| 不传两者 | 用全局 `study/guidance.md` 模板（默认行为） |

- API 侧（`POST /study/start`）继续支持文本直传 `guidance_md` 字段，不受命令限制
- **安全约束**：两参数仅接受 workspace 内相对路径（resolve 后必须位于
  workspace 之下），否则拒绝——防越权读任意文件

---

## 18. 风险与对策

| # | 风险 | 等级 | 对策 |
|---|---|---|---|
| R2.4 | TSV 行匹配错行（run 号每轮重复） | 高 | round+run 复合匹配（M2 强制） |
| R2.1 | read_current_state 双目录 | 中 | strategy_file + results_tsv 参数拆分 |
| R6.4 | MONITORING 重启恢复缺失 | 中 | recover 补 MONITORING → 重建 monitor task |
| R6.2 | monitor 执行策略位置 | 中 | state.json.last_keep_run_dir |
| R3.1 | discard 继承语义 | 已定 | 否决即回滚 + 否决原因记录注入 |
| 评审死循环 | 连续 high 偏离 | 中 | 3 次 → 自动停止（可恢复） |
| discard 无限兜底 | 全 discard → baseline 重试不收敛 | 低 | 连续 N 轮 discard → stagnation 停止（§8.2） |
| 评审失败 | 输出非法 | 低 | 跳过继续；连续 2 次 → 停止 |
| knowledge 膨胀 | 注入上下文超限 + 文件失控 | 中 | 注入近期 N 条 + 三层防重复 + 压缩机制（§11.2/11.3） |
| M2 回归 | CLI/单轮行为变化 | 中 | 默认参数旧行为 + 全量 pytest |
| 工具层参数化回归 | T1-T9 改动影响 CLI 工具行为 | 中 | 默认回退旧路径（ctx 字段 None）+ 工具回退测试 |
| hypotheses 并发 | 已转 SQLite | 低 | WAL + BEGIN IMMEDIATE |
| 成本 | 每轮 +评审 +收集 | 低 | 轻量单轮；K 轮强制收集可控 |
| task 泄漏 | 消费者/executor 生命周期 | 低 | _active_tasks 统一管理 + shutdown 清理 |
| --guidance-file 路径越权 | 命令参数读任意文件 | 低 | 仅接受 workspace 内相对路径（resolve 校验，§17.1） |
| goal 跨 session 写 | 写路径不再校验 session | 低 | goal_id 不可枚举 + expected_goal_id + API 层 IDOR（§8.5 E0） |

---

## 19. 任务规划 M0-M8

| # | 任务 | 关键产出 | 验收 |
|---|---|---|---|
| M0 | 预研 | ToolContext workspace 注入链、create_session 幂等性、回测链内部路径逐点确认 | 事实清单 |
| M1 | 微 session + 真并行 | create_study 生成 study:{id} + create_session；scheduler create_task + semaphore + task 管理 | pytest（并行 2 study） |
| M2 | 引擎路径参数化 | 10 处主链硬编码 + 工具层 9 处（T1-T9）+ 引擎函数（run_backtest_script/from_yaml 加 strategy_dir/results_tsv/runs_dir 参数）；ToolContext 三字段注入链；TSV round 列尾部 + 复合匹配双点改造；PathWhitelist roots 注入 | pytest 默认兼容回归 + T1-T9 工具回退测试 |
| M3 | 目录 + 继承链 + 三产物 | 创建引导；round/run 结构；adopted_run（keep/回滚/否决记录注入/best 口径）；round_manifest.py；append_round；端点 | pytest 纯函数/继承链 |
| M4 | 轮间评审循环 + 知识管理 | study_reviewer.md；偏离度/修正回路（3 次兜底）；**轮首缺口检查 + collector + knowledge.md 增量/去重/压缩（方案 B）**；todos.md 机制；事件 | pytest 评审解析/修正/todos 应用/缺口检查/压缩 |
| M5 | hypotheses SQLite | registry 接线 14 方法；create_app 默认启用；不迁移；并发测试 | pytest |
| M6 | guidance | guidance.py；双层加载；注入；硬校验；端点 | pytest |
| M7 | monitor + 事件 | monitor loop 移植（含 MONITORING recover）；事件补全 + study_phase + EventType 注册 | pytest fake-time |
| M8 | 验收 | 全量回归 + 真实 LLM E2E | 全绿 |

**提交拆分**：M1 → M2 → M3 → M4 → M5 → M6 → M7（每步一个 commit）

**测试说明**：M1-M7 的 pytest 均以 `AUTORESEARCH_BEHAVIOR=stub` 运行（零 LLM
成本，沿 v1 §10 策略）；M8 真实 LLM E2E 为单次手动执行。

---

## 20. 附录

### 20.1 术语表

| 术语 | 定义 |
|---|---|
| 单身份 | `studies.session_id` = `study_id`（执行身份 + 事件频道 + goal 隔离域三合一，§4） |
| owner_session_id | 创建者 chat 会话（归属查询 / IDOR 校验） |
| adopted_run | 下一轮继承源（最近 keep run 或 baseline） |
| inherited_from | manifest 记录的实际继承来源 |
| keep 口径 | 指标统计只含 verdict=keep 的 run |
| info_gap | 评审判定需要外部信息的状态 |
| K 轮强制 | 距上次实际收集 ≥ K 轮时强制收集（默认 5） |
| 缺口检查 | 轮首规则化检索：研究焦点 vs knowledge.md 活跃条目，输出未覆盖主题（gap_topics） |
| 压缩（方案 B） | 条目/体积超阈值 → 规则预筛 + compress agent 语义合并重写 |

### 20.2 LoopX 对照（设计来源）

| LoopX 概念 | 本系统对应 |
|---|---|
| objective | studies.objective |
| todos（claim/update） | todos.md + 评审 todo_updates |
| evidence + writeback | manifest + results.tsv + goal_evidence |
| gates（人类判断点） | guidance.md（frontmatter 硬校验 + 正文软规则） |
| quota | budget_turn/time + 评审偏离停止 |
| handoff + next todo | manifest（inherited_from/next/review） |
| scheduler_hint | 轮首缺口检查 + K 轮兜底 + monitor 周期 |

### 20.3 关键代码位置索引

| 模块 | 路径 |
|---|---|
| 调度器 | `core/study/scheduler.py` |
| 执行器 | `core/study/runner.py`（monitor 移植自 `executor.py:407-532`） |
| 状态模型 | `core/study/models.py` |
| 存储 | `core/study/store.py`（study_rounds 表 L200-223 启用） |
| 研究轮子 | `core/autoresearch.py`（路径硬编码 L142/1458/1715/1788） |
| 回测 | `core/backtest.py`（L275/388/400/539/597） |
| 沙箱 | `core/agent/sandbox.py`（PathWhitelist L155-200） |
| 工具 | `core/agent/builtin_tools/__init__.py`（WriteFileTool L313-418） |
| 会话 | `api/routers/web_session.py`（create_session L830） |
| 事件 | `api/session/event_v2.py`（EventType registry） |
| 假设注册 | `core/hypothesis/registry.py`（接线点）+ `store.py` |

### 20.4 StudyStore API 清单（v2 参考，store.py）

**现状已就绪（v1 全量落地，v2 直接复用）**：

| 方法 | 位置 | 用途 | v2 使用 |
|---|---|---|---|
| `create_study` | L228 | 创建 study 记录（v2 同时生成 `study:{id}` 微 session） | M1 接入点 |
| `get_study` / `get_active_study` | L445/452 | 按 id 读 / 按 session 查活跃 | 兼容保留 |
| `list_studies` / `list_active_studies` | L466/489 | 列表查询 / 恢复扫描 | recover_on_startup（§3.2） |
| `update_execution_status` | L357 | 状态迁移（含 last_error/last_metrics/last_verdict） | 覆盖 monitoring/needs_refresh 语义 |
| `update_round_heartbeat` | L417 | 心跳更新 | M1 `_active_tasks` 健康管理 |
| `update_last_metrics` | L429 | 最近指标快照 | 兼容保留 |
| `delete_session_studies` | L505 | 会话级清理 | 兼容保留 |
| `add_directive` / `list_pending_directives` / `mark_directives_consumed` | L525-618 | Phase 2 执行中交互 | 保留（注入优先级最高，§13.2） |
| `update_monitor_check` / `list_due_for_monitor_check` | L622-682 | Phase 3 监控状态 | M7 移植后继续用 |
| `append_round` / `list_rounds` / `get_round` | L686-772 | study_rounds CRUD（表已建，append_round 无调用者） | **M3 唯一 DB 接线点** |

**v2 增量**：
- DB 接线（方法级，**无 schema 新增**）：`append_round`（M3，manifest 第一段
  轮末落主体）+ 新增 `update_round`（第二段评审后更新 `review` 段），payload
  字段与 manifest 摘要对齐：round/hypothesis/verdict/决策原因/metrics/review
- **无 schema 新增**：任务级状态（`last_keep_run_dir`/`best_metrics`/
  `continuous_deviation`/`last_completed_round`）存 state.json 不进 DB；
  monitor 周期字段 Phase 3 已有
- **并行安全已具备**：`_write_transaction`（L776，BEGIN IMMEDIATE + 进程内
  RLock）——多 study 并行写 study_rounds 无竞态，无需加锁改造

### 20.5 扩展点（v1 §13 保留，v2 不实现，决策 D8）

| 扩展点 | 简单展开 | 触发时机 |
|---|---|---|
| executors 注册表 | StudyExecutor 协议（start/pause/resume/cancel/status）注册多 executor；当前注册 AutoresearchRunner；未来注册 GoalWorkflowRunner（workflow 编排已独立存在），study 成为统一任务入口调度两者 | 未来需求（workflow 型 study） |
| metric_targets 增强 | 支持 threshold/window/confidence：如 max_dd 在**最近 N 个 monitor 检查窗口内超阈值**才判定 drift（防单次抖动误报）；当前 op 仅 `>=/<=/>/</==` | M7 monitor 需更稳 drift 判定时启用 |
| 预算模型扩展 | token/turn/time 三维之上加 cost（美元）维度；`_budget_exceeded` 加入新维度比较 | 计费/成本需求 |
| 断点存储 checkpoint_store | v1 预留项；**已实现替代**——v2 的 state.json + 每轮 manifest.json 即检查点，恢复语义见 §3.2，不回退 | — |
| knowledge 分层存储 | **方案 B → 方案 C**（MemGPT 式）：knowledge.md 只留活跃注入区，旧条目进 `knowledge-archive/` 按主题分文件 + 检索层（§11.3 升级路径） | 压缩触发过于频繁时启用（如每 5 轮一次） |
