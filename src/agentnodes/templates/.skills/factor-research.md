---
name: factor-research
category: strategy
description: 因子研究方法论 — IC/IR 验证 + 6 维评分 + 单调性 + 衰减分析
tags: [factor, ic, ir, validation, alpha]
---

# Factor Research Framework

借鉴自 vibe-trading factor-research skill，适配 strategy-research 框架。

## IC/IR 标准

| 指标 | 阈值 | 含义 |
|------|------|------|
| IC mean | > 0.03 | 因子有基本预测力 |
| IC mean | > 0.05 | 因子有强预测力 |
| IC mean | > 0.10 | 异常高，检查前视偏差 |
| IR (IC mean / IC std) | > 0.5 | 因子稳定有效 |
| IR | > 1.0 | 极强，非常罕见 |
| IC > 0 占比 | > 55% | 因子方向稳定 |
| IC > 0 占比 | < 50% | 因子方向不稳定，不可用 |

## 因子验证流程

### Step 1: IC/IR 验证
```python
# 计算 IC (Pearson 或 Spearman)
ic_series = factor_values.groupby(level=0).apply(
    lambda g: g["factor"].corr(g["return"], method="spearman")
)
ic_mean = ic_series.mean()
ic_std = ic_series.std()
ir = ic_mean / ic_std if ic_std > 0 else 0
```

### Step 2: 6 维评分

| 维度 | 权重 | 指标 |
|------|------|------|
| Stability | 0.25 | IC 正比例 + IC 标准差 |
| Diversification | 0.20 | 截面标准差均值 |
| Turnover | 0.15 | 相邻日期 Rank 相关性 |
| Monotonicity | 0.20 | 5 分位收益单调性 |
| Coverage | 0.10 | 非空比例 |
| Rank IC | 0.10 | 基于 IC 绝对值 |

### Step 3: Mutual IC 去重
- 与已有因子计算 |Spearman corr|
- |corr| < 0.7 才保留

### Step 4: IC 衰减检查
- IC_5d >= 30% * IC_1d

## 因子组合方法

### 等权组合
```
复合因子 = Z(factor1) + Z(factor2) + ... + Z(factorN)
其中 Z() 是截面 Z-score 标准化
```

### IC 加权组合
```
weight_i = |IC_mean_i| / sum(|IC_mean_j|)
复合因子 = sum(weight_i * Z(factor_i))
```

### 正交化组合
```
1. 按 IC 从高到低排序因子
2. 保留第一个因子不变
3. 对后续每个因子，回归到所有先前因子上，用残差作为正交化因子
4. 等权组合正交化因子
```

## 分组回测解读

### 标准
- **单调性**: Group_1 到 Group_N 的最终净值应呈单调递增（或递减）
- **多空价差**: 最高组和最低组的净值差（long_short_spread），越大越好
- **非线性**: 如果只有顶底组差异明显，中间组相似，因子可能仅在尾部有效
- **稳定性**: 分组权益曲线应平滑；剧烈波动表示因子不稳定

### 警告信号
- 分组权益曲线无明显差异 → 因子无效
- 非单调模式（V 形或倒 V 形）→ 因子可能有非线性关系
- 某组净值持续下跌 → 因子可能可反向使用

## 常见陷阱

1. **前视偏差**: 因子值必须用 T 日及之前数据计算，收益必须用 T+1 到 T+N 数据
2. **偏态分布**: 某些因子（如市值、换手率）严重右偏，直接计算 IC 会被异常值主导
3. **行业效应**: 因子值在同一行业内可能高度相似，导致选股集中在少数行业
4. **样本不足**: 每个截面至少需要 5 个有效资产
5. **因子拥挤**: 经典因子（动量、价值）被广泛使用后超额收益可能衰减
6. **幸存者偏差**: 仅在仍存活的股票上回测会高估因子表现



## 预置因子库（Alpha Zoo）已移除

> 2026-11-04：`alpha_zoo`（465+ 预置因子）已从本项目移除。

**现在只能用表达式因子**，在 `config.yaml` 里写 `code`：

```yaml
factors:
  - name: momentum_20d
    code: ts_return(close, 20)
    weight: 0.5
```

配置里若仍写 `alpha_id` / `alpha_ids`，框架会显式记一条 factor_failure
（不会静默跳过——「因子没算出来」和「选中空仓」含义不同）。

### 算子参考

表达式因子可用的算子（`apps/tools/ops.py` + `compute_factor.OPERATORS`）：

**截面算子**（宽 DataFrame）：`rank` · `zscore` · `scale`
**时序算子**：`ts_rank` · `ts_corr` · `ts_cov` · `ts_mean` · `ts_std`
  · `ts_max` · `ts_min` · `ts_argmax` · `ts_argmin`
**滞后差分**：`delta`
**工具算子**：`decay_linear` · `signed_power` · `safe_div` · `vwap`

完整算子实现见 `testcase/golden/apps/alpha_zoo/alpha_zoo_ops.py`（归档）。
