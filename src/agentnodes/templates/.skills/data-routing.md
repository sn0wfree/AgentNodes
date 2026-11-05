---
name: data-routing
category: data-source
description: 数据源路由 — 10 个数据源 (tushare/akshare/yfinance/ccxt/eastmoney/...) 的选型矩阵
tags: [data, source, routing, loader, fallback]
---

# Data Routing

借鉴自 vibe-trading data-routing skill，适配 strategy-research 框架。

## 数据源概览

| 数据源 | 市场 | 认证 | 网络 |
|--------|------|------|------|
| tushare | A 股/基金/期货/宏观 | 需要 `TUSHARE_TOKEN` | 国内 |
| akshare | A 股/美股/港/期货/外汇 | 无需 | 无限制 |
| yfinance | 美股/港股/ETF | 无需 | 需 Yahoo 访问 |
| okx | 加密货币 (OKX) | 无需 | 需 okx.com 访问 |
| ccxt | 加密货币 (100+ 交易所) | 无需 | 需交易所访问 |
| baostock | A 股 (免费日线/分钟) | 无需 | 国内 |
| tencent | A 股/港/美股 (永不封禁) | 无需 | 无限制 |
| mootdx | A 股 (TDX 服务器) | 无需 | 国内 |
| iFinD | A 股/宏观/港美股 | 需 MCP token | 需 API 访问 |

## 能力 → 工具路由

| 数据需求 | 工具 | 市场 |
|----------|------|------|
| OHLCV 价格 | `get_market_data` | A 股/美股/港/加密/期货/外汇 |
| 资金流向 | `get_fund_flow` | A 股/港/美股 |
| 龙虎榜 | `get_dragon_tiger` | A 股 |
| 北向资金 | `get_northbound_flow` | A 股 |
| 融资融券 | `get_margin_trading` | A 股 |
| 大宗交易 | `get_block_trades` | A 股 |
| 股东户数 | `get_shareholder_count` | A 股 |
| 限售解禁 | `get_lockup_expiry` | A 股 |
| 板块分类 | `get_sector_info` | A 股 |
| 研报 | `get_research_reports` | A 股 |
| 新闻 | `get_stock_news` | A 股/美股/港 |
| 财务报表 | `get_financial_statements` | A 股/美股/港 |
| 期权链 | `get_options_chain` | 美股 |
| 宏观数据 | `get_macro_series` | 宏观 |

## 源优先级 (OHLCV)

### A 股
```
tencent / mootdx (永不封禁) > tushare (需 token) > baostock / akshare > eastmoney (限流)
```

### 美股
```
stooq / yahoo > tiingo / finnhub / fmp / alphavantage (需 key) > sina / eastmoney > yfinance
```

### 港股
```
tencent > eastmoney / yahoo > yfinance
```

### 加密货币
```
okx (单交易所) > ccxt (多交易所)
```

## 决策树

### 回测场景 (写 config.json)
使用 `source: "auto"` — 运行器根据代码格式自动路由，同市场源之间自动回退。

### 分析/研究场景
1. 识别数据需求，查能力表找工具 + env key
2. 如需 OHLCV，调用 `get_market_data`，让源回退运行
3. 设置所需的 env key；如缺失，报告缺失而非静默失败

## 符号格式参考

| 市场 | 格式 | 示例 |
|------|------|------|
| A 股 | `NNNNNN.SZ/SH/BJ` | 000001.SZ, 600000.SH, 430139.BJ |
| 美股 | `TICKER.US` | AAPL.US, MSFT.US |
| 港股 | `NNNNN.HK` | 00700.HK, 09988.HK |
| 加密货币 | `SYMBOL-USDT` | BTC-USDT, ETH-USDT |
| 期货 | `XXNNNN.EXCHANGE` | CU2406.SHFE |
| 外汇 | `XXX/YYY` | USD/CNY, EUR/USD |

## 数据验证纪律

1. **交叉验证**: 关键数字需 ≥2 个独立来源验证
2. **偏差标记**: >1% 偏差标记为 ⚠️ 校准不匹配
3. **单源标记**: 未验证数字标记为 "single-source" 或 "estimate"
