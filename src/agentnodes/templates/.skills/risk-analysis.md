---
name: risk-analysis
category: analysis
description: 风险度量与压力测试 — VaR/CVaR + 压力测试 + 风险预算 + 集中度
tags: [risk, var, cvar, stress-test, drawdown]
---

# Risk Measurement and Stress Testing

借鉴自 vibe-trading risk-analysis skill，适配 strategy-research 框架。

## VaR (Value at Risk)

### 历史模拟法
```python
def historical_var(returns, confidence=0.95, horizon=1):
    sorted_returns = returns.sort_values()
    index = int((1 - confidence) * len(sorted_returns))
    var_1d = -sorted_returns.iloc[index]
    return var_1d * np.sqrt(horizon)
```

### 参数法 (正态)
```python
from scipy.stats import norm

def parametric_var(returns, confidence=0.95, horizon=1):
    mu = returns.mean()
    sigma = returns.std()
    z = norm.ppf(1 - confidence)
    var_1d = -(mu + z * sigma)
    return var_1d * np.sqrt(horizon)
```

## CVaR / ES (条件 VaR)

```python
def historical_cvar(returns, confidence=0.95):
    var = historical_var(returns, confidence)
    tail_losses = returns[returns < -var]
    return -tail_losses.mean() if len(tail_losses) > 0 else var
```

## 最大回撤分析

```python
def max_drawdown_analysis(equity):
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    max_dd = drawdown.min()
    trough_idx = drawdown.idxmin()
    peak_idx = equity[:trough_idx].idxmax()
    recovery = equity[trough_idx:][equity[trough_idx:] >= equity[peak_idx]]
    recovery_date = recovery.index[0] if len(recovery) > 0 else None
    return {
        'max_drawdown': max_dd,
        'peak_date': peak_idx,
        'trough_date': trough_idx,
        'recovery_date': recovery_date,
        'underwater_days': (trough_idx - peak_idx).days,
        'recovery_days': (recovery_date - trough_idx).days if recovery_date else None
    }
```

## Monte Carlo 模拟

### 几何布朗运动
```python
def monte_carlo_gbm(S0, mu, sigma, T=252, n_paths=10000):
    dt = 1 / 252
    Z = np.random.standard_normal((n_paths, T))
    log_returns = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * Z
    prices = S0 * np.exp(np.cumsum(log_returns, axis=1))
    return prices
```

## 压力测试场景

### 历史场景
| 场景 | 时期 | A 股回撤 | 美股回撤 | BTC 回撤 |
|------|------|----------|----------|----------|
| 2008 金融危机 | 2008.01-2008.10 | -65% | -50% | N/A |
| 2015 A 股崩盘 | 2015.06-2015.08 | -45% | -10% | -20% |
| 2018 贸易战 | 2018.01-2018.12 | -25% | -20% | -80% |
| 2020 COVID | 2020.01-2020.03 | -15% | -35% | -50% |
| 2022 加息周期 | 2022.01-2022.10 | -20% | -25% | -65% |

### 假设场景
```python
STRESS_SCENARIOS = {
    'rate_shock_up_100bp': {'equity': -0.10, 'bond_10y': -0.08, 'gold': +0.05, 'btc': -0.15},
    'credit_crisis': {'equity': -0.25, 'bond_10y': +0.05, 'gold': +0.10, 'btc': -0.30},
    'liquidity_dry_up': {'equity': -0.20, 'bond_10y': -0.05, 'gold': -0.05, 'btc': -0.40},
    'geopolitical_conflict': {'equity': -0.15, 'bond_10y': +0.03, 'gold': +0.15, 'oil': +0.30},
}
```

## 尾部风险 (EVT)

```python
from scipy.stats import genpareto

def fit_gpd_tail(returns, threshold_pct=5.0):
    threshold = np.percentile(returns, threshold_pct)
    exceedances = threshold - returns[returns < threshold]
    shape, loc, scale = genpareto.fit(exceedances)
    return {
        'shape_xi': shape,  # >0 肥尾, =0 指数尾, <0 有界尾
        'tail_type': 'fat tail (dangerous)' if shape > 0 else 'thin tail (safer)',
    }
```

## 尾部风险指标

| 指标 | 计算 | 含义 |
|------|------|------|
| 峰度 | `returns.kurtosis()` | >3 表示肥尾；A 股通常在 4-8 |
| 偏度 | `returns.skew()` | <0 左偏（大跌比大涨更常见） |
| 尾部比率 | 最差 5% / 最好 5% | >1 表示下行风险更大 |
