---
name: sentiment-analysis
category: strategy
description: 情绪分析策略 — 新闻 / 社交媒体 / 研报 / 龙虎榜 情绪打分与反转信号
tags: [sentiment, news, social, reversal, NLP]
---

# Sentiment Analysis Strategy

利用非结构化文本的情绪打分预测短期股价。**核心挑战**: 数据噪声大、情绪衰减快、易被操纵。

## 数据源

| 来源 | 实时性 | 覆盖 | 噪声 | 备注 |
|------|--------|------|------|------|
| 新闻 (新浪/财联社) | 秒级 | 全市场 | 中 | 标题级情绪 |
| 雪球/股吧 | 分钟级 | 散户集中股 | 高 | 反向指标 |
| 微博/小红书 | 分钟级 | 消费/科技 | 高 | 时尚/消费可用 |
| 研报 (Wind/Choice) | 日级 | 全市场 | 低 | 机构情绪 |
| 龙虎榜 | 日级 | 异动股 | 中 | 游资动向 |
| 涨停板 | 日级 | 涨停股 | 高 | 短期动量 |

## 情绪打分方法

### 1. 词典法 (适合快速起步)
```python
positive_words = ['超预期', '突破', '增持', '成长', '景气', '放量', '突破']
negative_words = ['下滑', '减持', '亏损', '下行', '缩量', '破位']
# 句子级: score = (pos - neg) / total_words
```

### 2. LLM-as-judge (推荐)
```python
# 用 LLM 给定 1-10 的情绪打分
prompt = f"为以下研报标题打分 (1-10), 10=极度看好, 1=极度看空:\n{title}"
response = llm.chat([{"role": "user", "content": prompt}])
score = float(response.content.strip())
```

### 3. 预训练模型 (金融专用)
- FinBERT (ProsusAI)
- Chinese FinBert (Dejian-金融)
- BloombergGPT (私有)

## 信号合成

### 日度情绪指数
```python
daily_sentiment = (sum(score for news in todays_news) / n_news)
# 平滑: 5 日 EMA
smoothed = daily_sentiment.ewm(span=5).mean()
```

### 反转信号
```python
# 极端乐观 → 反向
reversal_signal = -zscore(smoothed, window=60)
# 极端悲观 → 正向 (rare)
```

## 回测陷阱

1. **未来函数**: 用 LLM 打分时, 必须用事件发布**前**的模型权重
2. **标签泄漏**: 训练集/测试集必须按时间切分, 不能随机
3. **幸存者偏差**: 退市股情绪数据不全
4. **操纵识别**: 单日情绪剧烈变化 > 5σ 可能是机器人/庄家操控
5. **冷启动**: 新股没有历史情绪数据

## 实战建议

| 场景 | 推荐方法 |
|------|---------|
| 中长线 (持仓 30 日) | 研报情绪 + 行业景气 |
| 短线 (持仓 1-5 日) | 新闻情绪 + 涨停板异动 |
| 反转策略 | 极端情绪反向 |
| 跟随策略 | 龙虎榜 + 大宗交易 |

## 风控

- 单信号最大仓位: 5%
- 信号一致性要求: 至少 2 个独立来源同向
- 黑名单: 涉及监管事件/财务造假/退市警示的股票全部剔除

## 输出 JSON

```json
{
  "data_source": "tushare_news + llm_judge",
  "sentiment_method": "5d_ema",
  "signal_type": "reversal_at_extreme",
  "n_trades_per_year": 145,
  "long_sharpe": 0.92,
  "short_sharpe": 1.04,
  "win_rate": 0.54,
  "avg_holding_days": 4.2
}
```