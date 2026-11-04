---
name: alpha-zoo
category: strategy
description: Alpha Zoo 因子库发现 — 460+ 因子的分类 / 检索 / 选用 / 注册
tags: [alpha-zoo, factor-registry, factor-discovery, factor-library]
---

# Alpha Zoo Factor Library

Alpha Zoo 是一个**开源因子库**, 含 460+ 经过初步验证的因子表达式。本 skill 教 LLM 如何高效检索和选用。

## 因子分类

### 按市场异象分

| 类别 | 代表因子 | 数量 |
|------|---------|------|
| 动量 (momentum) | ts_return, ts_mean ratio | ~80 |
| 反转 (reversal) | -ts_return short window | ~30 |
| 价值 (value) | E/P, B/P, CF/P | ~50 |
| 质量 (quality) | ROE, ROA, accruals | ~40 |
| 规模 (size) | log(market_cap) | ~15 |
| 波动率 (volatility) | -std, -beta | ~35 |
| 资金流 (flow) | volume-price corr | ~25 |
| 分析师 (analyst) | EPS revision, target price | ~30 |
| 另类 (alternative) | 卫星图像, 卡車流量 | ~10 |

### 按数据源分

| 类别 | 字段依赖 | 代表 |
|------|---------|------|
| 价格 (OHLCV) | close, volume | ts_return, ts_mean |
| 财务 (fundamental) | 季报/年报 | E/P, ROE |
| 分析师 | 共识/修正 | EPS revision |
| 另类 | 卫星/刷卡 | 客流指数 |

## 检索接口

### 命令行
```bash
# 列出所有动量因子
quantnodes-research alpha list --category momentum

# 显示因子详情
quantnodes-research alpha show gtja191_001

# 跨基准比较
quantnodes-research alpha bench --ids gtja191_001,gtja191_002

# 导出 manifest
quantnodes-research alpha export-manifest > my_factors.yaml
```

### Python API
```python
from strategy_research.core.alpha_zoo_yaml import (
    list_alphas, show_alpha, alpha_categories,
)

# 列表
momentum_alphas = list_alphas(category='momentum')
print(len(momentum_alphas), 'momentum factors')

# 详情
alpha = show_alpha('gtja191_001')
print(alpha.formula, alpha.universe)

# 分类
cats = alpha_categories()
# ['momentum', 'reversal', 'value', 'quality', 'size', 'volatility', ...]
```

## 因子选用决策树

```
START → 已知 alpha_id?
├─ YES → 直接用 (alpha show <id>)
└─ NO  → 已知想测的异象?
    ├─ YES → alpha list --category <x> → 选 5-10 个 → 单因子 IC 测试
    └─ NO  → 已知数据源?
        ├─ OHLCV  → ts_* 类 (动量/反转/波动率)
        ├─ 财务   → E/P, ROE 等 (价值/质量)
        ├─ 分析师 → EPS revision, target up
        └─ 另类   → 看具体数据源字段
```

## 实战流程

### Step 1: 候选筛选
- 从 460+ 因子初筛 30-50 个
- 标准: 同类因子不要超过 5 个 (避免共线)

### Step 2: 单因子 IC 测试
```bash
quantnodes-research validate --factor <expr> --start 2020-01-01
```
- IC > 0.02, IR > 0.3, hit_rate > 52%

### Step 3: 相关性矩阵
- 留下低相关 (< 0.4) 的因子
- 高相关 (> 0.7) 留 IC 最高的

### Step 4: 组合
- 5-8 个因子
- IC 加权起点
- 滚动 12 月再平衡

### Step 5: 全市场 OOS 验证
- 样本外 1 年以上
- 与市场基准 (中证 500) 比较

## 注册新因子

如果现有因子不够, 可以注册新因子到本地 zoo:

```bash
# 创建 .py 文件, 包含 compute_factor() 函数
quantnodes-research alpha register my_alpha.py --name "my_vol_carry"
```

`compute_factor` 必须:
1. 输入: pandas DataFrame (含 OHLCV)
2. 输出: pandas Series (单因子)
3. 无前视偏差 (只用 shift(N) 或更早数据)

## 引用与 License

- Alpha Zoo 原始数据归原作者
- 引用须注明: `Source: Alpha Zoo (gtja191 series)`
- License: 内部研究免费, 商业使用需另行授权

## 常见陷阱

1. **数据对齐**: 财务因子有披露延迟, 不能用当日数据
2. **幸存者偏差**: Alpha Zoo 因子多基于现存股票, 退市股被剔除
3. **过拟合**: 460 个因子选 5 个, 必有 IC > 0.02 的假阳性
4. **行业暴露**: 多数因子隐含行业偏好, 需中性化处理
5. **微观结构**: 高换手因子受流动性限制

## 输出

```json
{
  "candidate_factors": 35,
  "after_ic_filter": 12,
  "after_correlation_filter": 7,
  "final_portfolio": ["gtja191_001", "gtja038_002", ...],
  "combination_method": "ic_weighted",
  "expected_sharpe": 0.85,
  "annual_turnover": 8.5
}
```