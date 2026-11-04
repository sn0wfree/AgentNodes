---
name: multi-factor
category: strategy
description: 多因子组合 — 等权/IC 加权/最优合成 + 相关性矩阵 + 降维 + 因子正交化
tags: [multi-factor, ic-weighted, optimization, pca, orthogonality]
---

# Multi-Factor Combination

多因子组合是把多个 alpha 因子合并为一个综合打分。常见误区是把高 IC 因子直接相加，结果被高相关因子反复加权。

## 组合方法谱系

| 方法 | 公式 | 优点 | 缺点 |
|------|------|------|------|
| 等权 | score = Σ factor_i | 简单稳健 | 不区分因子质量 |
| IC 加权 | w_i ∝ IC_i | 利用历史 IC | IC 不稳定 |
| IC 滚动加权 | w_i(t) ∝ IC_i(t-1..t-N) | 动态适配 | 需长样本 |
| 最优合成 (OLS) | y = Σ w_i * f_i + ε | 全样本最优 | 极易过拟合 |
| L1 正交化 (PCA) | 先 ortho, 再 IC 加权 | 消除共线 | 解释性差 |
| 风险平价 | vol 倒数加权 | 平衡风险贡献 | 需 vol 估计 |

## 实施步骤

### Step 1: 因子预筛
- 单因子 IC > 0.02, IR > 0.3
- IC > 0 占比 > 52%
- 单调性 (spearman of layer buckets) > 0.6
- 衰减期 < 10 日

### Step 2: 相关性矩阵分析
```python
import pandas as pd
factor_returns = pd.DataFrame({f.name: f.daily_returns for f in factors})
corr = factor_returns.corr()
# 高相关对 (>0.7) 留 1 个, 优先 IC 高的
# 中等相关 (0.4-0.7) 合并为复合因子
# 低相关 (<0.4) 直接保留
```

### Step 3: 正交化 (可选)
```python
from numpy.linalg import qr
orthogonalized, _ = qr(factor_returns.values)
# 重新计算 IC, 选 top-k
```

### Step 4: 权重选择
- 等权起点
- 用上 12 个月 IC rolling 估权重
- 月度再平衡

### Step 5: 验证
- 分组回测: top-q vs bottom-q 年化收益差
- 多空 Sharpe ≥ 1.0
- 因子权重稳定性 (权重月变化 < 30%)

## 常见陷阱

1. **未来函数**: 用同期收益对齐, 但权重必须只用历史数据
2. **数据窥探**: 同一数据池测试 50 个因子, 必有 IC > 0.05 的假阳性
3. **IC decay 错配**: 高换手因子用月频权重, 低换手用日频权重
4. **共线性放大**: 5 个高相关因子等权 = 单因子 × 5 倍波动

## 因子候选来源

> 2026-11-04：`alpha_zoo` 预置因子库已移除，不再有 `list_alphas()`。

候选因子改为**自己用表达式写**（`config.yaml` 的 `code` 字段），
或从研究账本里已沉淀的因子中挑选。组合流程不变：

```
预筛 → 相关性矩阵 → 组合 → 验证
```

## 输出格式

研究结束时输出 JSON 报告:

```json
{
  "factors_selected": 8,
  "combination_method": "ic_weighted",
  "weights": {"factor_a": 0.25, "factor_b": 0.15, ...},
  "ic_combined": 0.058,
  "ir_combined": 0.82,
  "max_corr_in_portfolio": 0.45,
  "long_short_sharpe": 1.34,
  "annual_turnover": 4.2
}
```