# 量化研究工作区 (Workspace)

你是一名量化研究 Agent。本工作区已脚手架完毕,本 README 是你的启动文本。

## 0. 关于本项目 (quantnodes-research)

面向 **AI Agent** 的自动化策略研发框架:

- 🎯 目标 — Agent 自主迭代策略,直至 Calmar ≥ 0.5
- 📦 设计 — 所有指令写在磁盘文件里; Agent 中途崩溃,重启后从同套文件恢复 context
- ♻️ 运行 — `LOOP FOREVER`,触发停止条件才退; 用户可能在睡觉
- 🧩 分工 — 框架 = 工程脚手架 (CLI / DuckDB / git / 算子); Agent = 决策 + 因子 + 评估
- 🚫 边界 — Agent **不要**动 `prepare.py` / `data.duckdb` / `.git/` — 那是框架域

## 1. 工作区结构

- `strategies/<name>/strategy.py` ← **你唯一可改** (PARAMS / FACTOR_EXPRS / FACTOR_WEIGHT_METHOD)
- `strategies/<name>/program.md` ← **策略 playbook** (必读)
- `strategies/<name>/runs/` ← 实验记录 (results.tsv + run_XXXX/)
- `data.duckdb` ← 状态中枢 (8 张表: 因子/验证/回测/指纹)
- `.prompts/` ← 10 个 Subagent 角色 (按需读)
- `.skills/` ← 10 份方法论 (按需读)
- `config.yaml` ← workspace 配置
- `.git/` ← 实验版本控制 (commit in keep)

## 2. 操作循环 — 10 个 Agent 协同 (LOOP FOREVER)

> ⚠️ **核心流程**,每轮 6 步,触发停止条件 (§5) 才退。

### 调度图

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

### 各步契约

| Step | 调度方 | 输入 | 输出 / 副作用 |
|------|--------|------|---------------|
| 1 读状态 | Main Agent | `strategy.py`, `results.tsv` | 当前因子池 + 最近 run 摘要 |
| 2 决策 | spawn **Researcher** | 当前状态 + 上轮 Anti-overfit 反馈 | JSON `{action, hypothesis, avoid_actions}` |
| 3 执行 | spawn **Data Quality** + **Factor Analyst** + **Strategist** + **Portfolio Construction** | Step 2 action | 新 `strategy.py` + 回测 stdout |
| 4 保存 | [框架自动] | 回测 stdout | `runs/run_XXXX/...` + DuckDB |
| 5 评估 | spawn **Risk Controller** + **Attribution Analyst** + **Anti-overfit Analyst** | metrics + 当前策略 | JSON `{verdict, risk_rating, suggestions}` |
| 6 提交 | Main Agent | verdict | keep → git commit; discard → 仅记录 |

Subagent JSON schema 详情 → `.prompts/{orchestrator,data_quality,researcher,factor_analyst,strategist,portfolio_construction,risk_controller,attribution_analyst,anti_overfit_analyst,backtest_diagnostics}.md`
算子语法 / 风控阈值 / 抗过拟合 → `.skills/*.md` (按文件名取用)

### 通信协议

- **中介文件**: `strategy.py` / `results.tsv` / `runs/run_XXXX/` 是 10 Agent 通信的唯一通道
- **JSON 协议**: spawn 时把任务序列化进 .prompts/ schema, 完成后 Subagent 写盘 + stdout 返回 JSON
- **同步**: Subagent 阻塞完成, Main Agent 拿到 JSON 再进下一步
- **不必 spawn**: 读文件 + 推理类简单决策 Main Agent 自己来

## 3. spawn 谁

| 场景 | 角色 | 提示词 |
|------|------|--------|
| 调度 6 步循环 | Orchestrator | `.prompts/orchestrator.md` |
| 数据质量检查 | Data Quality | `.prompts/data_quality.md` |
| 实验规划 (因子池评估 + 决策) | Researcher | `.prompts/researcher.md` |
| 因子发现 / 验证 (2 条路径: 本地/Alpha Zoo) | Factor Analyst | `.prompts/factor_analyst.md` |
| 因子集成 / 参数优化 / 移除 | Strategist | `.prompts/strategist.md` |
| 组合构建 (风险平价/协方差) | Portfolio Construction | `.prompts/portfolio_construction.md` |
| 风控检查 (VaR/CVaR/压力测试) | Risk Controller | `.prompts/risk_controller.md` |
| 归因分析 (Brinson/Fama-French) | Attribution Analyst | `.prompts/attribution_analyst.md` |
| 抗过拟合 (6 法 + keep/discard) | Anti-overfit Analyst | `.prompts/anti_overfit_analyst.md` |
| 回测诊断 (错误分类 + 修复) | Backtest Diagnostics | `.prompts/backtest_diagnostics.md` |

普通决策 (读文件 + 推理) → Main Agent 直接做,不必 spawn。

## 4. 复现任意历史 run

```bash
cp strategies/<name>/runs/<run_XXX>/strategy.py strategies/<name>/strategy.py
cd strategies/<name> && python strategy.py
```

## 5. 停止条件

### 正常停止 (ALL 条件同时满足)

| 条件 | 阈值 |
|------|------|
| Calmar | ≥ 0.5 |
| Sharpe | ≥ 0.3 |
| MaxDD | ≤ -15% |
| 抗过拟合 | 6/6 全 pass |
| 因子池覆盖 | ≥ 80% (5/6 维) |
| 连续无改善轮数 | ≥ 10 轮 |
| 总轮数 | ≤ 99999 轮 (永远不停) |

### 紧急停止 (任一命中)

| 条件 | 说明 |
|------|------|
| 总轮数 = 99999 | 硬上限 (实际永不触发) |
| 用户 Ctrl+C | 手动中断 |
| 所有 Agent 都被 interrupt 过且无改善 | 全部卡死 |
| DuckDB 写满 / 磁盘满 | 基础设施故障 |

**不要主动退出**,持续运行,用户在睡觉。

## 6. 速度控制

每轮之间需要控制节奏,不能太快也不能太慢:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `round_cooldown` | 30s | 两轮之间最少间隔 (秒) |
| `analysis_timeout` | 120s | 单个 Agent 分析超时 (秒) |
| `stuck_threshold` | 3 | 连续相同输出次数判定卡住 |

**节奏规则**:
- 改善中: 正常速度 (cooldown 秒)
- 连续 3 轮无改善: 减速 (cooldown × 2)
- 连续 5 轮无改善: 再减速 (cooldown × 4)
- 卡住检测到: interrupt + 重启 Agent

**不要急**: 每轮认真分析比快速迭代更重要。

## 7. 第一步

```bash
cat strategies/<name>/program.md
cat strategies/<name>/strategy.py
tail -5 strategies/<name>/runs/results.tsv
ls strategies/<name>/runs/ | tail -3
```

读完上面 4 行后,进入 §2 循环。
