---
name: bottleneck-hunter
category: tool
description: 找瓶颈 — 当策略表现低于预期时, 定位拖累夏普的具体环节 (因子/参数/数据/风控)
tags: [bottleneck, diagnose, attribution, debug]
---

# Bottleneck Hunter

策略不work时, 需要系统化定位瓶颈在哪。本 skill 是一份诊断 checklist, **先排除低级问题再分析高级问题**。

## 诊断流程

### Phase 1: 数据健康 (必查)

| 检查项 | 通过条件 | 工具 |
|--------|---------|------|
| OHLCV 完整性 | 无 NaN, volume > 0 | `data_quality.py` |
| 时间序列连续 | 无缺失交易日 | `pd.date_range` 对齐 |
| 价格合理性 | 无 > 50% 单日跳价 | abs(diff) > 0.5 |
| 复权一致性 | 前复权 vs 后复权口径统一 | `tushare` hfq/qfq flag |
| 行业代码映射 | 无 None/0 | SW/中信表 |
| 货币单位 | 全部人民币 (或全部美元) | 同源 |

**任一检查失败**: 修复后再回测。

### Phase 2: 因子层

| 现象 | 可能原因 | 修复 |
|------|---------|------|
| IC mean 低 (< 0.02) | 因子无预测力 | 换因子/加约束 |
| IC 波动大 | 因子不稳定 | 加 rolling 平滑 |
| 多空收益差为负 | 因子方向反 | 取负号 |
| 单调性差 | 因子分组无序 | 重设计 |
| IC 衰减太快 | 半衰期 < 3 日 | 改周频/月频 |

### Phase 3: 组合层

| 现象 | 可能原因 | 修复 |
|------|---------|------|
| 因子共线性高 | 多因子冗余 | PCA/orthogonalize |
| 权重过于集中 | IC 加权放大 | 设 max_weight |
| 调仓过频 | 换手 > 20%/月 | 提高阈值 |
| 极端行情崩溃 | 风控缺失 | 加 VaR/止损 |

### Phase 4: 回测层

| 现象 | 可能原因 | 修复 |
|------|---------|------|
| 样本内 IR 高, 样本外 0 | 过拟合 | 减少参数/用 OOS |
| 跨市场失效 | 局部规律 | 加宏观过滤 |
| 不同时间段表现差异大 | 制度变化 | 滚动检验 |
| 极端回撤 (>30%) | 黑天鹅/数据错 | 看是历史真实事件还是数据问题 |

### Phase 5: 执行层

| 现象 | 可能原因 | 修复 |
|------|---------|------|
| 实盘夏普 < 回测夏普 × 0.7 | 滑点/手续费估算偏低 | 加 stress cost |
| 开盘价严重偏离 | 跳价 | 信号延后到次日开盘 |
| 信号消失 | 容量上限 | 减仓位 |

## 归因工具

### Brinson 归因
```python
from performance_attribution import brinson_decompose
# 总超额 = 配置贡献 + 选股贡献 + 交互
```

### Sharpe 分解
```
总 Sharpe = Σ (子策略权重 × 子策略 Sharpe) + 多样性贡献
```

### Drawdown 分析
- 持续期 (DURATION)
- 深度 (DEPTH)
- 恢复时间 (RECOVERY)
- 三者乘积预测下次回撤

## 快速诊断命令

```bash
# 单因子 IC
quantnodes-research validate --factor "ts_mean(close,20)/ts_mean(close,60) - 1"

# 单次回测
quantnodes-research run my_workspace --strategy momentum

# 统计验证
quantnodes-research validate-run runs/run_0042

# Goal 体系 (覆盖完整研究流)
quantnodes-research goal start --session-id X --objective "Improve momentum_20_60 by +20% sharpe"
```

## 输出

诊断结束输出 1-2 段文字:
- 主要瓶颈: <what> (置信度: high/medium/low)
- 建议下一步: <action>
- 估算影响: <quantified improvement if possible>
```

## 红旗信号 (立刻停止回测)

1. 样本内夏普 > 3.0 (99% 过拟合)
2. IC mean > 0.15 (前视偏差)
3. 样本期 < 250 个交易日 (无统计意义)
4. 因子公式含未来数据 (e.g. `close.shift(-1)`)
5. 回测结果完美回撤 0 (代码 bug)