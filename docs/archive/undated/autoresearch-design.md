# agentnodes 自动化策略研发框架设计文档

> 本文档记录 autoresearch 框架的完整架构设计。
> Agent 启动时应先读 `README.md`(操作指南),本文档作为完整参考。

## 1. 项目概述

**agentnodes** 是一个面向 AI Agent 的自动化策略研发框架。

- **目标**: Agent 自主迭代策略,直至 Calmar ≥ 0.5
- **设计哲学**: 所有指令写在磁盘文件里 — Agent 中途崩溃可从同套文件恢复 context
- **运行模式**: `LOOP FOREVER` — 持续迭代不主动停 (用户可能在睡觉)
- **分工**: 框架 = 工程脚手架 (CLI / DuckDB / git / 算子库), Agent = 决策 + 因子 + 评估
- **边界**: Agent 不动 `prepare.py` / `data.duckdb` / `.git/` — 那是框架域

## 2. 架构总览

### 2.1 角色拓扑

```
┌─────────────────────────────────────────────────────────────────────┐
│                 Main Process (Orchestrator + Main Agent)            │
│                                                                     │
│  职责:                                                              │
│  - 读状态 (Step 1)                                                  │
│  - spawn 每个 Subagent via Task tool (Step 2/3/5)                  │
│  - 保存记录到 runs/run_XXXX/agents/                                 │
│  - 传递输出给下一个 Agent                                           │
│  - 决策 keep/discard (Step 6)                                       │
│  - 监控卡住/重复                                                    │
│  - 速度控制                                                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ spawn via Task tool (串行)
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Subagents                                   │
│                                                                     │
│  Step 2: Researcher → agents/researcher.json                       │
│  Step 3: Data Quality → agents/data_quality.json                   │
│  Step 3: Factor Analyst → agents/factor_analyst.json               │
│  Step 3: Strategist → agents/strategist.json                       │
│  Step 3: Portfolio Construction → agents/portfolio_construction.json│
│  Step 5: Risk Controller → agents/risk_controller.json             │
│  Step 5: Attribution Analyst → agents/attribution_analyst.json     │
│  Step 5: Anti-overfit Analyst → agents/anti_overfit_analyst.json   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 关键约束

- **Task tool 嵌套限制**: 只能 spawn 一个 subagent,不能嵌套 spawn
- **串行执行**: 所有 Agent 由 Main Process 串行 spawn
- **立即保存**: 每个 Agent 完成后立即保存记录到 `runs/run_XXXX/agents/`
- **传递通信**: Agent 间通信通过 Main Process 传递 (不通过文件)

### 2.3 Agent 清单

| # | 角色 | 文件 | 核心问题 |
|---|------|------|---------|
| 1 | **Researcher** | `.prompts/researcher.md` | 下一步做什么? |
| 2 | **Data Quality** | `.prompts/data_quality.md` | 数据干净吗? |
| 3 | **Factor Analyst** | `.prompts/factor_analyst.md` | 哪些因子有效? |
| 4 | **Strategist** | `.prompts/strategist.md` | 怎么集成到策略? |
| 5 | **Portfolio Construction** | `.prompts/portfolio_construction.md` | 权重怎么分配? |
| 6 | **Risk Controller** | `.prompts/risk_controller.md` | 风险超标了吗? |
| 7 | **Attribution Analyst** | `.prompts/attribution_analyst.md` | 收益来源是什么? |
| 8 | **Anti-overfit Analyst** | `.prompts/anti_overfit_analyst.md` | 结果可信吗? |

## 3. Agent 角色定义

### 3.1 Main Agent (看门狗)

**职责**:启动 autoresearch loop,监控所有 Agent 是否卡住/重复,最终停止确认。

**不做的事**:
- 不跑 6 步循环 (Orchestrator 负责)
- 不做因子研究 / 策略评估 / 风控检查 (各角色负责)
- 不改 strategy.py (Strategist 负责)

**启动流程**:
```
读 README.md → 读 program.md → 读 strategy.py → 读 results.tsv
→ 确认工作区就绪
→ launch Orchestrator (传入 workspace path + strategy name)
```

**卡住检测** (任一命中 → interrupt 该 Agent):

| 检测项 | 阈值 | 动作 |
|--------|------|------|
| 同一个 action 连续出现 | ≥ 3 次 | interrupt Researcher, 重启时注入 "avoid_actions" |
| 同一个 hypothesis 连续出现 | ≥ 3 次 | interrupt Researcher |
| 同一个 factor candidate 被反复推荐 | ≥ 3 次 | interrupt Factor Analyst |
| Agent 输出 malformed JSON | 1 次 | interrupt 该 Agent, 重启 |
| Agent 输出包含回避语 | 1 次 | interrupt 该 Agent, 重启 |
| Agent 执行超时 | > 60s | interrupt 该 Agent, 重启 |
| Agent 输出与上轮完全相同 | 1 次 | interrupt 该 Agent, 重启 |

**停止条件** (必须 ALL 同时满足):

| 条件 | 阈值 |
|------|------|
| Calmar | ≥ 0.5 |
| Sharpe | ≥ 0.3 |
| MaxDD | ≤ -15% |
| 抗过拟合 | 6/6 全 pass |
| 因子池覆盖 | ≥ 80% (5/6 维) |
| 连续无改善轮数 | ≥ 10 轮 |
| 总轮数 | ≤ 99999 轮 (永远不停) |

**紧急停止条件** (任一命中 → 立即停):

| 条件 | 说明 |
|------|------|
| 总轮数 = 99999 | 硬上限 (实际永不触发) |
| 用户 Ctrl+C | 手动中断 |
| 所有 Agent 都被 interrupt 过且无改善 | 全部卡死 |
| DuckDB 写满 / 磁盘满 | 基础设施故障 |

### 3.2 Orchestrator (调度)

**职责**:跑 6 步循环,管理 Agent 间通信。

**每步 spawn 逻辑**:

| Step | 调度方 | 输入 | 输出 |
|------|--------|------|------|
| 1 读状态 | 自己 | `strategy.py`, `results.tsv` | 当前因子池 + 最近 run 摘要 |
| 2 决策 | spawn Researcher | 当前状态 + 上轮 Critic 反馈 | JSON `{action, hypothesis, avoid_actions}` |
| 3 执行 | spawn Data Quality → Factor Analyst → Strategist → Portfolio Construction | Step 2 action | 新 `strategy.py` + 回测 stdout |
| 4 保存 | 框架自动 | 回测 stdout | `runs/run_XXXX/...` + DuckDB |
| 5 评估 | spawn Risk Controller → Attribution Analyst → Anti-overfit Analyst | metrics + 当前策略 | JSON `{verdict, risk_rating, suggestions}` |
| 6 提交 | 自己 | verdict | `evaluate ... --status keep\|discard` |

**通信协议**:
- 共享文件: `strategy.py` / `results.tsv` / `runs/run_XXXX/` 是 Agent 间通信的唯一通道
- 同步: Subagent 阻塞完成, Orchestrator 拿到 JSON 再继续
- 不必 spawn: 读文件 + 推理类简单决策, Orchestrator 自己来

### 3.3 Data Quality (数据质量)

**职责**:在因子计算前验证数据质量。

**检查项**:
- NaN 比例 (< 5% pass)
- 交易日缺失 (连续 3 天以上 warning)
- 价格异常 (单日涨跌 > 20%)
- 除权因子 (需要检测)
- 数据指纹 (SHA-256)

**输入**: prices (from DuckDB)
**输出**: `{passed, warnings, data_fingerprint}`

**如果 data_quality 不通过**: Orchestrator 跳到 Step 4,记录 "data_quality_failed" 状态。

### 3.4 Researcher (研究员)

**职责**:偏见自检 + 因子池评估 + 行动决策 + Research Momentum。

**步骤**:
- Step 0: 偏见自检 (5 种偏见: 龙头/英文/叙事/确认/近因)
- Step 1: 因子池评估 (当前因子数,覆盖维度,缺少维度)
- Step 2: 行动决策 + Research Momentum (读 results.tsv 最近 10 轮,统计哪些 action 失败过,输出 "avoid_actions")
- Step 3: 假设 (hypothesis)

**输入**: 当前状态 + results.tsv 最近 10 轮
**输出**: `{action, hypothesis, avoid_actions, factor_direction}`

**action 类型**:
- `search_external`: 外部搜索因子
- `discover_local`: 本地算子挖掘
- `optimize_param`: 参数优化
- `remove_factor`: 因子移除

### 3.5 Factor Analyst (因子分析师)

**职责**:因子发现 (2 条路径) + 验证 (IC/IR/6D/Mutual IC/IC 衰减)。

**路径**:
- 路径 A: 本地算子 MCTS (含 LLM reasoning)
- 路径 D: Alpha Zoo (450+ 预置因子)

**验证流程**:
1. IC/IR 验证 (IC > 0.03, IR > 0.5)
2. 6 维评分 (Stability/Diversification/Turnover/Monotonicity/Coverage/Rank IC)
3. Mutual IC 去重 (|corr| < 0.7)
4. IC 衰减检查 (IC_5d >= 30% * IC_1d)

**输入**: prices + action + factor candidates
**输出**: `{path_used, candidates, rejected}`

### 3.6 Strategist (策略师)

**职责**:因子集成 + 参数优化 + 因子移除。

**操作**:
- 操作 1: 因子集成 (search_external 或 discover_local 后)
- 操作 2: 参数优化 (optimize_param)
- 操作 3: 因子移除 (remove_factor)

**输入**: 当前 strategy.py + candidates
**输出**: 修改 strategy.py (PARAMS / FACTOR_EXPRS / FACTOR_WEIGHT_METHOD)

### 3.7 Portfolio Construction (组合构建)

**职责**:风险平价 + 协方差估计 + 风险预算。

**方法**:
- 等权 (`equal`)
- 逆波动率 (`inv_vol`)
- 风险平价 (`risk_parity`)
- 最大分散化 (`max_diversification`)

**输入**: prices + strategy.py + Σ (协方差矩阵)
**输出**: `{weights, risk_contributions, diversification_ratio}`

### 3.8 Risk Controller (风控官)

**职责**:风控阈值 + VaR/CVaR + Monte Carlo + 压力测试 + 尾部风险。

**风控阈值**:
| 指标 | 阈值 |
|------|------|
| MaxDD | ≤ -15% |
| Calmar | ≥ 0.5 |
| Sharpe | ≥ 0.3 |
| 单资产权重 | ≤ 25% |
| 年化换手 | ≤ 600% |
| 因子数 | ≤ 30 |

**风险度量**:
- VaR/CVaR (历史模拟法)
- Monte Carlo (10000 条 GBM 路径)
- 压力测试 (2008/2015/2020/利率冲击/流动性枯竭)
- 尾部风险 (峰度/偏度)

**输入**: metrics + 当前策略
**输出**: `{risk_passed, risk_rating, var_95, cvar_95, stress_results}`

### 3.9 Attribution Analyst (归因分析师)

**职责**:Brinson 归因 + Fama-French + 牛熊捕获率。

**方法**:
- Brinson 归因 (配置效应 / 选股效应 / 交互效应)
- Fama-French (R_p - R_f = α + β_mkt × MKT + β_smb × SMB + β_hml × HML + β_mom × MOM)
- 牛熊捕获率 (牛市 > 100% → 跑赢, 熊市 < 100% → 防御)

**输入**: metrics + 当前策略
**输出**: `{alpha, beta_mkt, sector_allocation, stock_selection, bull_capture, bear_capture}`

### 3.10 Anti-overfit Analyst (抗过拟合分析师)

**职责**:6 种抗过拟合方法 + keep/discard 判断。

**6 种方法**:
1. 起点依赖: 3 起点 CV% < 25%
2. 调仓日偏移: ±5 日 Calmar 稳定 (CV% ≤ 15%)
3. 参数扰动: ±10% 退化 < 20%
4. 消融实验: 每关一项退化 >= 5%
5. Bootstrap: Sharpe 显著性 (t = Sharpe × sqrt(n) / sqrt(1 + 0.5×Sharpe²))
6. Monte Carlo 排列检验: 打乱交易顺序 1000 次, p < 0.05

**判断逻辑**:
- 目标函数改善 + 风控通过 + 统计显著 → keep
- 目标函数不变或退化 → discard
- 风控阈值触发 → discard
- 统计不显著 → 标记 warning

**输入**: metrics + 当前策略
**输出**: `{verdict: keep|discard, overfit_passed, methods_passed, suggestions}`

### 3.11 Backtest Diagnostics (回测诊断)

**职责**:错误分类 + 修复建议。

**错误分类**:
| 类型 | 症状 | 修复 |
|------|------|------|
| 零交易 | trade_count=0 | 检查信号逻辑 |
| 交易过晚 | 首笔 >2 年后 | 缩短 lookback |
| 资金闲置 | 利用率 <50% | 检查信号频率 |
| 期末持仓 | 结束时仍持仓 | 添加强制平仓 |

**修复原则**:精确修复,不重写;每次只修一个问题;最多 3 轮修复。

**输入**: run.log + metrics
**输出**: `{error_type, fix_suggestion, severity}`

## 4. 操作循环

### 4.1 调度图

```
                      ┌─────────────────── Main Agent ────────────────────┐
                      │                                                  │
   ┌────────────┐     │  Step 1 读状态    ───── 自己读                     │
   │ 上轮       │◀────┤                                                  │
   │ Anti-overfit│     │  Step 6 提交      ───── evaluate + git commit     │
   │ 反馈       │     │                                                  │
   └────────────┘     │  Step 2 决策         ── spawn ─→  Researcher     │
                      │                        ↓ action JSON             │
                      │  Step 3 执行                                     │
                      │    ├── DQ 检查 ────── spawn ─→  Data Quality     │
                      │    ├── 因子发现 ───── spawn ─→  Factor Analyst   │
                      │    ├── 策略集成 ───── spawn ─→  Strategist       │
                      │    └── 组合构建 ───── spawn ─→  Portfolio Constr. │
                      │                        ↓ 新 strategy.py          │
                      │  Step 4 保存 ─────── [框架自动]                   │
                      │        runs/run_XXXX/{metrics.json,              │
                      │                       run_card.json,              │
                      │                       run_card.md}                │
                      │  Step 5 评估                                     │
                      │    ├── 风控检查 ───── spawn ─→  Risk Controller  │
                      │    ├── 归因分析 ───── spawn ─→  Attribution      │
                      │    └── 抗过拟合 ───── spawn ─→  Anti-overfit     │
                      │                        ↓ verdict JSON             │
                      └──────────────────────────────────────────────────┘
```

### 4.2 通信协议

- **中介文件**: `strategy.py` / `results.tsv` / `runs/run_XXXX/` 是 Agent 间通信的唯一通道
- **JSON 协议**: spawn 时把任务序列化进 .prompts/ schema, 完成后 Subagent 写盘 + stdout 返回 JSON
- **同步**: Subagent 阻塞完成, Orchestrator 拿到 JSON 再进下一步
- **不必 spawn**: 读文件 + 推理类简单决策 Orchestrator 自己来

## 5. Git-as-log 系统

### 5.1 4 层索引设计

| 层 | 数据 | 用途 | 查询方式 |
|---|------|------|---------|
| **DuckDB** | 8 张表 (因子/验证/回测/指纹) | 主索引 | SQL |
| **results.tsv** | 每行一个 run | 快速人读 | tail / grep |
| **Git** | commit 历史 | 审计 + 恢复 + diff | git log / git diff / git checkout |
| **runs/** | 每个 run 的详细产物 | 详细分析 | cat metrics.json |

### 5.2 快速索引查询

**DuckDB SQL**:
```sql
-- 最近 10 个实验
SELECT run, calmar, sharpe, max_dd, status, action_type, hypothesis
FROM backtest_results
WHERE strategy_name = 'rot_alpha'
ORDER BY created_at DESC
LIMIT 10;

-- 所有 keep 实验, 按 Calmar 排序
SELECT run, calmar, sharpe, max_dd, hypothesis
FROM backtest_results
WHERE strategy_name = 'rot_alpha' AND status = 'keep'
ORDER BY calmar DESC;

-- 平均 Calmar (keep vs discard)
SELECT status, AVG(calmar), COUNT(*)
FROM backtest_results
WHERE strategy_name = 'rot_alpha'
GROUP BY status;
```

**Git**:
```bash
git log --oneline                    # 时间线
git log --oneline --grep="^keep:"   # 只看 keep
git log --oneline --grep="^discard:" # 只看 discard
git diff run_0003 run_0005          # 比较两个实验
git checkout HEAD                    # 恢复到最后状态
```

### 5.3 Commit message 格式

```
{status}: {strategy_name}/{run_name} | Calmar={X.XX} Sharpe={X.XX} MaxDD={X.XX%} | {action}: {hypothesis}
```

示例:
```
keep: rot_alpha/run_0003 | Calmar=0.71 Sharpe=0.82 MaxDD=-11.2% | discover_local: "加入量价因子提升IC"
discard: rot_alpha/run_0004 | Calmar=0.23 Sharpe=0.15 MaxDD=-28.5% | optimize_param: "top_n=3 过于集中"
```

### 5.4 实现改动

- `backtest.evaluate_experiment()`: 每次 run 都 commit (不只是 keep)
- `backtest.evaluate_experiment()`: commit message 格式改为 rich format
- `git.py`: 新增 `git_commit_rich()` 函数
- `db.py`: 扩展 `backtest_results` 表 (新增 hypothesis/verdict/risk_rating/action_type 字段)

## 6. 默认 Baseline

### 6.1 策略

默认 baseline = **buy and hold 沪深300**。

```python
PARAMS = {
    "top_n": 1,
    "max_weight": 1.0,
    "rebalance_freq": 999999,
}

FACTOR_EXPRS = []  # 无因子 — buy and hold

FACTOR_WEIGHT_METHOD = "equal"
```

### 6.2 基线回测

`cli.cmd_init` 时自动跑一次:
1. 生成 HS300 模拟数据 (`generate_sample_data(1, 600, "2020-01-01")`)
2. 灌入 DuckDB
3. 跑 `python strategy.py` (buy and hold)
4. 保存为 `runs/run_0000/` (status="baseline")
5. 更新 `results.tsv` (第一行: baseline metrics)
6. `git commit` (第一个 commit: "seed: HS300 buy-and-hold baseline")

### 6.3 用户覆盖

如果用户指定不同 baseline:
- `cli.cmd_init --baseline=custom_strategy.py`
- `cli.cmd_init --baseline=none` (跳过 baseline)

## 7. 停止条件

详见 §3.1 Main Agent 的停止条件。

## 8. 速度控制

每轮之间需要控制节奏,不能太快也不能太慢。

### 8.1 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `round_cooldown` | 30s | 两轮之间最少间隔 (秒) |
| `analysis_timeout` | 120s | 单个 Agent 分析超时 (秒) |
| `stuck_threshold` | 3 | 连续相同输出次数判定卡住 |

### 8.2 节奏规则

- **改善中**: 正常速度 (cooldown 秒)
- **连续 3 轮无改善**: 减速 (cooldown × 2)
- **连续 5 轮无改善**: 再减速 (cooldown × 4)
- **卡住检测到**: interrupt + 重启 Agent

### 8.3 实现

Orchestrator 在每轮结束时检查:
```python
import time

# 计算本轮耗时
round_time = time.time() - round_start

# 根据连续无改善轮数调整 cooldown
if consecutive_no_improve >= 5:
    cooldown = base_cooldown * 4
elif consecutive_no_improve >= 3:
    cooldown = base_cooldown * 2
else:
    cooldown = base_cooldown

# 如果本轮太快,等待
if round_time < cooldown:
    time.sleep(cooldown - round_time)
```

## 9. 文件结构

### 8.1 工作区结构

```
workspace/
├── README.md                    ← Agent 第一个读: 操作指南
├── config.yaml                  ← workspace 配置
├── data.duckdb                  ← 8 张表状态中枢
├── .git/                        ← 实验版本控制
├── .prompts/                    ← 10 个 Subagent 角色说明书
│   ├── orchestrator.md
│   ├── data_quality.md
│   ├── researcher.md
│   ├── factor_analyst.md
│   ├── strategist.md
│   ├── portfolio_construction.md
│   ├── risk_controller.md
│   ├── attribution_analyst.md
│   ├── anti_overfit_analyst.md
│   └── backtest_diagnostics.md
├── .skills/                     ← 9+1 份方法论文档
│   ├── data-routing.md
│   ├── factor-research.md
│   ├── backtest-diagnose.md
│   ├── correlation-analysis.md
│   ├── ml-strategy.md
│   ├── performance-attribution.md
│   ├── quant-statistics.md
│   ├── risk-analysis.md
│   ├── sector-rotation.md
│   └── research-discipline.md   ← 新增
└── strategies/
    └── <name>/
        ├── strategy.py          ← Agent 唯一可改 (PARAMS / FACTOR_EXPRS / FACTOR_WEIGHT_METHOD)
        ├── program.md           ← 策略 playbook (必读)
        ├── prepare.py           ← 框架调用, 不要改
        └── runs/
            ├── results.tsv      ← 实验记录
            ├── run_0000/        ← baseline
            ├── run_0001/        ← 实验 1
            └── ...
```

### 8.2 .prompts/ 说明

每个 .prompts/*.md 文件定义一个 Subagent 角色:
- 角色名称
- 职责描述
- 输入/输出 schema (JSON)
- 步骤说明
- 规则/约束

### 8.3 .skills/ 说明

每个 .skills/*.md 文件定义一份方法论文档:
- 算子语法 (factor-research.md)
- 数据源路由 (data-routing.md)
- 风控阈值 (risk-analysis.md)
- 抗过拟合方法 (quant-statistics.md)
- 归因方法 (performance-attribution.md)
- 偏见自检 (research-discipline.md)

## 9. 实现计划

### 9.1 优先级

| 优先级 | 工作 | 文件 |
|--------|------|------|
| P0 | 写设计文档 | `docs/archive/undated/autoresearch-design.md` |
| P0 | 改 README | `templates/README.md` |
| P0 | 改 program.md | `templates/program.md` |
| P0 | 改 strategy.py | `templates/strategy.py` |
| P0 | 创建 research-discipline | `templates/.skills/research-discipline.md` |
| P1 | 创建/修改 10 个 .prompts | `templates/.prompts/` |
| P1 | 改 cli.py (baseline) | `src/agentnodes/cli.py` |
| P2 | 改 backtest.py (git-as-log) | `src/agentnodes/core/backtest.py` |
| P2 | 改 git.py (rich message) | `src/agentnodes/core/git.py` |
| P2 | 改 db.py (扩展表) | `src/agentnodes/core/db.py` |

## 10. 实现细节

### 10.1 辅助函数 (autoresearch.py)

```python
# 核心函数
build_agent_prompt()      # 构造 Agent prompt
save_agent_record()       # 保存 Agent 记录到 runs/run_XXXX/agents/
read_current_state()      # 读取当前状态 (strategy.py + results.tsv)
parse_agent_output()      # 解析 Agent 输出 (自动处理 markdown 包裹)
retry_agent_spawn()       # 重试 Agent spawn (最多 3 次)
get_cooldown_seconds()    # 计算带随机抖动的 cooldown 时间
```

### 10.2 输出格式要求

所有 Agent 必须返回纯 JSON,不要包含 markdown 代码块标记。

**容错解析**:
1. 尝试直接 JSON 解析
2. 提取 ```json ... ``` 中的内容
3. 提取 ``` ... ``` 中的内容
4. 提取 { ... } 或 [ ... ] 中的内容
5. 返回 {"error": "parse_failed", "raw": raw_output[:1000]}

### 10.3 速度控制

**Cooldown 配置**:
- `base_cooldown`: 30 秒 (默认)
- `jitter`: 10 秒 (±随机抖动)
- `min_cooldown`: 1 秒 (最小值)

**实际 cooldown** = `base_cooldown + random(-jitter, +jitter)`, 最小 `min_cooldown`

**执行位置**: 每个 Agent 之间 (Step 3.1→3.2→3.3→4→5.1→5.2→5.3→5.4)

**轮间 cooldown**: `base_cooldown * 2 ± jitter * 2`

### 10.4 强制执行

所有 Agent 强制执行,无论前一个 Agent 输出如何:
- Portfolio Construction: 即使 Strategist 无新因子,仍检查/优化现有权重
- Backtest Diagnostics: 即使 Risk Controller 无问题,仍检查回测日志

### 10.5 重试机制

**重试策略**:
- 最大重试次数: 3 (可配置)
- 重试间隔: 5 秒
- 重试条件: JSON 解析失败或执行异常

**重试流程**:
1. 执行 Agent spawn
2. 解析输出 (parse_agent_output)
3. 如果解析失败,等待 5 秒后重试
4. 如果 3 次都失败,返回 {"error": "max_retries_exceeded"}

### 10.6 CLI 命令

```bash
# 运行自动化研究循环
agentnodes autoresearch <path> \
  --strategy <name> \
  --cooldown 30 \        # 基础 cooldown (秒)
  --jitter 10 \          # 随机抖动范围 (±秒)
  --min-cooldown 1 \     # 最小 cooldown (秒)
  --max-retries 3 \      # 最大重试次数
  --max-rounds 100       # 最大轮数 (不指定则无限循环)
```

## 11. 测试验证

### 11.1 第 13 轮测试结果

| Agent | 输出 | 状态 |
|-------|------|------|
| Researcher | action=search_external | ✅ |
| Data Quality | passed=True | ✅ |
| Factor Analyst | candidates=0, rejected=8 | ✅ |
| Strategist | vol_20d 退化,已回退 | ✅ |
| Portfolio Construction | 未执行 | ❌ (已修复) |
| Risk Controller | risk_passed=False, Red | ✅ |
| Attribution Analyst | alpha=-0.39% | ✅ |
| Anti-overfit Analyst | verdict=discard | ✅ |
| Backtest Diagnostics | 未执行 | ❌ (已修复) |

### 11.2 修复内容

1. **强制执行**: Portfolio Construction + Backtest Diagnostics 强制执行
2. **统一格式**: 所有 .prompts/*.md 添加纯 JSON 格式要求
3. **容错解析**: 添加 parse_agent_output() 函数
4. **速度控制**: 添加 cooldown 配置 (30s ± 10s, MIN=1s)
5. **重试机制**: 添加 retry_agent_spawn() 函数 (最多 3 次)

## 12. Lazy Detection (懒惰检测)

### 12.1 目的

检测 Agent 是否在 "偷懒" — 返回相同或过于简单的输出,没有真正分析当前状态。

**问题示例**:
- Researcher: 每轮返回相同的 hypothesis ("波动率因子可能有效")
- Factor Analyst: 每轮返回 0 个候选因子
- Anti-overfit Analyst: 每轮返回 discard,没有详细分析

### 12.2 检测频率

- **检测间隔**: 每 10 轮检测一次 (而不是每轮)
- **检测时机**: 在 Step 1 读状态时,如果 `round_num % 10 == 0` 则执行检测
- **影响范围**: 只读取历史记录并生成报告,不干预正常流程

### 12.3 检测逻辑

**函数**: `detect_lazy_behavior(agent_name, current_output, history, threshold=10)`

| Agent | 检测标准 | lazy_score |
|-------|---------|------------|
| Researcher | hypothesis 重复 | +0.5 |
| Researcher | action 重复 | +0.3 |
| Factor Analyst | candidates 连续为空 (≥3 轮) | +0.3 |
| Factor Analyst | rejected 因子相同 | +0.2 |
| Strategist | changes 连续为空 (≥3 轮) | +0.4 |
| Strategist | action 连续相同 | +0.3 |
| Risk Controller | risk_rating 连续相同 | +0.2 |
| Anti-overfit Analyst | verdict 连续 discard (≥3 轮) | +0.4 |
| Anti-overfit Analyst | overfit_passed 连续 false | +0.3 |

**lazy_score 阈值**:
- < 0.3: 正常
- 0.3 - 0.7: 轻度懒惰
- > 0.7: 严重懒惰

### 12.4 输出报告

**函数**: `save_laziness_report(run_dir, round_num, lazy_results, overall_lazy_score)`

**保存位置**: `runs/run_XXXX/laziness_report.json`

**输出格式**:
```json
{
  "round": 10,
  "timestamp": "2026-07-22T08:44:12",
  "overall_lazy_score": 0.6,
  "agents": [
    {
      "agent": "researcher",
      "lazy_score": 0.5,
      "issues": ["hypothesis 与上轮相同"]
    },
    {
      "agent": "factor_analyst",
      "lazy_score": 0.3,
      "issues": ["连续 3 轮无候选因子"]
    }
  ],
  "summary": "Researcher 和 Factor Analyst 存在轻度懒惰行为"
}
```

### 12.5 CLI 集成

```bash
# 运行自动化研究循环 (默认每 10 轮检测)
agentnodes autoresearch <path> \
  --strategy <name> \
  --lazy-detection-interval 10  # 懒惰检测间隔 (轮数)
```

### 12.6 实现位置

| 文件 | 修改内容 |
|------|---------|
| `autoresearch.py` | 添加 `should_run_lazy_detection()`, `detect_lazy_behavior()`, `save_laziness_report()`, `read_agent_history()` |
| `cli.py` | 在 Step 1 之后添加检测逻辑 (条件: `round_num % 10 == 0`) |
| `design doc` | 本章节 |

## 13. 分层上下文 (Layered Context) - 暂不实施

**状态**: 📝 设计完成, ⏸️ 暂不实施 (2026-07-22 决策)

### 13.1 动机

当前每个 Agent 接收相同的完整上下文 (strategy.py 全文 + recent_runs + 累积 previous_outputs):
- 每个 Agent 平均 ~1K tokens context
- Anti-overfit 输入累积 8 个 outputs (~3.2KB)
- 通过分层可优化 token 使用

**为什么不实施**: 当前 token 压力在可控范围,实施复杂度高,收益相对有限 (~14% 平均 context 减少)。等出现明显瓶颈再考虑。

### 13.2 设计 (待未来实施)

**核心思路**: 不同 Agent 看不同粒度的信息,优化 token 使用。

**9 个 Agent 分层配置**:

| Agent | needs | max_chars | 当前 (chars) | 优化后 (chars) | 变化 |
|-------|-------|-----------|------------|--------------|------|
| researcher | strategy_summary, recent_summaries | 1500 | 2322 | 1500 | **-35%** |
| data_quality | data_fingerprint | 500 | 742 | 500 | **-33%** |
| factor_analyst | researcher_action, factor_pool | 1000 | 1021 | 1000 | -2% |
| strategist | factor_candidates, strategy_key_params | 1200 | 974 | 1200 | +23% |
| portfolio_construction | factor_scores | 600 | 449 | 600 | +34% |
| risk_controller | current_metrics, risk_thresholds | 600 | 755 | 600 | -21% |
| attribution_analyst | current_metrics | 600 | 1085 | 600 | **-45%** |
| anti_overfit_analyst | all_agent_summaries, current_metrics | 1800 | 1545 | 1800 | +17% |
| backtest_diagnostics | run_log, current_metrics | 500 | 712 | 500 | **-30%** |

### 13.3 待实施函数

```python
# 待添加: 分层配置
AGENT_LAYER_CONFIG = {
    "researcher": {"needs": ["strategy_summary", "recent_summaries"], "max_chars": 1500},
    "data_quality": {"needs": ["data_fingerprint"], "max_chars": 500},
    # ... 其他 7 个 Agent
}

# 待添加: 单个 Agent output 摘要
def summarize_agent_output(agent_name: str, output: dict) -> str:
    """把单个 agent output 压缩成 ~50 chars 摘要。"""

# 待添加: 分层 prompt 构建 (核心)
def build_agent_prompt_layered(
    agent_name: str,
    prompts_dir: Path,
    current_state: dict,
    previous_outputs: list[dict],
    keep_recent: int = 10,
) -> str:
    """分层构建 Agent prompt。"""
```

### 13.4 关键设计: previous_outputs 改用摘要

**当前问题**:
- Strategist input 累积 3 个完整 outputs (~1.2KB)
- Portfolio input 累积 4 个完整 outputs (~1.6KB)
- Anti-overfit input 累积 8 个完整 outputs (~3.2KB!)

**优化方案**:
```python
# 当前 (累积完整)
previous_outputs = [researcher_out, data_quality_out, factor_analyst_out]

# 优化后 (只传摘要)
previous_outputs_summary = {
    "researcher": "search_external | volatility | hypothesis: ...",
    "data_quality": "passed=True | warnings=1",
    "factor_analyst": "vol_adj_mom (IC=0.052)",
}
```

### 13.5 触发条件

如果出现以下情况,启用 Phase 4:
- 单轮总 token 超过 5K
- Anti-overfit 输入超过 2K (累积 8+ outputs)
- 运行超过 1000 轮
- Agent 频繁报告"信息不足"

### 13.6 风险与缓解 (未来实施时考虑)

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| Agent 缺少关键信息 | 高 | 配置 max_chars 和 needs 字段,确保必要信息 |
| 摘要丢失细节 | 中 | 保留 summary.json 包含 key_insight,Agent 可读完整文件 |
| 向后兼容 | 中 | 保留 `build_agent_prompt` 旧函数,通过 use_layered 切换 |
| 测试覆盖 | 中 | 为每个 Agent 写测试,验证 context 大小 |

### 13.7 实施步骤 (未来)

1. 添加 `AGENT_LAYER_CONFIG` 和 `summarize_agent_output` (~50 行)
2. 添加 `build_agent_prompt_layered` 核心函数 (~100 行)
3. 集成到 `cmd_autoresearch` (替换 `build_agent_prompt` 调用, ~20 行)
4. 添加单元测试 (验证每个 Agent context 大小, ~100 行)
5. 完整 autoresearch 测试 (验证流程不变)

---

## 附录: 实施路线图总结

| Phase | 内容 | 状态 | 风险 |
|-------|------|------|------|
| 1 | summary.json 生成 (每轮) | 📝 设计完成 | 低 |
| 2 | 可配置 keep_recent + CLI 参数 | 📝 设计完成 | 低 |
| 3 | 分层读取 (详细/摘要) | 📝 设计完成 | 低 |
| 4 | 分层上下文 (Agent prompt 优化) | 📝 记录, 暂不实施 | 中 |

**最近一次更新**: 2026-07-22 — 完成 lazy detection + 模拟 agent 行为 + 记录 Phase 4 设计
