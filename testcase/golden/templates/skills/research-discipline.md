---
name: research-discipline
category: tool
description: 研究纪律 — 单变量隔离 + 前视偏差 + 数据窥探 + 多重检验
tags: [discipline, look-ahead, data-snooping, multiple-testing]
---

# Research Discipline

研究纪律 — 偏见自检清单。

## 5 种研究偏见

每次开始研究前,必须自检以下 5 种偏见:

### 1. 龙头偏见 (Leader Bias)

- **问题**: 是否只关注大盘股?
- **检查**: 搜索范围是否包含中小盘?
- **修正**: 刻意搜索中小盘标的

### 2. 英文偏见 (English Bias)

- **问题**: 是否遗漏非英文市场?
- **检查**: 数据源是否覆盖 A 股?
- **修正**: 补充 A 股本土来源 (tushare/akshare/tencent)

### 3. 叙事偏见 (Narrative Bias)

- **问题**: 是否被概念标签误导?
- **检查**: 因子是否基于实际业务和财务?
- **修正**: 看实际业务数据,不看概念

### 4. 确认偏见 (Confirmation Bias)

- **问题**: 是否只找支持证据?
- **检查**: 是否搜索了反对观点?
- **修正**: 强制搜索反对观点

### 5. 近因偏见 (Recency Bias)

- **问题**: 是否依赖过时数据?
- **检查**: 数据日期是否最新?
- **修正**: 检查数据日期,更新到最新

## 自检流程

每次研究前:

1. 列出当前假设
2. 对每个假设,检查 5 种偏见
3. 如果发现偏见,修正假设
4. 记录自检结果到 Researcher 输出 JSON

## 输出格式

```json
{
  "bias_check": {
    "leader_bias": "pass | fail",
    "english_bias": "pass | fail",
    "narrative_bias": "pass | fail",
    "confirmation_bias": "pass | fail",
    "recency_bias": "pass | fail"
  },
  "corrections": ["修正 1", "修正 2"]
}
```
