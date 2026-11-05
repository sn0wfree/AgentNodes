---
name: correlation-analysis
category: analysis
description: 相关性与协整分析 — Pearson/Spearman 相关 + Engle-Granger/Johansen 协整检验
tags: [correlation, cointegration, pairs, statistics]
---

# Correlation and Cointegration Analysis

借鉴自 vibe-trading correlation-analysis skill，适配 strategy-research 框架。

## 协整检验

### Engle-Granger 两步法
```python
from statsmodels.tsa.stattools import coint
import statsmodels.api as sm

def engle_granger_coint(y, x, significance=0.05):
    x_const = sm.add_constant(x)
    ols = sm.OLS(y, x_const).fit()
    hedge_ratio = ols.params[x.name]
    residuals = ols.resid
    coint_stat, coint_p, crit_vals = coint(y, x)
    return {
        'is_cointegrated': coint_p < significance,
        'coint_p': coint_p,
        'hedge_ratio': hedge_ratio,
        'spread': residuals,
    }
```

### 半衰期计算
```python
def compute_half_life(spread):
    spread_lag = spread.shift(1)
    delta = spread.diff()
    model = sm.OLS(delta.dropna(), sm.add_constant(spread_lag.dropna())).fit()
    lam = model.params[1]
    if lam >= 0:
        return float("inf")
    return -np.log(2) / lam
```

## Kalman Filter 动态对冲比

```python
import numpy as np

def kalman_hedge_ratio(y, x, delta=1e-4, vt=1.0):
    n = len(y)
    Wt = delta / (1 - delta) * np.eye(2)
    theta = np.zeros((n, 2))
    P = np.zeros((n, 2, 2))
    P[0] = np.eye(2)
    spread = np.zeros(n)
    spread[0] = float("nan")

    for t in range(1, n):
        F = np.array([x.iloc[t], 1.0])
        theta_pred = theta[t - 1]
        P_pred = P[t - 1] + Wt
        innovation = y.iloc[t] - F @ theta_pred
        S = F @ P_pred @ F.T + vt
        K = P_pred @ F.T / S
        theta[t] = theta_pred + K * innovation
        P[t] = (np.eye(2) - np.outer(K, F)) @ P_pred
        spread[t] = y.iloc[t] - theta[t, 0] * x.iloc[t] - theta[t, 1]

    return pd.DataFrame({
        "hedge_ratio": theta[:, 0],
        "intercept": theta[:, 1],
        "spread": spread,
    }, index=y.index)
```

## 配对交易信号

```python
def generate_pair_signals(y_price, x_price, lookback=60,
                          entry_z=2.0, exit_z=0.5, stop_z=3.5,
                          use_kalman=False):
    if use_kalman:
        kf = kalman_hedge_ratio(y_price, x_price)
        spread = kf["spread"]
    else:
        hedge_ratio = abs(np.polyfit(x_price, y_price, 1)[0])
        spread = np.log(y_price) - hedge_ratio * np.log(x_price)

    spread_mean = spread.rolling(lookback).mean()
    spread_std = spread.rolling(lookback).std()
    z_score = (spread - spread_mean) / spread_std

    signal_y = pd.Series(0.0, index=y_price.index)
    signal_x = pd.Series(0.0, index=x_price.index)
    position = 0

    for i in range(lookback, len(z_score)):
        z = z_score.iloc[i]
        if np.isnan(z):
            continue
        if position == 0:
            if z < -entry_z:
                position = 1
            elif z > entry_z:
                position = -1
        elif position == 1:
            if z > -exit_z or z > stop_z:
                position = 0
        elif position == -1:
            if z < exit_z or z < -stop_z:
                position = 0
        signal_y.iloc[i] = 0.5 * position
        signal_x.iloc[i] = -0.5 * position

    return pd.DataFrame({
        "spread": spread, "z_score": z_score,
        "signal_y": signal_y, "signal_x": signal_x,
        "position": signal_y * 2,
    })
```

## Z-Score 阈值配置

| 参数 | 保守 | 标准 | 激进 |
|------|------|------|------|
| entry_z | 2.5 | 2.0 | 1.5 |
| exit_z | 0.3 | 0.5 | 0.8 |
| stop_z | 3.0 | 3.5 | 4.0 |
| lookback | 90 | 60 | 30 |

## 相关性矩阵参考 (A 股)

| | 沪深 300 | 中证 500 | 国债 | 黄金 | BTC |
|--|----------|----------|------|------|-----|
| 沪深 300 | 1.00 | 0.85 | -0.15 | 0.05 | 0.10 |
| 中证 500 | 0.85 | 1.00 | -0.10 | 0.03 | 0.12 |
| 国债 | -0.15 | -0.10 | 1.00 | 0.20 | -0.05 |
| 黄金 | 0.05 | 0.03 | 0.20 | 1.00 | 0.15 |
| BTC | 0.10 | 0.12 | -0.05 | 0.15 | 1.00 |
