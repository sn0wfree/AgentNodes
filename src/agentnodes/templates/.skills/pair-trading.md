---
name: pair-trading
category: strategy
description: 配对交易 — 协整检验 + 价差 z-score + 半衰期 + 进场出场规则
tags: [pairs, cointegration, mean-reversion, z-score, half-life]
---

# Pair Trading

配对交易利用两个协整资产价差的均值回复。**前提**: 价差是平稳序列 (ADF p-value < 0.05), 且半衰期 < 60 个交易日。

## 流程

### Step 1: 配对候选筛选

| 筛选维度 | 标准 |
|---------|------|
| 同行业 | 必需 (白酒 vs 白酒, 不混银行) |
| 市值相近 | ±30% |
| 流动性 | 日均成交额 > 1 亿 |
| 历史协整 | 滚动 250 日 ADF p < 0.1 |

### Step 2: 协整检验

```python
from statsmodels.tsa.stattools import coint
score, pvalue, crit = coint(price_a, price_b)
# pvalue < 0.05 → 协整
# 滚动测试: 至少 70% 的 250 日窗口通过
```

### Step 3: 价差建模

**价差公式**:
```
spread = price_a - hedge_ratio * price_b
```

**对冲比 hedge_ratio**:
- OLS: `price_a = α + β * price_b + ε`, β 即 hedge_ratio
- Kalman filter (动态): 用 `pykalman` 滚动估计

### Step 4: 半衰期

```python
from numpy import log
delta_spread = spread.diff().dropna()
lag_spread = spread.shift(1).dropna()
beta = numpy.polyfit(lag_spread, delta_spread, 1)[0]
half_life = -log(2) / beta
# half_life < 60 个 bar
```

### Step 5: 信号生成

```python
z_score = (spread - spread.rolling(60).mean()) / spread.rolling(60).std()
# 进场: z > +2.0 (做空价差) 或 z < -2.0 (做多价差)
# 出场: |z| < 0.5 或 |z| > 3.5 (止损)
# 仓位: 单边 5% 资金
```

### Step 6: 风控

- 单笔止损: 2 倍 entry z-score 对应的 P&L
- 最大持仓周期: 5 个半衰期
- 配对失效检测: 滚动 60 日 ADF p > 0.2 → 平仓

## 常见坑

1. **幸存者偏差**: 用现存股票回测, 忽略已退市
2. **样本期内协整 ≠ 样本期外协整**: 必须滚动测试
3. **对冲比漂移**: 用 Kalman 不用静态 OLS
4. **融资融券限制**: A 股融券难, 实际空头仓位受限
5. **流动性不对等**: 价格便宜的那只成交慢

## 验收指标

| 指标 | 健康阈值 |
|------|---------|
| 单配对年化 | > 8% (扣费前) |
| 最大回撤 | < 6% |
| 配对胜率 | > 60% |
| 平均持仓 | 半衰期 × 1.5 |
| IC (日) | > 0.02 |

## 输出 JSON

```json
{
  "pair": ["600519.SH", "000858.SZ"],
  "hedge_ratio": 1.42,
  "half_life_days": 28,
  "coint_pvalue": 0.012,
  "entry_z": 2.0,
  "exit_z": 0.5,
  "stop_z": 3.5,
  "annual_return": 0.11,
  "max_drawdown": -0.045,
  "win_rate": 0.64
}
```