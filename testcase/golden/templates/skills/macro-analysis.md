---
name: macro-analysis
category: analysis
description: 宏观分析 — 美林时钟 / 利率-通胀传导 / 经济周期 / 政策预期定价
tags: [macro, investment-clock, regime, rates, inflation, growth]
---

# Macro Analysis

宏观是"为什么涨/跌"的最深层解释。本 skill 教 LLM 用宏观框架解释市场、做配置、识别 regime 切换。

## 美林投资时钟 (经典)

| 阶段 | 增长 | 通胀 | 最佳资产 | 典型行业 |
|------|------|------|---------|---------|
| 衰退 | ↓ | ↓ | 债券 | 防御、消费 |
| 复苏 | ↑ | ↓ | 股票 | 金融、地产 |
| 过热 | ↑ | ↑ | 大宗商品 | 能源、矿业 |
| 滞胀 | ↓ | ↑ | 现金/抗通胀 | 黄金、公用 |

### 识别指标
- **增长**: PMI, 工业增加值, 信贷增速, 失业率
- **通胀**: CPI, PPI, 大宗商品价格
- **利率**: 10Y 国债收益率, 央行政策利率

## 利率-通胀-增长传导链

```
油价上涨 → 成本端通胀 → 央行紧缩 → 利率上行 → 估值压制 → 股票下跌
                              ↓
                         债券下跌 (久期)
                              ↓
                         现金价值上升
```

## 经济周期指标

### 领先指标 (领先 3-6 月)
- M1 同比
- 社融增速
- PMI 新订单
- 消费者信心指数

### 同步指标
- GDP
- 工业增加值
- 失业率

### 滞后指标
- CPI
- PPI
- 库存

## Regime 检测

### 简单方法: 滚动相关 + 趋势
```python
# 用最近 60 日数据判断
recent_growth = gdp_growth.tail(60).mean()
recent_inflation = cpi.tail(60).mean()

if recent_growth > 0.05 and recent_inflation < 0.03:
    regime = "expansion_recovery"  # 复苏
elif recent_growth > 0.05 and recent_inflation > 0.03:
    regime = "overheat"            # 过热
elif recent_growth < 0.02 and recent_inflation > 0.03:
    regime = "stagflation"         # 滞胀
else:
    regime = "recession"           # 衰退
```

### 机器学习方法 (高级)
- HMM (隐马尔可夫模型)
- 聚类 (k-means on macro features)
- 分类 (XGBoost 训练宏观→市场)

## 配置建议

| Regime | 股票 | 债券 | 商品 | 现金 |
|--------|------|------|------|------|
| 衰退 | 20% | 60% | 10% | 10% |
| 复苏 | 60% | 20% | 15% | 5% |
| 过热 | 30% | 10% | 50% | 10% |
| 滞胀 | 15% | 15% | 40% | 30% |

## 政策预期定价

### 央行会议日历
- 美联储 FOMC (8 次/年)
- ECB GC (8 次/年)
- 中国央行货币政策委员会 (4 次/年)

### 鹰派/鸽派识别
| 措辞 | 含义 |
|------|------|
| "data dependent" | 中性 |
| "patient" | 鸽派 |
| "vigilant on inflation" | 鹰派 |
| "balanced risks" | 中性 |
| "full employment" | 鸽派 |

## 行业映射

| Regime | 利好行业 |
|--------|---------|
| 复苏 | 银行、地产、券商、汽车 |
| 过热 | 能源、矿业、化工、有色 |
| 滞胀 | 黄金、白酒、医药、公用 |
| 衰退 | 食品、医药、公用事业 |

## 陷阱

1. **Look-ahead bias**: 用事后公布的 GDP 训练模型
2. **数据修订**: GDP/CPI 多次修订, 实时判断需用初值
3. **政策窗口**: 央行会议前后波动率翻倍
4. **地缘风险**: 战争/制裁的传导路径难建模
5. **相关性切换**: 危机时股债相关性由负转正

## 实施

### 月度研究流程
1. 收集当月宏观数据 (CPI/PMI/GDP)
2. 更新 regime 判断
3. 检查组合配置是否需调整
4. 记录到 Goal/Hypothesis 系统

## 输出 JSON

```json
{
  "regime": "expansion_recovery",
  "regime_probabilities": {"recession": 0.15, "recovery": 0.55, "overheat": 0.20, "stagflation": 0.10},
  "key_indicators": {
    "gdp_growth": 0.052,
    "cpi_yoy": 0.018,
    "pmi": 51.2,
    "m1_yoy": 0.058
  },
  "central_bank_stance": "neutral_to_dovish",
  "next_policy_event": "2025-10-15 FOMC",
  "recommended_allocation": {"equity": 0.55, "bond": 0.25, "commodity": 0.15, "cash": 0.05}
}
```