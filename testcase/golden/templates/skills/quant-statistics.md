---
name: quant-statistics
category: analysis
description: 量化统计方法 — ADF 单位根 + Hurst 指数 + Newey-West + Bootstrap
tags: [statistics, adf, hurst, bootstrap, newey-west]
---

# Quantitative Statistical Methods

借鉴自 vibe-trading quant-statistics skill，适配 strategy-research 框架。

## ADF 单位根检验

```python
from statsmodels.tsa.stattools import adfuller

def adf_test(series, significance=0.05):
    result = adfuller(series.dropna(), autolag='AIC')
    return {
        'adf_statistic': result[0],
        'p_value': result[1],
        'is_stationary': result[1] < significance,
        'critical_values': result[4],
    }
```

### 决策规则
| p-value | 结论 | 操作 |
|---------|------|------|
| < 0.01 | 强平稳 | 可直接用于回归/建模 |
| 0.01-0.05 | 平稳 | 可用 |
| 0.05-0.10 | 弱证据 | 差分后重测 |
| > 0.10 | 非平稳 | 必须差分或协整处理 |

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

### 半衰期参考
| 半衰期 | 含义 | 交易指导 |
|--------|------|----------|
| < 5 天 | 极快回复 | 日内或隔夜交易 |
| 5-20 天 | 快速回复 | 配对交易理想范围 |
| 20-60 天 | 中速回复 | 中期持仓 |
| 60-180 天 | 慢速回复 | 长持仓期 |
| > 180 天 | 近随机游走 | 配对交易风险高 |

## Granger 因果检验

```python
from statsmodels.tsa.stattools import grangercausalitytests

def granger_test(data, x_col, y_col, max_lag=5):
    results = grangercausalitytests(data[[y_col, x_col]].dropna(), maxlag=max_lag)
    return {lag: results[lag][0]['ssr_ftest'][1] for lag in range(1, max_lag+1)}
```

## GARCH 波动率建模

```python
from arch import arch_model

def fit_garch(returns):
    model = arch_model(returns * 100, vol='Garch', p=1, q=1,
                       mean='Constant', dist='normal')
    result = model.fit(disp='off')
    return {
        'omega': result.params['omega'],
        'alpha': result.params['alpha[1]'],
        'beta': result.params['beta[1]'],
        'persistence': result.params['alpha[1]'] + result.params['beta[1]'],
        'long_run_vol': np.sqrt(result.params['omega'] /
                        (1 - result.params['alpha[1]'] - result.params['beta[1]'])) / 100,
    }
```

### GARCH 变体
| 模型 | 特点 | 适用场景 |
|------|------|----------|
| GARCH(1,1) | 基线，对称冲击响应 | 默认选择 |
| EGARCH | 非对称（杠杆效应） | 下跌波动 > 上涨波动 |
| GJR-GARCH | 另一种非对称形式 | 同 EGARCH |
| FIGARCH | 长记忆 | 波动率聚集持续很久 |

## Bootstrap 方法

```python
def bootstrap_sharpe(returns, n_bootstrap=10000):
    def sharpe(r):
        return r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    n = len(returns)
    bootstrap_stats = np.array([
        sharpe(np.random.choice(returns, size=n, replace=True))
        for _ in range(n_bootstrap)
    ])
    return {
        'point_estimate': sharpe(returns.values),
        'ci_lower': np.percentile(bootstrap_stats, 2.5),
        'ci_upper': np.percentile(bootstrap_stats, 97.5),
        'is_significant': np.percentile(bootstrap_stats, 2.5) > 0,
    }
```

## 多重检验校正

```python
from statsmodels.stats.multitest import multipletests

# 控制 FDR (推荐)
reject, p_adj, _, _ = multipletests(p_values, method='fdr_bh')
```

## 回测统计显著性

```
Sharpe 显著性检验:
H0: Sharpe = 0 (策略无效)
H1: Sharpe > 0

t = Sharpe × sqrt(n) / sqrt(1 + 0.5×Sharpe²)
其中 n = 观察期数 (年)

经验法则:
- Sharpe > 0.5 且回测 >5 年 → 可能显著
- Sharpe > 1.0 且回测 >3 年 → 很可能显著
- Sharpe > 2.0 → 过拟合警告
```

## 回归诊断清单

```
□ 1. 线性性: 残差 vs 拟合值无明显模式
□ 2. 正态性: 残差 QQ 图接近直线, Jarque-Bera p>0.05
□ 3. 异方差性: White/BP test p>0.05, 或使用 HAC 标准误
□ 4. 自相关: DW≈2, Ljung-Box p>0.05
□ 5. 多重共线性: VIF<5
□ 6. 异常值: Cook's D < 4/n
```
