# Study 长任务后台化设计（日志进度驱动）

> 版本：v1（2026-08-14）· 状态：设计定稿 · 实现：S1-S8 分步
> 前置：`docs/archive/undated/study-longhorizon-v2-design.md`（study v2）、`docs/archive/undated/scheduled-study-link-plan.md`
> 关联：`core/utils/bg_proc.py`（新基础设施）、`core/agent/builtin_tools/bg_tools.py`（新工具集）

## 1. 背景与目标

### 1.1 问题

长任务（长回测、大数据因子计算、数据准备、未来任意长程序）会卡住执行链：

| 环节 | 执行方式 | 现状 | 问题 |
|---|---|---|---|
| LLM 生成 | 流式（httpx read timeout 60s/次） | 慢吐无总时长兜底 | 无限等待 |
| 策略脚本回测 | subprocess `run_strategy` | **300s 墙钟硬超时** | **合法长回测（>5min）被杀** |
| 引擎回测（agent 工具链） | 进程内同步 | 无超时 | 阻塞 agent、无进度可见 |
| 整轮 | 串行 9 agent + 回测 | watchdog heartbeat 1h | 检测粒度粗、误杀长任务 |

### 1.2 目标

1. **长任务不阻塞**：转为后台执行，日志持续落盘（`run.log`）
2. **卡死可判定**：日志 mtime 推进 = 正常（无限期）；停滞 > 阈值 = 卡死 → kill
3. **不误杀长任务**：只要日志在涨，跑多久都行（判定依据 = 日志内容推进，非时间戳）
4. **agent 可协作**：显式确认转后台 + 轮询日志尾部，可读中间错误提前决策
5. **通用基础设施**：不只服务 backtest——任何未来长程序（因子计算/训练/下载）用同一套机制

## 2. 核心机制（nohup 语义）

```
启动：nohup <cmd> > run.log 2>&1 &        → Python 等价：run_bg()（Popen + 日志流式 + start_new_session）
判定：日志 mtime 推进 = 正常              → wait_bg() 继续等（无限期）
卡死：存活但日志停滞 > 300s              → killpg 全组杀
完成：进程退出码 0 + run.log 就绪         → 结果解析
```

**判定模型（统一）**：

| 状态 | 判定 | 处置 |
|---|---|---|
| 活跃 | run.log mtime 持续推进 | 继续等 |
| 卡死 | 存活但停滞 > stall_timeout（默认 300s） | killpg + 标记 + 告警（停滞秒数 + 最后日志行） |
| 完成 | 进程退出 | 解析结果 |
| LLM 阶段 | 无后台任务 | 由 LLM 墙钟兜底（`AN_AGENT_WALLCLOCK_TIMEOUT` 30min），watchdog 跳过 |

## 3. 架构总览

```
┌─ 基础设施层：core/utils/bg_proc.py（纯函数，无类无状态）────────┐
│  run_bg / wait_bg / log_progress / log_tail / is_stalled        │
│  ← backtest.py、engine、runner、watchdog、工具集共同调用         │
└──────────────────────────┬─────────────────────────────────────┘
                           ▼
┌─ 工具集层：core/agent/builtin_tools/bg_tools.py（单入口）───────┐
│  run_bg_command(action: start|status|wait|log|kill, ...)       │
│  模块级注册表 {task_id → handle}（进程内 dict + lock）           │
└──────────────┬──────────────────────────────┬─────────────────┘
               ▼                              ▼
┌─ 消费方 A：runner/backtest ──────┐  ┌─ 消费方 B：agent ───────────┐
│  run_strategy → bg_proc          │  │  run_backtest(background=True) │
│  _current_log 暴露给 watchdog    │  │  run_bg_command 轮询/收割      │
│  轮结束收割注册表孤儿任务          │  │  观察窗 × 3 → 交回 watchdog    │
└──────────────────────────────────┘  └──────────────────────────────┘
```

## 4. 触发机制（全显式，零自动）

| 入口 | 显式参数 | 行为 |
|---|---|---|
| `run_backtest` 工具 | `background: bool=False` | 默认前台（现状不变）；`True` → 后台启动 → 返回 `{status:"running", task_id, log}` |
| `run_bg_command(start, command=...)` | 动作即确认 | 任意命令后台化 |
| `run_command` | **不加** background | 保持纯前台（后台启动统一走 `run_bg_command`，入口收敛） |

**前台超时恢复模式**：前台超时 → 错误提示"如预期耗时长，请用 `background=True` 重试" → agent 第二次调用显式加 background（主动确认）→ 后台化。

## 5. 单入口工具 `run_bg_command`

```
run_bg_command(action: str, *, task_id: str = "", command: str = "",
               cwd: str = "", log: str = "", seconds: int = 15, n_lines: int = 20)

  action="start"  → command+cwd+log → 后台启动 → {task_id, log}
  action="status" → task_id → {running|stalled|done, exit_code, 停滞秒数, 尾部3行}
  action="wait"   → task_id, seconds → 观察窗（内部 sleep）→ status
  action="log"    → task_id, n_lines → 日志尾部
  action="kill"   → task_id → killpg + 注销
```

- 白名单 4 角色：strategist / backtest_diagnostics / factor_analyst / data_quality

## 6. Agent 协作协议

```
预判长任务 → run_backtest(background=True) 或 run_bg_command(start, command)
  → {status:"running", task_id, log}
  → 观察窗 × 3：run_bg_command(wait, task_id, seconds=15)   ← 1 迭代/窗
  → 3 次无进展 → run_bg_command(kill) 主动放弃，或报告停滞交回 watchdog
  → 完成（exit_code=0）→ 读 metrics.json / run_card.json 正常继续
```

分工：**agent 管前 ~45s 观察，watchdog 管 300s+ 停滞兜底**。token 纪律：日志尾部 ≤20 行/次。

迭代预算：观察窗 3 + 触发/收尾 ≈ 6-7 ≤ 8 —— study 场景 strategist/backtest_diagnostics `max_iterations 8→10` 留余量（CLI 不受影响）。

## 7. 超时体系总表（A/B/C/D 关系）

| 机制 | 处置 | 默认 |
|---|---|---|
| **A** LLM per-call 墙钟 | 保留（LLM 生成不后台化，同步交互） | `AN_AGENT_WALLCLOCK_TIMEOUT` 1800s |
| **B** 轮级墙钟 | 显式治理选项，**默认禁用**（`AN_STUDY_ROUND_TIMEOUT` 0=禁用） | 禁用 |
| **C** heartbeat 阶段级 | 判定依据升级为日志推进；heartbeat 字段保留做前端展示/兜底 | — |
| **D** 幽灵轮守卫 | 保留（kill 后台任务后的清理/状态守卫） | — |
| **watchdog** | 升级为日志停滞检测：runner `_current_log` + 注册表停滞 → killpg + INTERRUPTED | `AN_BACKTEST_STALL_TIMEOUT` 300s |
| **engine 埋点** | execute_bars 每 N bar print 进度行（N=max(1,bars//100)，≤100 行/回测） | `progress_every` 默认 None 零输出 |

## 8. 通用后台化约定（未来程序接入 checklist）

任何长程序后台化三要素：

1. **CLI 入口可独立运行**（如 `agentnodes engine run-backtest`）——后台化 = 子进程启动它
2. **stdout 打进度**（每 N 步/每阶段一行）→ run.log 自动捕获，停滞判定与 agent 观察都靠它
3. **接入模式**：`proc = run_bg(cmd, run.log)` → `wait_bg(proc, run.log)`——回测、因子计算、数据准备、训练脚本全部同款

停滞/完成/卡死判定与程序类型**无关**，全部由 `bg_proc` 统一提供。

## 9. 文件清单

### 新增
```
core/utils/bg_proc.py                    — 基础设施纯函数
core/agent/builtin_tools/bg_tools.py     — run_bg_command 工具集 + 注册表
templates/.prompts/_common/rules/long-task.md — agent 协作协议（按需读取）
tests/test_bg_proc.py
tests/test_bg_tools.py
tests/test_engine_progress.py
docs/archive/undated/study-long-task-background-plan.md  — 本文档
```

### 修改
```
core/backtest.py                     — run_strategy → bg_proc（停滞语义）
core/engine/base.py                  — progress_every 埋点
core/agent/builtin_tools/backtest_tools.py — run_backtest background 参数 + 超时提示
core/agent/builtin_tools/shell_tools.py    — （不加 background；前台超时提示对齐）
core/study/runner.py                 — _current_log 暴露 + 轮结束收割
core/study/scheduler.py              — watchdog 日志停滞检测
core/agent/role_factory.py           — 4 角色白名单 + bg 工具
core/autoresearch.py                 — study 场景迭代上限 8→10（2 角色）
```

## 10. 实施步骤

| 步骤 | 内容 | 验收 |
|---|---|---|
| S1 | 设计文档（本文档） | ✅ `b5f2700` |
| S2 | bg_proc 基础设施 + 单测 | ✅ `4bd3457`（13 tests） |
| S3 | run_strategy 后台化 + backtest 测试改写 | ✅ `56993d6`（48 tests） |
| S4 | engine progress_every + 测试 | ✅ `6ead907`（653 engine 系） |
| S5 | run_bg_command 工具集 + 测试 | ✅ `b003712`（9 tests） |
| S6 | run_backtest background 参数 + 测试 | ✅ `eace71f`（4 tests） |
| S7 | 注册表下沉 + runner 收割 + watchdog + 测试 | ✅ `91a93ed`（31 tests） |
| S8 | rules + 白名单 + 迭代预算 + 断言 | ✅ `5cb9999` |
| S9 | 回归全量 + 文档实施记录 | ✅ **10813 passed / 0 failed** |

### 实施记录（S2-S8 落地要点）

- **S2**：`core/utils/bg_proc.py` — run_bg（Popen+流式+start_new_session）/wait_bg（启动宽限 max(10s, 5×poll) + 停滞判定）/log_tail/is_stalled/kill_bg/log_progress；`AN_BACKTEST_STALL_TIMEOUT` 默认 300
- **S3**：`run_strategy` 超时语义 墙钟→日志停滞；run.log 流式（删一次性写入）；stalled 错误带日志尾部
- **S4**：`BaseEngine.run_backtest(progress_every)` — align/每 N bar（N=max(1,bars//100)，≤100 行）/metrics 三处 stdout 进度行；CLI run-backtest 默认开启（progress_every=0 自动频率）
- **S5**：`run_bg_command` 单入口（start/status/wait/log/kill）+ 模块级注册表 + opt-in 注册
- **S6**：`run_backtest(background=True)` 线程化后台（步骤级日志 + 完成态带回 result）；前台默认不变；线程任务 kill 仅注销
- **S7**：注册表下沉 bg_proc（`owner` 归属 + `harvest_by_owner`）；runner 轮结束收割（放 stop 条件前，避免 max_rounds 早退绕过）+ run() finally 兜底；watchdog 第三分支扫注册表停滞任务（kill + 注销 + study_interrupted 事件）
- **S8**：`_common/rules/long-task.md`（何时显式转后台/观察窗 ×3/停滞交接/token 纪律）+ INDEX 索引；4 角色白名单（strategist/backtest_diagnostics/factor_analyst/data_quality）加 run_bg_command；study 三 phase `max_iterations=10`（CLI 默认 8 不变）；工具说明书契约（版本/变更 + 6 章节）

### 关键修正（实施中）

- wait_bg 停滞判定参考点改为当前时间 + 启动宽限（原实现 last_activity 引用导致永不判定停滞）
- 轮收割插点在 stop 条件检查之前（max_rounds/stagnation 早退不绕过）
- engine 进度行去掉"末尾补打"（整除时多 1 行，改为 metrics done 表完成）
- `_make_spawn_fn` 加 max_iterations 透传时曾重复参数（脚本替换误伤），已修复

### 追加轮：A（LLM 流式墙钟）落地（2026-08-14，`e546446`）

- `LLMConfig.wallclock_timeout_s` 默认 **1800s**（30min）；env `AN_AGENT_WALLCLOCK_TIMEOUT` 注入（`_env_to_overrides`）；**≤0 禁用**
- `stream()`/`astream()`：请求级 `deadline`（monotonic）——每 chunk 到达时 + 每次重试开始前检查，超时抛 `LLMTimeoutError("wall-clock ...")`；**重试穿透**（墙钟超时不再重试）
- 语义：httpx 60s 是"单次读等待"，墙钟是"整个请求总预算"——慢吐流（每 59s 一行永不 finish）由墙钟兜底
- 测试 `test_llm_wallclock.py` 7 个：sync/async 慢吐命中、正常流通过、0 禁用、重试前超时即抛、env 注入、默认值

## 11. 风险与对策

| 风险 | 等级 | 缓解 |
|---|---|---|
| 显式模式依赖 agent 判断"何时长" | 中 | rules 判断标准 + 前台超时提示重试路径 |
| 孤儿任务（agent 放弃后） | 中 | 轮结束收割 + watchdog 停滞兜底双保险 |
| engine CLI 子进程数据加载 | 低 | run_engine_backtest 已参数化（M2），实施时验证 |
| 孙进程泄漏 | 低 | start_new_session + killpg 全组杀 |
| 停滞误判（静默计算段） | 低 | 埋点覆盖长段（align/每 N bar/metrics）+ 300s 保守阈值 |
| 行为变化（长回测不再 300s 被杀） | 低 | 测试改写 + 文档标注 |
