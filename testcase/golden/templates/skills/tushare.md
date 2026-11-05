---
name: tushare
category: data-source
description: Tushare 数据源 — API 速查 + 积分门槛 + 字段映射 + 限流 + 注意事项
tags: [tushare, data-source, api, chinese-market, a-share]
---

# Tushare Data Source

Tushare 是国内最稳定的开源量化数据源之一。本 skill 覆盖常用 API、积分门槛、限流策略。

## 注册与认证

```python
import tushare as ts
ts.set_token('YOUR_TOKEN')  # 在 tushare.pro 注册获取
pro = ts.pro_api()
```

| 账号等级 | 积分要求 | API 限流 | 数据范围 |
|---------|---------|---------|---------|
| 试用 | 0 | 200 次/分 | 部分日线 |
| 基础 | 2000 | 200 次/分 | 全 A 股日线 |
| 高级 | 5000+ | 1000 次/分 | 财务/分钟 |

## 常用 API 速查

### 股票基础
```python
# 股票列表
pro.stock_basic(list_status='L', fields='ts_code,symbol,name,industry,list_date')

# 交易日历
pro.trade_cal(exchange='SSE', start_date='20240101', end_date='20241231')

# 名称变更
pro.namechange(ts_code='000001.SZ', fields='ts_code,name,start_date,end_date')
```

### 日线行情
```python
# 日线 (前复权)
pro.daily(
    ts_code='000001.SZ',
    start_date='20240101',
    end_date='20241231',
    adj='qfq',  # qfq 前复权, hfq 后复权, None 不复权
)

# 分钟线 (高级权限)
pro.stk_mins(ts_code='000001.SZ', freq='1min', start_date='20241010 09:00:00')

# 指数日线
pro.index_daily(ts_code='000300.SH', start_date='20240101', end_date='20241231')
```

### 财务数据
```python
# 利润表
pro.income(ts_code='000001.SZ', period='20240930', 
           fields='ts_code,end_date,revenue,n_income')

# 资产负债表
pro.balancesheet(ts_code='000001.SZ', period='20240930')

# 现金流量表
pro.cashflow(ts_code='000001.SZ', period='20240930')

# 财务指标
pro.fina_indicator(ts_code='000001.SZ', period='20240930',
                   fields='eps,roe,roa,grossprofit_margin')
```

### 行情类
```python
# 涨跌停统计
pro.limit_list_d(trade_date='20241010')

# 龙虎榜
pro.top_list(trade_date='20241010')

# 资金流向
pro.moneyflow(ts_code='000001.SZ', start_date='20241001', end_date='20241010')

# 北向资金
pro.hsgt_top10(trade_date='20241010', market_type='1')  # 沪股通
```

### 指数成分
```python
# 沪深 300 成分
pro.index_weight(index_code='000300.SH', start_date='20241001', end_date='20241010')
```

## 字段命名约定

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts_code` | str | 股票代码 `000001.SZ` |
| `trade_date` | str | 交易日期 `20241010` |
| `period` | str | 财报期 `20240930` |
| `adj` | str | 复权标志 |
| `open/high/low/close` | float | OHLC |
| `vol` | float | 成交量(手) |
| `amount` | float | 成交额(元) |

## 限流策略

### 错误处理
```python
import time

def safe_api_call(func, *args, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if 'rate limit' in str(e).lower():
                time.sleep(60)  # 等 1 分钟
            elif attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
```

### 批量拉取
```python
# 拉取全 A 股日线 (分批)
stocks = pro.stock_basic(list_status='L')['ts_code'].tolist()
batch_size = 50
for i in range(0, len(stocks), batch_size):
    batch = stocks[i:i+batch_size]
    # ... fetch one by one with sleep
    time.sleep(0.5)
```

## 与 strategy-research 集成

### data import 命令
```bash
quantnodes-research import --source tushare \
  --ts-code 000001.SZ \
  --start 2020-01-01 \
  --end 2024-12-31
```

### 内部封装
```python
# strategy_research.core.data_source.tushare_loader
from strategy_research.core.data_source.tushare_loader import TushareLoader
loader = TushareLoader()
df = loader.fetch('daily', ts_code='000001.SZ', start='20240101', end='20241231')
```

## 陷阱

1. **复权口径**: 跨期研究必须统一 (推荐前复权 qfq)
2. **退市股票**: stock_basic 的 `list_status='D'` 包含已退市, 必须保留
3. **财报延迟**: Q1 财报 4 月底前披露, 不能用 4 月 30 日的数据
4. **API 改名**: 字段名偶尔变化, 建议固定版本 (e.g. `fields=...`)
5. **停牌数据**: 停牌日 open/high/low = 0 或 NaN, 需特殊处理
6. **新股破发**: 上市首日可能跌 30%+, 因子模型需保护

## 替代数据源对比

| 数据源 | 优点 | 缺点 | 适用 |
|--------|------|------|------|
| Tushare | 稳定 + 文档 | 高级功能收费 | 全市场 |
| AKShare | 免费 + 实时 | 字段不统一 | 散户研究 |
| BaoStock | 免费 | 数据滞后 | 学习/回测 |
| Wind | 全面 | 贵 | 机构 |
| Choice | 全面 | 贵 | 机构 |

## 输出 JSON

```json
{
  "data_source": "tushare",
  "ts_codes_fetched": 4523,
  "date_range": "2020-01-01 to 2024-12-31",
  "fields": ["open", "high", "low", "close", "vol", "amount"],
  "adjustment": "qfq",
  "fetch_time_s": 312,
  "rate_limit_hits": 0,
  "missing_dates": ["2020-10-01", "2024-02-09"]
}
```