---
name: seasonal
category: strategy
description: 季节性效应 — 日历异象 / 月度效应 / 节日效应 / 财报季效应 / 窗口期统计
tags: [seasonal, calendar, anomaly, turn-of-month, holiday]
---

# Seasonal Effects

日历异象是学术研究最古老的话题之一, 实务中**部分仍有效**但需小心使用。

## 经典日历异象

| 异象 | 描述 | A 股有效性 | 美股有效性 |
|------|------|-----------|-----------|
| 一月效应 | 小盘股 1 月跑赢 | ✅ 弱 | ✅ 已消失 |
| 周末效应 | 周一负收益 | ❌ | ❌ |
| 月末效应 | 月末 5 日正收益 | ✅ 弱 | ✅ 弱 |
| 节日效应 | 节前正收益 | ✅ | ✅ |
| Sell in May | 5-10 月跑输 | ❌ | ❌ |
| 财报季后 | 信息扩散期正收益 | ✅ 中 | ✅ 中 |

## 实施流程

### Step 1: 异象筛选
- 学术文献: 至少 3 篇独立研究复现
- 持续性: 最近 5 年仍然显著
- 跨市场: 至少 2 个市场有效

### Step 2: 窗口期定义
```python
# 月末效应: 月末 5 个交易日
month_end_window = (date.day >= 25) | (date.day <= 5)  # 含跨月
# 节日效应: 春节前 10 日 + 后 5 日
holiday_window = ...  # 自定义
```

### Step 3: 单异象回测
```python
# 信号: 在异象窗口期做多, 非窗口期做空
# 或: 异象窗口期内多头仓位, 其他时间减仓

signal = pd.Series(0, index=date_range)
signal[month_end_window] = 1  # 月末做多
# 或
target_weight = np.where(month_end_window, 1.0, 0.3)
```

### Step 4: 显著性检验
```python
from scipy import stats
returns_in_window = returns[month_end_window]
returns_out_window = returns[~month_end_window]
t_stat, p_value = stats.ttest_ind(returns_in_window, returns_out_window)
# p < 0.05 且效应大小 > 0.5% 月度
```

### Step 5: 与其他因子叠加
```python
# 日历异象通常是 alpha 的 10-20%, 应作为叠加而非主策略
combined_signal = base_factor + 0.2 * calendar_signal
```

## 常见异象实现

### 1. 一月小盘股效应 (A 股)
```python
small_cap_factor = -np.log(market_cap)
jan_filter = (date.month == 1)
combined = small_cap_factor * jan_filter.astype(int)
```

### 2. 月末效应
```python
last_5_days = (date.day >= 25)
signal = last_5_days.astype(float)
```

### 3. 财报季效应 (4/8/10 月)
```python
earnings_season = date.month.isin([4, 8, 10])
# 财报披露密集, 信息扩散 → 动量效应增强
momentum_enhanced = momentum * (1 + 0.3 * earnings_season)
```

### 4. 节日效应
- 春节: 农历除夕前 5 日 + 后 5 日
- 国庆: 10 月 1 日前 3 日 + 后 3 日
- 中秋/端午: 各 2 日

## 陷阱

1. **数据挖掘偏差**: 测试 50 个日历窗口, 必有 1-2 个显著
2. **制度变化**: A 股注册制后, 一月效应减弱
3. **流动性溢价**: 节日效应多由流动性不足解释, 容量小
4. **过拟合**: 用整个 2005-2024 数据定窗口, 然后样本外"验证"自己
5. **过度交易**: 月末换仓信号反复触发, 摩擦成本高

## 实证检验清单

- [ ] 至少 5 年数据
- [ ] t 检验 p < 0.01
- [ ] 效应大小 > 0.5% 月度收益
- [ ] 扣除交易成本后仍显著
- [ ] 跨 2 个市场有效
- [ ] 滚动 60 月均显著 (非单期偶然)
- [ ] 与基准的相关性 < 0.5

## 输出 JSON

```json
{
  "anomaly": "month_end_effect",
  "universe": "A 股中证 500",
  "window": "month_end_5d",
  "in_sample_sharpe": 0.65,
  "out_sample_sharpe": 0.42,
  "hit_rate": 0.62,
  "avg_effect_bps": 35,
  "annual_turnover": 4.5,
  "robustness": "strong"
}
```