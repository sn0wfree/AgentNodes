---
name: options-payoff
category: tool
description: 期权 payoff 图解 — 4 个基本策略 + 风险图 + Greeks 速查 + 盈亏平衡点
tags: [options, payoff, strategy, greeks, black-scholes]
---

# Options Payoff Visualization

本 skill 教 LLM 理解和画 4 个基本期权策略的 payoff 图。

## 基本策略

### 1. Long Call (看涨多头)

```
Payoff at T: max(S - K, 0) - premium
Break-even: K + premium
Max loss: premium
Max gain: unlimited
```

### 2. Long Put (看跌多头)

```
Payoff at T: max(K - S, 0) - premium
Break-even: K - premium
Max loss: premium
Max gain: K - premium
```

### 3. Short Call (看涨空头 / 备兑)

```
Payoff at T: premium - max(S - K, 0)
Break-even: K + premium
Max gain: premium
Max loss: unlimited (危险)
```

### 4. Short Put (看跌空头 / 担保)

```
Payoff at T: premium - max(K - S, 0)
Break-even: K - premium
Max gain: premium
Max loss: K - premium
```

## 组合策略

### Bull Call Spread (牛市价差)
```python
# 买低执行价 call, 卖高执行价 call
# 净 debit = call_long_premium - call_short_premium
# Max profit = (K_high - K_low) - net_debit
# Max loss = net_debit
# 适用: 温和看涨
```

### Bear Put Spread (熊市价差)
```python
# 买高执行价 put, 卖低执行价 put
# 与 Bull Call 对称
```

### Straddle (跨式)
```python
# 同时买 ATM call + ATM put (同到期)
# 押注大幅波动, 方向中性
# Max loss = 总权利金
# 盈亏平衡: K ± 总权利金
```

### Strangle (宽跨式)
```python
# 买 OTM call + OTM put
# 比 Straddle 便宜, 但需要更大波动才盈利
```

### Iron Condor (铁鹰)
```python
# 卖 OTM put + 买更低 OTM put (保护)
# 卖 OTM call + 买更高 OTM call (保护)
# 押注低波动, 区间震荡
```

### Butterfly (蝶式)
```python
# 1 × K1 call + 2 × K2 call (卖) + 1 × K3 call
# K1 < K2 < K3, K2 = (K1+K3)/2
# 押注价格在 K2 附近
```

## Greeks 速查

| Greek | 含义 | Long Call | Long Put |
|-------|------|-----------|----------|
| Delta | 价格变动 | + (0~1) | - (-1~0) |
| Gamma | Delta 变动 | + | + |
| Theta | 时间衰减 | - | - |
| Vega | 波动率敏感 | + | + |
| Rho | 利率敏感 | + | - |

## Payoff 图绘制

### Python 示例 (call spread)
```python
import numpy as np
import matplotlib.pyplot as plt

S_range = np.linspace(80, 120, 100)
K_low, K_high = 95, 105
premium_low, premium_high = 5, 2

payoff = (
    np.maximum(S_range - K_low, 0) - premium_low
    - (np.maximum(S_range - K_high, 0) - premium_high)
)

plt.figure(figsize=(10, 6))
plt.plot(S_range, payoff)
plt.axhline(0, color='gray', linestyle='--')
plt.axvline(K_low, color='red', label=f'K_low = {K_low}')
plt.axvline(K_high, color='green', label=f'K_high = {K_high}')
plt.xlabel('Stock Price at Expiry')
plt.ylabel('Profit / Loss')
plt.title('Bull Call Spread Payoff')
plt.legend()
plt.grid(True)
plt.show()
```

## 风险图关键节点

| 节点 | 计算 |
|------|------|
| 最大亏损 | 在所有 K 处的最小 payoff |
| 最大盈利 | 在所有 K 处的最大 payoff (含 unlimited) |
| 盈亏平衡 | payoff = 0 的 S 值 |
| 标的盈亏比 | max_profit / |max_loss| |

## 策略选择矩阵

| 市场观点 | 推荐策略 |
|---------|---------|
| 温和看涨 | Bull Call Spread |
| 强烈看涨 | Long Call |
| 看涨但保守 | Covered Call |
| 温和看跌 | Bear Put Spread |
| 强烈看跌 | Long Put |
| 大幅波动 | Straddle / Strangle |
| 低波动 | Iron Condor |
| 价格不变 | Short Straddle (高风险) |

## 实施陷阱

1. **隐含波动率变化**: 同一策略在不同 IV 下 payoff 形状不变, 但市场价值变化大
2. **提前行权**: 美式期权可能在到期前行权 (尤其深度 ITM)
3. **股息影响**: 除息日前期权 Delta 跳跃
4. **流动性**: OTM 期权流动性差, 滑点大
5. **保证金**: Short 期权需冻结保证金, 影响组合 leverage

## 输出 JSON

```json
{
  "strategy": "bull_call_spread",
  "K_low": 95,
  "K_high": 105,
  "premium_paid": 3.0,
  "max_profit": 7.0,
  "max_loss": -3.0,
  "breakeven": 98.0,
  "delta_atm": 0.5,
  "theta_30d": -0.04,
  "vega_30d": 0.18,
  "implied_vol": 0.25,
  "days_to_expiry": 30
}
```