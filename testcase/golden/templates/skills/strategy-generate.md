---
name: strategy-generate
category: strategy
description: 策略生成框架 — PARAMS / FACTOR_EXPRS / FACTOR_WEIGHT_METHOD 编辑清单 + AST guard 边界
tags: [strategy, generate, params, factor-exprs, ast-guard]
---

# Strategy Generate Framework

本 skill 教 LLM agent 如何**安全地**生成和修改 `strategy.py`。在 agent 修改任何策略文件前, 必须熟悉 AST guard 边界。

## strategy.py 结构

```python
PARAMS = {
    "top_n": 10,                # 持仓数
    "max_weight": 0.25,         # 单资产最大权重
    "rebalance_freq": 20,       # 调仓频率(交易日)
}

FACTOR_EXPRS = [
    {
        "factor_name": "momentum_20",
        "factor_code": "ts_mean(close, 20) / ts_mean(close, 60) - 1",
        "weight": 1.0,
    },
]

FACTOR_WEIGHT_METHOD = "equal"   # "equal" | "inv_vol"
```

## 可编辑字段

| 字段 | 类型 | 范围 | 风险 |
|------|------|------|------|
| `top_n` | int | 5-50 | 太小集中, 太大稀释 |
| `max_weight` | float | 0.05-0.40 | 太小分散, 太大集中 |
| `rebalance_freq` | int | 5-60 | 太短高换手, 太长失活 |
| `FACTOR_EXPRS` | list | 1-8 项 | 太多易过拟合 |
| `factor_code` | str | 见下 | 必须能 parse |

## 可用算子

### 价格类
- `ts_mean(close, N)`, `ts_std(close, N)`
- `ts_return(close, N)` = close/close.shift(N) - 1
- `ts_rank(close, N)`, `ts_zscore(close, N)`

### 成交量类
- `ts_mean(volume, N)`, `volume_ratio(close, volume)`
- `OBV(close, volume)`, `VWAP(close, high, low, volume)`

### 价量结合
- `ts_corr(close, volume, N)`
- `turnover_rate(close, volume, share_outstanding)`

### 横截面
- `rank(factor)`, `scale(factor)` (归一化到 [-1, 1])
- `demean(factor)` (减均值)

### 复合
- `where(condition, a, b)`, `abs(x)`, `sign(x)`
- `delay(x, N)`, `delta(x, N)`, `correlation(a, b, N)`

## AST guard 拦截

`WriteFileTool` 对 `.py` 文件执行 AST 校验, **禁止**:

1. `import os, sys, subprocess, shutil, requests, urllib` (I/O 逃逸)
2. `open(...)` (任意文件读写)
3. `exec(...)` / `eval(...)` (代码注入)
4. `__import__(...)` (动态导入)
5. 类定义 / 函数定义 / lambda (代码复杂度)
6. 顶层语句数 > 50 (恶意代码体积)
7. 文件长度 > 20KB

**遇到拦截怎么办**:
- 把因子逻辑用现有算子表达
- 不要试图绕过 AST guard

## 生成新策略的工作流

### Step 1: 决定方向
- 趋势 / 反转 / 截面 / 时序 / 资金流 / 多因子?

### Step 2: 因子设计
```python
# 例: 短期反转 + 长期动量 + 波动率过滤
FACTOR_EXPRS = [
    {
        "factor_name": "reversal_5d",
        "factor_code": "-ts_return(close, 5)",
        "weight": 0.4,
    },
    {
        "factor_name": "momentum_60d",
        "factor_code": "ts_return(close, 60)",
        "weight": 0.4,
    },
    {
        "factor_name": "low_vol_filter",
        "factor_code": "-ts_std(returns, 20)",
        "weight": 0.2,
    },
]
```

### Step 3: 写 strategy.py
- 用 `write_file` 工具 (沙箱内)
- 路径: `<workspace>/strategies/<strategy_name>/strategy.py`

### Step 4: 回测验证
```bash
quantnodes-research run <workspace> --strategy <strategy_name>
```

### Step 5: 解读结果
- 必查: calmar > 0.5, sharpe > 0.3, max_dd < -15%, trades > 30
- 验收: 调 `accept --metrics-file runs/run_XXXX/metrics.json`

## 最佳实践

1. **单一职责**: 一个因子做一件事, 不要 5 个条件套娃
2. **可解释性**: 因子名称反映其含义
3. **稳定性优先**: IR > IC 极值, 抗换手
4. **小步迭代**: 每次只改一个变量, 观察变化
5. **记录假设**: 在 `program.md` 写明设计理由
6. **诚实失败**: 因子失效时不要硬调, 删掉换下一个

## 反面案例

```python
# ❌ 复杂嵌套
"factor_code": "where(ts_zscore(ts_mean(close,5),20)>2 && rank(ts_std(close,10))<0.3, ts_return(close,20)*-1, 0)"

# ✅ 简单清晰
"factor_code": "-ts_return(close, 20)",
```

## 输出

生成结束输出 JSON 摘要:

```json
{
  "strategy_name": "momentum_reversal_blend",
  "n_factors": 3,
  "weight_method": "inv_vol",
  "expected_calmar": 0.8,
  "expected_turnover": 6.0,
  "design_notes": "短期反转 (5d) + 长期动量 (60d) + 低波动过滤"
}
```