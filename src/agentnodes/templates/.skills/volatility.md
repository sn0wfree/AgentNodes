---
name: volatility
category: analysis
description: 波动率分析 — 已实现波动 / GARCH / 隐含波动率 / 波动率择时
tags: [volatility, garch, realized-vol, implied-vol, regime]
---

# Volatility Analysis

波动率是**唯一**可观测的市场状态变量。本 skill 教 LLM 如何用波动率做风险预算、择时、因子增强。

## 波动率度量

### 已实现波动 (Realized Vol)
```python
import pandas as pd
import numpy as np

# 简单实现
returns = close.pct_change()
realized_vol_20d = returns.rolling(20).std() * np.sqrt(252)

# Parkinson (用 high-low)
parkinson = np.sqrt(
    (np.log(high / low) ** 2).rolling(20).mean() / (4 * np.log(2))
) * np.sqrt(252)

# Garman-Klass (OHLC)
gk = np.sqrt(
    0.5 * (np.log(high/low) ** 2)
    - (2*np.log(2) - 1) * (np.log(close/open) ** 2)
).rolling(20).mean() * np.sqrt(252)
```

### GARCH (前向预测)
```python
from arch import arch_model
returns = close.pct_change().dropna() * 100  # GARCH 用百分比
am = arch_model(returns, vol='Garch', p=1, q=1)
res = am.fit(disp='off')
forecast_vol = res.forecast(horizon=5).variance ** 0.5
```

### 隐含波动率 (Implied Vol)
- 数据源: 期权 chain (50ETF 期权 / 300ETF 期权 / 个股期权)
- 计算: BS 模型反解 sigma
- 应用: 隐含/已实现比 = 方差风险溢价

## 波动率特征

| 现象 | 含义 | 应用 |
|------|------|------|
| 波动率聚集 (clustering) | 大波动后跟大波动 | GARCH/EGARCH |
| 均值回归 | 高波动后会回落 | 波动率做空 |
| 杠杆效应 | 负收益→波动率上升 | GJR-GARCH |
| 期限结构 | IV 远月 > 近月 | 跨期套利 |

## 策略应用

### 1. 波动率择时
```python
# 低波动期 → 加大仓位
# 高波动期 → 减仓或对冲
vol_zscore = (realized_vol - realized_vol.rolling(252).mean()) / realized_vol.rolling(252).std()
target_exposure = clip(1.0 - 0.5 * vol_zscore, 0.3, 1.5)
```

### 2. 波动率因子
```python
# 低波动异象: 低波动股票长期跑赢
# 但在牛市中段会跑输 (beta < 1)
low_vol_factor = -realized_vol_60d  # 截面 rank
```

### 3. 波动率风险平价
```python
# 仓位 ∝ 1 / vol
target_weight = inverse_vol_weights(returns, lookback=60)
```

### 4. 跨资产波动率套利
- 隐含 > 已实现 + 0.05 → 卖期权
- 隐含 < 已实现 - 0.05 → 买期权

## 风险指标

| 指标 | 公式 | 健康区间 |
|------|------|---------|
| 年化波动率 | std(returns) * sqrt(252) | 5%-30% |
| 偏度 (skew) | E[(r-μ)^3] / σ^3 | > -0.5 |
| 峰度 (kurtosis) | E[(r-μ)^4] / σ^4 | 3-15 (正常) |
| VaR (95%) | -quantile(returns, 0.05) | < 3% 日 |
| CVaR (95%) | -mean(returns[returns < VaR]) | < 4% 日 |
| 最大回撤 | max peak-to-trough | < -20% |

## GARCH 模型选型

| 模型 | 适用 | 参数 |
|------|------|------|
| GARCH(1,1) | 标配 | p=1, q=1 |
| EGARCH(1,1) | 杠杆效应 | o=1 |
| GJR-GARCH(1,1) | 不对称波动 | o=1 |
| HARCH | 长记忆 | power=2 |

## 实施陷阱

1. **年化错误**: 日频用 sqrt(252), 分钟用 sqrt(periods_per_year)
2. **GARCH 不平稳**: 数据需 return, 不能用 price
3. **IV 数据缺失**: A 股个股期权覆盖少, 多用 50ETF 代替
4. **波动率突变**: 黑天鹅导致所有模型失效, 需断路器
5. **样本量**: GARCH 至少 500 个观测, 否则不稳

## 输出 JSON

```json
{
  "vol_method": "realized_20d_parkinson",
  "current_vol_annualized": 0.18,
  "vol_regime": "normal",
  "vol_zscore_252d": 0.42,
  "garch_1_1_forecast_5d": 0.16,
  "var_95_daily": -0.021,
  "cvar_95_daily": -0.028,
  "max_drawdown_252d": -0.12,
  "skew_252d": -0.34,
  "kurtosis_252d": 4.8
}
```