# 强制回测流程（详细版）

> 本文件是 `_common/principles.md` 红线约束的具体执行方法。适用于所有会跑回测的角色（chat / researcher / strategist / backtest_diagnostics 等）。

## 流程总览

```
步骤 1（list_files）  → 步骤 2（write_file × 2）  → 步骤 3（run_backtest）  → 步骤 4（汇报）
   确认目录存在           创建 strategy.py              不可省略              引用返回值
                          + config.yaml                等返回值               给 run_id
```

## 步骤 1：list_files 确认目录存在

```python
list_files(workspace=workspace, path="strategies")
```

**目的**：避免直接 write_file 到不存在的目录。

**常见错误**：跳过此步直接 write_file。

## 步骤 2：write_file 创建两个文件

创建两个文件，**顺序可换但必须都有**：

### 2a. strategy.py

参考模板 `templates/strategy.py`：

```python
PARAMS = {
    "top_n": 10,
    "max_weight": 0.25,
    "rebalance_freq": 5,
}

FACTOR_EXPRS = [
    {
        "factor_name": "momentum_20d",
        "factor_code": "ts_return(close, 20)",
        "weight": 1.0,
    },
]

FACTOR_WEIGHT_METHOD = "equal"
```

### 2b. config.yaml

参考模板 `templates/config.yaml`：

```yaml
strategy:
  name: my_strategy
  type: rotation
data:
  source: auto+duckdb
  codes:
    - 000001.SZ
    - 600519.SH
  start_date: 2020-01-01
  end_date: 2025-12-31
rebalance:
  freq: W
  min_history: 252
top_n: 10
max_weight: 0.25
factors:
  - name: momentum_20d
    code: ts_return(close, 20)
    weight: 1.0
```

## 步骤 3：run_backtest（**不可省略**）

```python
run_backtest(workspace=workspace, strategy_name="my_strategy")
```

**关键约束**：

- ❌ **禁止**在调用前声称任何回测结果
- ❌ **禁止**用"理论上"、"应该会"等推测语言
- ✅ 必须等待 tool 返回实际指标

### run_backtest 常见错误处理

| 错误类型 | 常见原因 | 处理方法 |
|---|---|---|
| ImportError | 缺少依赖 | `pip install xxx` 后重试 |
| KeyError | DataFrame 列名不匹配 | 用 `read_file` 查 strategy.py 实际字段 |
| FileNotFoundError | 策略目录不存在 | 回到步骤 1 确认 |
| 数据加载失败 | 网络/duckdb 问题 | 检查 `data.source` 配置 |

## 步骤 4：汇报（基于返回值）

**正确示例**：

> "已跑完回测，run_id = `20250608_abc123`。
>
> - 年化收益: 18.5% `[来源: run_backtest 返回值]`
> - Sharpe: 1.42 `[来源: run_backtest 返回值]`
> - MaxDD: -8.3% `[来源: run_backtest 返回值]`
>
> 完整结果见 `runs/20250608_abc123/results.tsv`。"

**错误示例**：

> ❌ "V1 的 sharpe 是 1.5，V2 是 1.8，V2 更好。"

（V1 数据从哪来？V2 又从哪来？没有 tool 返回值 = 编造）

## 反模式（绝对禁止）

### ❌ 反模式 1：跳过步骤 3 推算结果

```
步骤 1: list_files ✅
步骤 2: write_file ✅
步骤 3: "理论上应该 sharpe > 1.5"
```

**禁止**。必须实际调用 `run_backtest`。

### ❌ 反模式 2：用上次实验数据当这次结果

```
"V1 是上个月跑过的，sharpe = 1.42"
```

**禁止**。每次回测都要重新调用工具，除非用户明确要求引用历史结果。

### ❌ 反模式 3：批量跳过中间步骤

```
用户问"对比 V1 和 V2"
→ "V1 sharpe 1.2，V2 sharpe 1.5，V2 更好"
```

**禁止**。除非你刚刚在**同一对话**中跑过两者的回测并持有返回值。

## 何时读本文件

- 准备第一次跑回测，不确定流程
- 步骤 3 报错，需要排查
- 用户要求"快速跑个回测"（容易被诱惑跳过步骤）

## 何时**不**读本文件

- chat 模式 + chat.md 已内联高频规则 + 问题简单
- 你已经在同一对话中跑过多次回测，流程熟悉