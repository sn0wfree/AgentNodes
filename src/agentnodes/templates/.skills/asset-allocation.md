---
name: asset-allocation
category: strategy
description: 资产配置 — 风险平价 / Black-Litterman / 均值方差 / 战略战术再平衡
tags: [asset-allocation, risk-parity, black-litterman, mean-variance, rebalance]
---

# Asset Allocation

资产配置决定 80% 的组合波动。本 skill 覆盖经典方法及其在多资产/多策略的应用。

## 主流方法

| 方法 | 输入 | 优点 | 缺点 |
|------|------|------|------|
| 等权 (1/N) | 无 | 简单稳健 | 不区分风险 |
| 均值方差 (Markowitz) | μ, Σ | 理论最优 | μ 不稳, 极敏感 |
| 风险平价 (Risk Parity) | Σ | 平衡风险贡献 | 不考虑收益预期 |
| 最大分散化 | Σ | 改善 Sharpe | 同上 |
| Black-Litterman | Σ + 主观观点 | 融合人机判断 | 主观权重难定 |
| 最小方差 | Σ | 稳健 | 收益保守 |

## 实施步骤

### Step 1: 数据准备
```python
import pandas as pd
returns = pd.DataFrame({
    'equity': equity_returns,
    'bond': bond_returns,
    'commodity': commodity_returns,
    'gold': gold_returns,
})
# 至少 5 年日度数据
# 用最近 3 年做权重估计 (避免远古数据)
recent = returns.tail(252 * 3)
```

### Step 2: 协方差估计
```python
# 简单
cov = recent.cov() * 252

# Ledoit-Wolf 收缩 (推荐)
from sklearn.covariance import LedoitWolf
lw = LedoitWolf().fit(recent)
cov = lw.covariance_ * 252

# 风险平价依赖 cov, 不依赖 mean → 更稳
```

### Step 3: 风险平价
```python
from scipy.optimize import minimize

def risk_parity_objective(weights, cov):
    portfolio_vol = np.sqrt(weights @ cov @ weights)
    marginal_risk = cov @ weights / portfolio_vol
    risk_contrib = weights * marginal_risk
    target = portfolio_vol / len(weights)
    return np.sum((risk_contrib - target) ** 2)

n = cov.shape[0]
bounds = [(0.01, 0.5)] * n
constraints = [{"type": "eq", "fun": lambda w: sum(w) - 1}]
result = minimize(risk_parity_objective, x0=[1/n]*n,
                  args=(cov,), bounds=bounds, constraints=constraints)
weights = result.x
```

### Step 4: Black-Litterman
```python
# 后验期望 = τΣ⁻¹Pᵀ(τPΣPᵀ)⁻¹(view - P prior_mean) + prior_mean
# 其中:
#   τ: 缩放因子 (0.05 - 0.10)
#   P: 观点矩阵 (n_views × n_assets)
#   view: 主观观点向量
#   prior_mean: 先验 (可用市场隐含或历史均值)
```

### Step 5: 再平衡规则

| 类型 | 频率 | 阈值 | 备注 |
|------|------|------|------|
| 定期 | 季度/半年 | — | 简单 |
| 阈值 | 漂移 > 5% | 触发 | 节省交易 |
| 混合 | 季度 OR 阈值 | — | 推荐 |

## 多策略组合

当资产是多个策略而非多资产时:
- 协方差用策略日收益
- Risk Parity 等价于"等风险贡献"
- 加 Black-Litterman: 主观观点可以是"我对 X 策略未来 3 月的预期"

## 风控

| 指标 | 阈值 |
|------|------|
| 单资产最大权重 | 40% |
| 单资产最大风险贡献 | 35% |
| 组合波动率 | < 12% 年化 |
| 单月最大回撤 | < 5% |
| 再平衡滑点 | < 0.1% |

## 陷阱

1. **协方差估计误差**: 5 年日度数据下, 协方差估计误差 ~ 50%
2. **均值估计误差**: 历史均值对未来预测力极弱
3. **换手过高**: 月度再平衡 + 阈值触发, 实际换手 5-10 倍/年
4. **流动性**: 仓位限制看可交易资产, 非名义
5. **极端事件**: 2008/2020 危机时相关性 → 1, Risk Parity 失效

## 输出 JSON

```json
{
  "method": "risk_parity",
  "assets": ["equity_cn", "bond_cn", "gold", "commodity"],
  "weights": [0.42, 0.31, 0.15, 0.12],
  "risk_contrib": [0.25, 0.25, 0.25, 0.25],
  "portfolio_vol_annualized": 0.085,
  "expected_sharpe": 0.62,
  "rebalance_freq": "quarterly + 5% threshold"
}
```