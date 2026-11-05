# {strategy_name} Research

## 策略概述
- 类型: {strategy_type}
- 目标函数: {goal_metric}
- 基线指标: (待填写)

## 快速开始

### 第一步: 了解当前状态
```bash
# 读取策略配置
cat strategies/{strategy_name}/strategy.py

# 读取历史实验
cat strategies/{strategy_name}/runs/results.tsv

# 查看最新实验结果
ls strategies/{strategy_name}/runs/ | tail -1
```

### 第二步: 开始实验循环
按以下步骤循环执行，永不停止：

1. **读取状态** → 了解当前因子池、参数、历史实验
2. **决策** → 选择下一步行动 (搜索/挖掘/优化/移除)
3. **执行** → 修改 strategy.py + 运行回测
4. **保存** → 创建 runs/run_XXXX/ 目录
5. **判断** → keep (改善) 或 discard (退化)
6. **重复** → 回到第 1 步

## 策略知识
(填写策略专属知识)

## 参数含义表
| 参数 | 含义 | 默认值 | 范围 |
|------|------|--------|------|
| (待填写) | | | |

## 因子表达式语法

### 时序算子 (per-asset)
| 算子 | 说明 | 示例 |
|------|------|------|
| ts_return(close, N) | N 期收益率 | ts_return(close, 20) |
| ts_std(series, N) | N 期标准差 | ts_std(ts_return(close,1), 20) |
| ts_corr(x, y, N) | N 期相关系数 | ts_corr(close, volume, 20) |
| ts_rank(series, N) | N 期排名百分比 | ts_rank(close, 20) |
| delay(series, N) | 滞后 N 期 | delay(close, 1) |
| delta(series, N) | N 期变化量 | delta(close, 5) |
| ts_max(series, N) | N 期最大值 | ts_max(high, 20) |
| ts_min(series, N) | N 期最小值 | ts_min(low, 20) |
| ts_mean(series, N) | N 期均值 | ts_mean(volume, 20) |

### 截面算子 (cross-section)
| 算子 | 说明 | 示例 |
|------|------|------|
| rank(series) | 截面排名 (0-1) | rank(ts_return(close,20)) |
| zscore(series) | 截面 z-score | zscore(ts_std(close,20)) |
| scale(series) | 截面缩放 (和=1) | scale(close) |
| winsorize(series, N) | 截面缩尾 | winsorize(ts_return(close,20), 3) |

## 覆盖维度定义
| 维度 | 示例因子 | 检查方式 |
|------|---------|---------|
| 动量 | ts_return(close, N) | 因子名/category 包含 momentum |
| 反转 | short_term_reversal | 包含 reversal |
| 波动率 | realized_vol, ts_std(ret, N) | 包含 volatility |
| 流动性 | amihud, turnover | 包含 liquidity |
| 量价 | price_volume_corr | 包含 volume_price |
| 宏观 | 宏观增长因子 | 包含 macro |

## 实验循环详细流程

### 每轮实验步骤

#### 1. 读取当前状态
```bash
# 读取当前策略配置
cat strategies/{strategy_name}/strategy.py

# 读取最近 5 个实验
tail -5 strategies/{strategy_name}/runs/results.tsv

# 查看最近 3 个 run 目录
ls strategies/{strategy_name}/runs/ | tail -3
```

#### 2. 决策下一步行动
根据当前状态选择：

| 条件 | 行动 | 说明 |
|------|------|------|
| 因子数 < 20 或 覆盖 < 60% | search_external | 外部搜索因子 |
| 因子数 >= 20 且 覆盖 >= 60% | discover_local | 本地算子挖掘 |
| 因子充足但参数不优 | optimize_param | 参数优化 |
| 因子过多 (>30) | remove_factor | 因子移除 |

#### 3. 执行行动

**搜索因子 (search_external)**
- 使用 web_search 搜索学术论文
- 提取因子表达式
- 验证 IC/IR

**本地挖掘 (discover_local)**
- 使用 MCTS 搜索算子组合
- 验证 IC/IR

**参数优化 (optimize_param)**
- 修改 PARAMS 中的参数值
- 运行回测验证

**因子移除 (remove_factor)**
- 移除 IR < 0.3 的因子
- 运行回测验证

#### 4. 保存实验结果
```bash
# 创建 run 目录
mkdir -p strategies/{strategy_name}/runs/run_XXXX

# 保存快照
cp strategies/{strategy_name}/strategy.py strategies/{strategy_name}/runs/run_XXXX/

# 运行回测
cd strategies/{strategy_name}
python strategy.py > runs/run_XXXX/run.log 2>&1

# 提取指标
grep "^calmar:\|^sharpe:\|^max_dd:" runs/run_XXXX/run.log
```

#### 5. 判断 keep/discard
- 目标函数改善 → keep
- 目标函数不变或退化 → discard
- 风控阈值触发 → discard

#### 6. 更新 results.tsv
```bash
# 追加到 results.tsv
echo "run_XXXX\tcommit\taction\tcalmar\tsharpe\tmax_dd\tann_return\tturnover\tfactors_added\tfactors_removed\tparams_changed\tstatus\tdescription" >> strategies/{strategy_name}/runs/results.tsv
```

## Subagent 调用

### 何时 spawn 哪个 Subagent

| 场景 | spawn 谁 | 原因 |
|------|---------|------|
| 需要发现新因子 | Factor Analyst | 复杂: 3 条路径 + 5 步验证 |
| 需要验证因子 | Factor Analyst | 复杂: IC/IR + 6 维评分 + 缓存 |
| 每次回测后 | Critic | 复杂: 风控检查 + 抗过拟合 |
| 简单决策 | 主 Agent 直接 | 简单: 读数据 + 判断 |
| 修改 strategy.py | 主 Agent 直接 | 简单: 文件编辑 |

### Subagent 调用示例

```
# 需要因子发现时
spawn(
    task="读取 .prompts/factor_analyst.md，按其指示完成因子发现和验证。当前因子池: [...], 缺少维度: [波动率, 流动性]",
    label="factor-analyst"
)

# 需要评估结果时
spawn(
    task="读取 .prompts/critic.md，按其指示评估回测结果。指标: calmar=0.71, sharpe=0.82, max_dd=-0.11",
    label="critic"
)
```

## 风控阈值
| 指标 | 阈值 | 说明 |
|------|------|------|
| MaxDD | <= -15% | 最大回撤上限 |
| Calmar | >= 0.5 | 收益/回撤比下限 |
| Sharpe | >= 0.3 | 风险调整收益下限 |
| 单资产权重 | <= 25% | 集中度上限 |
| 年化换手 | <= 600% | 成本控制 |
| 因子数 | <= 30 | 避免维度爆炸 |

## 抗过拟合检验
1. 起点依赖: 3 起点 CV% < 25%
2. 调仓日偏移: ±5 日 Calmar 稳定
3. 参数扰动: ±10% 退化 < 20%
4. 消融实验: 每关一项退化 >= 5%

## 停止条件
- 连续 5 轮无改善
- 最大 99999 轮 (永远不停)
- 用户中断 (Ctrl+C)

## NEVER STOP
一旦开始循环，不要停下来问用户是否继续。用户可能在睡觉。持续运行直到被手动中断。如果 run out of ideas，重新阅读本文件，尝试新的方向。

## 速度控制
每轮之间需要控制节奏,不能太快也不能太慢:
- 改善中: 正常速度 (30 秒间隔)
- 连续 3 轮无改善: 减速 (60 秒间隔)
- 连续 5 轮无改善: 再减速 (120 秒间隔)
- 卡住检测到: interrupt + 重启 Agent
每轮认真分析比快速迭代更重要。
