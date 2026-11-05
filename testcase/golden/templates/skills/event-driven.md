---
name: event-driven
category: strategy
description: 事件驱动策略 — 财报 / 分红 / 拆分 / 并购 / 解禁 / 增减持 等事件的信号提取与回测
tags: [event, earnings, dividend, split, m-and-a, unlock]
---

# Event-Driven Strategy

事件驱动策略在特定事件发生时建仓/平仓。难点是**事件窗口**和**幸存者偏差**。

## 常见事件类型

| 事件 | 信号方向 | 持有期 | 期望收益 |
|------|---------|--------|---------|
| 财报超预期 | 多 | 60 日 | +2% |
| 财报低于预期 | 空 | 30 日 | -1.5% |
| 现金分红 | 多 (除息日前后) | 5 日 | +0.3% |
| 股票拆分 | 中性 | — | — |
| 大股东减持公告 | 空 | 90 日 | -3% |
| 高管增持 | 多 | 60 日 | +1.8% |
| 重大合同中标 | 多 | 30 日 | +2.5% |
| 并购重组 | 不确定 | — | — |
| 限售解禁 | 空 (短期) | 30 日 | -1.2% |
| 退市风险警示 | 空 | 持续 | -8% |

## 实施流程

### Step 1: 事件数据库
- 数据源: tushare (`disclosure_date`, `event_type`)
- 字段: `(symbol, event_date, event_type, magnitude, source)`
- 去重: 同事件多次公告取首次

### Step 2: 事件标准化
- 财报超预期: actual_EPS > consensus × 1.05
- 大股东减持: 减持比例 > 0.5% 流通股
- 增持: 增持金额 > 1000 万

### Step 3: 信号构建
```python
# 事件收益 = P[t+60] / P[t-1] - 1  (前 1 日为基准)
# 异常收益 AR = event_return - market_return
# 累积异常收益 CAR = Σ AR
```

### Step 4: 回测
- **样本期外**: 至少 3 年
- **剔除事件日**: 避免未来函数
- **样本权重**: 大盘股事件权重高
- **跨事件去重**: 同一股票 30 日内多事件, 合并

### Step 5: 过滤

| 过滤条件 | 作用 |
|---------|------|
| 流动性 (成交额 > 1000 万) | 避免仙股 |
| 流通市值 > 50 亿 | 信号更稳定 |
| 过去 20 日波动率 < 行业均值 | 避开已大涨/大跌 |
| 非 ST/非停牌 | 合规 |

## 关键陷阱

1. **前视偏差**: 用事件日的公告内容定信号 → 必须在事件日前一天收盘前决定
2. **跳价滑点**: 重大事件开盘跳价, 实际成交价 ≠ 信号日收盘价
3. **事件撤销**: 增持计划终止, 信号失效 → 加撤销事件监测
4. **同事件窗口**: 财报季 (4/8/10 月) 多事件堆积, 信号被稀释
5. **行业事件**: 行业政策 (如教培双减) 影响整个行业, 个股 alpha 被掩盖

## 与 Alpha Zoo 集成

事件型策略较少用因子组合, 多用事件本身的特征工程:

```python
# 事件特征
features = {
    'event_magnitude': abs(surprise_pct),
    'days_since_last_event': 30,
    'company_size_log': np.log(market_cap),
    'past_cumulative_car_60d': past_car,
}
```

## 输出 JSON

```json
{
  "event_type": "earnings_beat",
  "n_events": 142,
  "avg_car_60d": 0.023,
  "sharpe": 1.18,
  "max_drawdown": -0.08,
  "annual_turnover": 6.5,
  "hit_rate": 0.58
}
```