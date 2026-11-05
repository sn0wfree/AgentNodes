---
name: thesis-tracker
category: tool
description: 论点追踪 — 把策略背后的"研究假设"显式记录, 跟踪证据 / 反证 / 状态, 支持 hypothesis lifecycle
tags: [thesis, hypothesis, evidence, lifecycle, audit]
---

# Thesis Tracker

策略 alpha 来源本质是一个**假设**: "X 现象导致 Y 收益"。本 skill 强制把假设显式记录, 并跟踪证据链。

## 假设 vs 策略

| 维度 | 假设 (thesis) | 策略 (strategy) |
|------|--------------|----------------|
| 内容 | "小盘股长期跑赢大盘" | `factor = -log(market_cap)` |
| 验证方式 | 学术研究 + 历史数据 | 回测 + 风控 |
| 失效模式 | 制度变化/规模效应反转 | 代码 bug/数据错误 |
| 更新频率 | 年级 | 周/月 |

**核心**: 没有 thesis 的策略是"裸奔", 任何回撤都无所适从。

## 假设生命周期

```
DRAFT → PROPOSED → TESTING → VALIDATED / REJECTED → MONITORING → RETIRED
```

| 状态 | 含义 | 行动 |
|------|------|------|
| DRAFT | 灵感/直觉 | 收集证据 |
| PROPOSED | 写成可证伪命题 | 设计测试 |
| TESTING | 回测/外样本 | 验证 |
| VALIDATED | 3+ 年 OOS 稳定 | 上线 |
| REJECTED | 证据反证 | 归档 |
| MONITORING | 衰减信号出现 | 准备下线 |
| RETIRED | 失效 | 删除 |

## 假设文档结构

```yaml
# hypothesis.yaml
thesis_id: hyp_abc123
title: 小盘股长期跑赢大盘
statement: |
  在 A 股市场, 流通市值 < 50 亿的股票组合年化跑赢
  沪深 300 约 3-5%, 主因是流动性溢价 + 成长溢价。
universe: A 股全市场
signal: log_rank(market_cap) (越小越好)
timeframe: 月频调仓, 持有 12 个月
evidence:
  - type: paper
    ref: Fama & French 1993
    quote: "..."
  - type: backtest
    ref: runs/run_0042
    metrics: {sharpe: 0.85, calmar: 0.62}
invalidation:
  - 若 24 个月滚动 sharpe < 0.3
  - 若 max_drawdown < -30%
  - 若市场制度重大变化 (如注册制)
status: VALIDATED
created_at: 2025-01-15
updated_at: 2025-09-20
```

## 与现有系统集成

| 系统 | 关联方式 |
|------|---------|
| Goal | thesis 是 goal 的子节点 |
| Hypothesis | thesis 的代码化表示 |
| Run | thesis 验证的实验 |
| Evidence | 论文/数据/回测片段 |
| Audit | thesis 状态变更的审计日志 |

## 验证流程

### Step 1: 写下 PROPOSED 假设
```bash
quantnodes-research hypothesis create \
  --title "小盘股长期跑赢大盘" \
  --statement "..." \
  --universe "A 股" \
  --signal "..." \
  --timeframe "月频"
```

### Step 2: 设计测试
- OOS 样本期: 至少 3 年 (最近)
- 对照组: 多空对冲、纯多头、市值中性
- 多市场验证: A 股/港股/美股

### Step 3: 执行回测, 收 evidence
```bash
quantnodes-research run <ws> --strategy small_cap_value
# 收集 metrics.json, save evidence
quantnodes-research hypothesis link <hyp_id> --run-id run_0042
```

### Step 4: 状态变更
```bash
quantnodes-research hypothesis update <hyp_id> --status VALIDATED \
  --evidence "3年 OOS sharpe 0.85"
```

### Step 5: 持续监控
- 月度: 滚动 sharpe 检查
- 季度: 重测 OOS
- 触发 invalidation → 自动转 MONITORING

## Evidence 类型

| 类型 | 示例 | 权重 |
|------|------|------|
| 学术论文 | Fama-French 1993 | 高 |
| 实盘数据 | 5 年实盘 sharpe | 最高 |
| 历史回测 | 10 年 OOS | 高 |
| 跨市场验证 | A/H/US 同向 | 中 |
| 理论推导 | 微观结构理论 | 中 |
| 经验法则 | 行业惯例 | 低 |

## 常见陷阱

1. **过度自信**: 一次回测 sharpe 1.5 就说 VALIDATED → 至少 3 年 OOS
2. **忽略样本期**: 2007-2024 含 2 轮牛市, 不能外推到 2025
3. **多重检验**: 测试 50 个假设, 必有 1-2 个 sharpe > 1
4. **逆向合理化**: 假设错了不承认, 改用"季节性调整"
5. **缺乏 invalidation 规则**: 没有触发条件 = 永远 VALIDATED = 没意义

## 团队协作

- 1 个 thesis 1 个 owner
- 月度 thesis review meeting
- 跨人 audit: 别人挑战你的 VALIDATED
- 公开 thesis 库: 避免重复劳动

## 输出

```json
{
  "thesis_id": "hyp_abc123",
  "status": "VALIDATED",
  "evidence_count": 7,
  "oos_years": 5,
  "sharpe_oos": 0.85,
  "last_review": "2025-09-20",
  "invalidation_triggers": 3,
  "next_review_due": "2025-12-20"
}
```