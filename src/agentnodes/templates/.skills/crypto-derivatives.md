---
name: crypto-derivatives
category: crypto
description: 加密衍生品 — 永续合约资金费率 / 期货基差 / 期权波动率 / 套利机会
tags: [crypto, derivatives, perp, funding, basis, options]
---

# Crypto Derivatives

加密衍生品市场 24/7, 含**永续合约** (perpetual) / **交割合约** / **期权**。本 skill 覆盖主流机会与风险。

## 永续合约 (Perpetual Swap)

### 机制
- **资金费率 (Funding Rate)**: 每 8 小时结算一次 (00:00, 08:00, 16:00 UTC)
- 多头付空头 (正费率): 价差高于现货 → 鼓励做空
- 空头付多头 (负费率): 价差低于现货 → 鼓励做多
- 资金费率 = clamp( (mark_price - index_price) / index_price, -0.5%, 0.5% )

### 数据源
- Coinglass: 各交易所 funding 历史
- Exchange API: Binance / OKX / Bybit / dYdX

### 资金费率套利
```python
# 当 funding > 0.03% / 8h (年化 32%+) → 做空 perp + 做多现货
position_size = min(available_cash * 0.5, position_cap)

# 当 funding < -0.03% / 8h → 做多 perp + 做空现货 (需要融币)
```

### 风险
- 强平价差
- 现货/永续 同步性问题
- 资金费率突变 (黑天鹅)
- 平台信用风险

## 期货基差 (Basis)

### 基差定义
```python
basis = (futures_price - spot_price) / spot_price
annualized_basis = basis * (365 / days_to_expiry)  # 年化
```

### 基差交易
- **正基差** (期货 > 现货): 做多现货 + 做空期货 (期现套利)
- **负基差** (期货 < 现货): 做空现货 + 做多期货 (逆向套利, 需融券)

### Quarterly Futures
- 每季度末交割 (3/6/9/12 月)
- 基差通常在 5-15% 年化
- 临近交割日收敛

## 期权

### 平台
- Deribit (BTC/ETH 期权最大)
- OKX Options
- Binance Vanilla Options

### Greeks (与股票期权类似但路径不同)
- Delta: 与股票不同, 受 min_price step 影响
- Gamma: 临近到期剧变
- Vega: 加密 IV 高 (60-120%)
- Theta: 周末不衰减 (24/7)

### 策略
- Covered Call: 持币 + 卖 call (年化 10-30%)
- Cash-Secured Put: 持币 + 卖 put
- Straddle: 押波动
- Iron Condor: 押低波动 (加密效果差, 趋势太强)

## 关键指标

| 指标 | 数据源 | 阈值 |
|------|--------|------|
| Funding Rate | 交易所 API | ±0.01-0.05% / 8h |
| Open Interest | 交易所 API | > $1B (主流币) |
| 24h Volume | Coinglass | > $10B |
| Long/Short Ratio | Coinglass | > 1.5 (多) |
| Liquidations | Coinglass | 24h > $50M |
| Basis (季度) | 交易所 | 5-15% 年化 |
| IV (ATM 30d) | Deribit | 40-80% |

## 常见策略

### 1. Delta-Neutral 资金费率套利
```python
# Long spot + Short perp
# 收益 = funding_received - holding_costs
# 风险: spot/perp 偏离 → 需 rebalance
```

### 2. Basis Trade
```python
# Long quarterly future + Short spot (or vice versa)
# 收敛到 0 时平仓
# 持有期: 1-3 个月
```

### 3. 期权 Covered Call
```python
# 持 1 BTC + 卖 1 份 OTM call (delta ~0.3)
# 收益: premium (~ 1-3% 月)
# 风险: 币价涨超 strike → 被行权
```

## 风控

| 风险 | 缓释 |
|------|------|
| 强平 | 维持保证金率 > 50% |
| 平台跑路 | 多平台分散, 大额用冷钱包 |
| 流动性 | OI 监控, 避免小币种 |
| 黑天鹅 | 止损线 5%, 期权对冲 |
| 关联性 | BTC/ETH 高度相关, 别当真分散 |

## 实施注意

1. **时区**: 加密用 UTC, 资金费率结算在北京时间 8/16/24 点
2. **API 限流**: Binance 1200 次/分, OKX 600 次/分
3. **小数位**: BTC 0.01 步长, ETH 0.001 步长
4. **周末不停**: 数据连续, 无交易日历
5. **分叉风险**: BCH/BSV 等分叉币, 注意持仓处理

## 输出 JSON

```json
{
  "instruments": ["BTC-PERP", "ETH-PERP"],
  "funding_8h_bps": {"BTC": 5, "ETH": 3},
  "funding_annualized_pct": {"BTC": 5.5, "ETH": 3.3},
  "oi_usd": {"BTC": "12.5B", "ETH": "8.2B"},
  "iv_30d_atm_pct": {"BTC": 55, "ETH": 68},
  "recommended_strategies": ["funding_arb_btc", "covered_call_eth"],
  "max_position_size_usd": 50000,
  "stop_loss_pct": 5
}
```