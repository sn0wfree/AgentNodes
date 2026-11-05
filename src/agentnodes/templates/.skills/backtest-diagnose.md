---
name: backtest-diagnose
category: analysis
description: 回测诊断工作流 — 从 metrics.json / run.log / strategy.py 识别失败原因并修复
tags: [backtest, diagnose, debug, metrics, log]
---

# Backtest Diagnosis

借鉴自 vibe-trading backtest-diagnose skill，适配 strategy-research 框架。

## 诊断工作流

1. **读取产物**: 检查 metrics.json, run.log, strategy.py
2. **读取代码**: 检查策略逻辑
3. **分类问题**: 使用错误分类法确定根因
4. **应用修复**: 修改代码，重新运行
5. **验证修复**: 检查新的指标

## 错误分类

### 运行时错误 (exit_code != 0)

| 错误类型 | 常见原因 | 修复 |
|----------|----------|------|
| ImportError | 缺少依赖 | `pip install xxx` |
| KeyError | DataFrame 列名不匹配 | 检查实际列名 |
| IndexError | 空数据或长度不足 | 添加长度检查 |
| TypeError | 信号类型错误 | 确保返回值是 `pd.Series` |

### 逻辑 Bug (回测成功但结果异常)

1. **零交易** (`trade_count=0`): 信号逻辑 Bug，条件太严格
2. **交易过晚** (首笔交易在回测开始 2 年后): 数据过滤 Bug
3. **资金利用率 <50%** (大部分时间空仓): 仓位管理 Bug
4. **期末持仓** (回测结束时仍有持仓): 出场时机 Bug

### 数据错误

| 症状 | 根因 | 修复 |
|------|------|------|
| 无数据 | API token 无效或代码问题 | 检查配置 |
| 数据太少 | 日期范围太窄 | 扩大日期范围 |

### 数据源错误忽略列表

遇到以下关键词时**不要修改代码**，问题在数据提供方:
- "no data available"
- "rate limit"
- "API limit"
- "daily limit"
- "Information" (常见于 Tushare API 响应)

## 硬门检查清单

1. metrics.json 存在且非空
2. run.log 中有指标输出
3. `trade_count > 0` (0 交易 = 信号 Bug)
4. 权益序列无 `NaN`
5. `exit_code == 0`

## 修复原则

- 使用精确代码修复而非重写整个文件
- 仅修复 Bug，不改变策略逻辑 (除非用户明确要求)
- 每次只修一个问题，修复后立即重跑
- 最多 3 轮修复迭代

## 修复后验证

1. **AST 语法通过**: `python -c "import ast; ast.parse(open('strategy.py').read()); print('OK')"`
2. **包含因子表达式**: 检查 FACTOR_EXPRS 是否合理
3. **重跑回测**: 修复后重新运行并验证结果

## action_items 格式

```json
{
  "verdict": "keep | discard",
  "analysis": "分析原因",
  "risk_rating": "Green | Yellow | Red",
  "direction": "exploit | explore | diversify",
  "suggestions": ["建议1", "建议2"],
  "risk_warnings": ["警告1"]
}
```
