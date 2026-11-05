# Backtest Engine 设计文档

> 借鉴 vibe-trading 成熟 backtest 架构，重构 quantnodes strategy-research 回测引擎
>
> 创建：2026-07-22 · 状态：实施中

---

## 1. 目标

将 vibe-trading 的 9 个市场引擎 + bar-by-bar 执行循环搬入 strategy-research，同时保留 DuckDB 作为唯一数据仓库。

### 核心决策

| 决策点 | 选择 |
|---|---|
| 数据源 | **DuckDB 唯一仓库**；loader 降级为 fetch → write DuckDB |
| 引擎模式 | **bar-by-bar 执行**（逐 bar 处理 hooks/rebalance/equity） |
| 信号接口 | **SignalEngine.generate(data_map) → {code: Series}** |
| 向后兼容 | 旧路径 `run_strategy(subprocess)` 保留；新路径独立 |

---

## 2. 架构

```
core/engine/
├── __init__.py
├── base.py              # BaseEngine — 核心执行循环
├── signals.py           # SignalEngine 抽象基类
├── models.py            # Position / TradeRecord / EquitySnapshot (整合已有)
├── config.py            # BacktestConfigSchema (Pydantic)
├── runner.py            # 主入口 + AST guard + engine routing
├── market_hooks.py      # per-bar hooks (funding/liquidation/swap)
├── benchmark.py         # 自动基准解析
├── validation.py        # 整合 P3-c validation
├── artifacts.py         # 写 OHLCV/equity/trades CSV
├── china_a.py           # A 股引擎
├── global_equity.py     # US/HK 引擎
├── crypto.py            # 加密引擎
├── forex.py             # 外汇引擎
├── india_equity.py      # 印度引擎
├── futures_base.py      # 期货基类
├── china_futures.py     # 中国期货
├── global_futures.py    # 全球期货
├── composite.py         # 跨市场组合
└── optimizers/
    ├── __init__.py
    └── base.py           # 5 个优化器 (QuantOPT 适配层)
```

---

## 3. 数据流

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────┐
│  8 Loaders  │────▶│  DuckDB      │────▶│  BaseEngine         │
│  (fetch)    │     │  price_data  │     │  _execute_bars()    │
└─────────────┘     │  (OHLCV)     │     │  bar-by-bar loop    │
                    └──────────────┘     └─────────┬───────────┘
                                                   │
                              ┌─────────────────────┼─────────────────────┐
                              │                     │                     │
                              ▼                     ▼                     ▼
                        equity_curve            trades[]           run_card.json
                        (pd.Series)        (List[TradeRecord])    + artifacts/
```

### 3.1 数据读取

```python
# 新增函数 (db.py)
def load_ohlcv_data(workspace_path, strategy_name, codes=None, start_date=None, end_date=None)
    → dict[str, pd.DataFrame]  # {code: DataFrame(OHLCV)}

# 保留旧函数 (向后兼容)
def load_price_data(workspace_path, strategy_name, ...)
    → pd.DataFrame  # (T, N) close-only panel
```

### 3.2 执行循环

```python
# BaseEngine._execute_bars() 伪代码
for bar_idx, ts in enumerate(dates):
    # 1. per-bar hooks (funding/liquidation/swap)
    for symbol in codes:
        engine.on_bar(symbol, data_map[symbol].loc[ts], ts)

    # 2. rebalance each symbol
    equity = engine._calc_equity(close_df, ts)
    for symbol in codes:
        target_weight = target_pos.at[ts, symbol]
        engine._rebalance(symbol, target_weight, data_map[symbol], ts, equity)

    # 3. record equity snapshot
    engine.equity_snapshots.append(EquitySnapshot(...))

# 4. force-close remaining positions
for symbol in engine.positions:
    engine._close_position(symbol, last_price, last_ts, "end_of_backtest")
```

---

## 4. BaseEngine 接口

### 4.1 抽象方法（每个市场引擎必须实现）

| 方法 | 签名 | 说明 |
|---|---|---|
| `can_execute` | `(symbol, direction, bar) → bool` | 市场规则是否允许此交易 |
| `round_size` | `(raw_size, price) → float` | 整手/精度取整 |
| `calc_commission` | `(size, price, direction, is_open) → float` | 佣金计算 |
| `apply_slippage` | `(price, direction) → float` | 滑点模型 |
| `on_bar` | `(symbol, bar, timestamp) → None` | per-bar hooks |

### 4.2 可选 override（期货等需要）

| 方法 | 默认实现 | 用途 |
|---|---|---|
| `_calc_pnl` | `direction * size * (exit - entry)` | PnL 公式（期货有合约乘数） |
| `_calc_margin` | `size * price / leverage` | 保证金公式 |

---

## 5. 市场引擎规则

### 5.1 ChinaAEngine

| 规则 | 值 |
|---|---|
| T+N | T+1（当日买入不可当日卖出） |
| 做空 | 禁止 |
| 整手 | 100 股 |
| 涨跌停 | 主板 10%，创业板/科创板 20%，北交所 30% |
| 佣金 | 万 2.5（最低 ¥5）+ 万 0.1 过户费 |
| 印花税 | 万 5（仅卖出） |
| 滑点 | 0.1% |

### 5.2 GlobalEquityEngine

| 规则 | US | HK |
|---|---|---|
| T+N | T+0 | T+0 |
| 做空 | 允许 | 允许 |
| 整手 | fractional (0.01) | 100 股 |
| 佣金 | $0 | 万 1.5 |
| 印花税 | $0 | 万 10（双向） |
| 征费 | $0 | SFC+FRC+CCASS |
| 滑点 | 0.05% | 0.1% |

### 5.3 CryptoEngine

| 规则 | 值 |
|---|---|
| 交易时间 | 24/7 |
| 佣金 | maker 0.02% / taker 0.05% |
| Funding | 每 8h (00:00/08:00/16:00 UTC) |
| 强平 | tiered maintenance margin |
| 精度 | 6 位小数 |

### 5.4 ForexEngine

| 规则 | 值 |
|---|---|
| 交易时间 | 24x5 |
| 成本 | spread-as-cost (pip table) |
| 杠杆 | 默认 100:1 |
| Swap | 每日，周三三倍 |

### 5.5 FuturesBaseEngine

| 规则 | 值 |
|---|---|
| PnL | `direction * size * (exit - entry) * contract_multiplier` |
| Margin | `size * price * contract_multiplier / leverage` |
| 佣金 | per-contract (USD) |

### 5.6 CompositeEngine

跨市场组合：共享资金池，委托子引擎规则方法。

---

## 6. Optimizer 适配层

基于 QuantOPT 的 5 个优化器，通过统一接口 `optimize_weights()` 调用。

### 6.1 接口

```python
from strategy_research.core.engine.optimizers import optimize_weights

result = optimize_weights(ret_df, pos_df, dates, method="risk_parity")
# ret_df: 收益率矩阵 (T, N)
# pos_df: 当前权重矩阵 (T, N)
# dates: 统一日期索引
# method: 优化方法名
# 返回: 优化后的权重矩阵 (T, N)
```

### 6.2 优化器列表

| 方法 | 算法 | QuantOPT 模型 | 说明 |
|---|---|---|---|
| `equal_volatility` | inverse-volatility | 自写 (~15行) | 按波动率倒数分配权重 |
| `risk_parity` | 等风险贡献 | `RiskParity` | 每个资产对组合风险贡献相等 |
| `mean_variance` | Markowitz 均值方差 | `MVO` | 最大化夏普比率 |
| `max_diversification` | 最大化分散化比率 | `MaxIR` | 最大化分散化收益/风险比 |
| `turnover_aware` | 考虑换手成本 | `MaxRiskAdjReturn` | 带 L1 换手惩罚的均值方差 |

### 6.3 QuantOPT 集成

```python
# 直接调用 QuantOPT 模型 (非 RunOpt 复杂路径)
from QuantOPT.models.model_RiskParity import RiskParity
from QuantOPT.models.model_MVO import MVO
from QuantOPT.models.model_MaxIR import MaxIR
from QuantOPT.models.model_MaxRiskAdjReturn import MaxRiskAdjReturn

# 统一接口: optimize_weights() → pd.DataFrame
# 优化失败自动 fallback 到 equal weight
```

### 6.4 集成到 Engine

```python
# runner.py — 自动构建 optimizer callable
opt_func = lambda ret_df, pos_df, dates: optimize_weights(
    ret_df, pos_df, dates, method=optimizer_name
)
engine.run_backtest(data_map, signal_map, codes, optimizer=opt_func)

# CLI — 直接传参
quantnodes-research engine run-backtest \
  --workspace ./workspace --strategy my_strategy \
  --signal-engine ./signal_engine.py \
  --optimizer risk_parity
```

---

## 7. Runner 流程

```python
def main(run_dir: Path):
    # 1. 安全校验
    safe_run_dir(run_dir)

    # 2. 读取 + 验证配置
    config = load_config(run_dir)
    BacktestConfigSchema(**config)  # Pydantic

    # 3. 加载 signal_engine.py
    _validate_signal_engine_source(file_path)  # AST guard
    signal_cls = load_module(file_path).SignalEngine
    _validate_signal_engine_class(signal_cls)   # 接口校验

    # 4. 从 DuckDB 加载数据
    data_map = load_ohlcv_data(workspace, strategy_name, codes, dates)

    # 5. OHLC 校验
    data_map = _sanitize_data_map(data_map)

    # 6. 创建市场引擎
    engine = _create_market_engine(config, codes)

    # 7. 运行
    metrics = engine.run_backtest(config, signal_cls, data_map, run_dir)

    # 8. 写 run_card + artifacts
    write_run_card(run_dir, config, metrics)
    write_artifacts(run_dir, engine)
```

---

## 8. AST Guard 增强

在现有 `sandbox.py` 基础上，为 `signal_engine.py` 增加：

| 检查项 | 说明 |
|---|---|
| 禁止 decorators | 函数/类上不允许装饰器 |
| 禁止非字面量默认值 | `def f(x=[])` 被拒绝 |
| 禁止 unsafe annotations | 仅允许 Name/Attribute/Constant/Subscript |
| 禁止 class keywords | `class Foo(metaclass=...)` 被拒绝 |
| 禁止可执行 class body | 仅允许函数/常量赋值/pass |
| 禁止循环导入 | `from signal_engine import ...` 被拒绝 |

---

## 9. 向后兼容

| 旧路径 | 新路径 |
|---|---|
| `quantnodes-research run` | `quantnodes-research backtest run` |
| `run_strategy(subprocess)` | `BaseEngine._execute_bars()` |
| `parse_run_log(8 keys)` | `calc_metrics(17 keys)` |
| close-only panel | full OHLCV DataFrames |
| 无市场规则 | 9 个市场引擎 |

旧路径完全保留，新路径独立。两者可共存。

---

## 10. 测试策略

| Phase | 测试数 | 覆盖 |
|---|---|---|
| Phase 1: 数据层 | 18 | load_ohlcv_data + roundtrip |
| Phase 2: BaseEngine | 20 | 执行循环 + rebalance + equity |
| Phase 3: 市场引擎 | 24 | 9 个引擎 |
| Phase 4: Runner | 20 | config + AST + routing |
| Phase 5: 辅助 | 10 | artifacts + metrics |
| Phase 6: Optimizer | 17 | 5 个优化器 + edge cases |
| **总计** | **109** | — |

---

## 11. 依赖

```toml
[project.optional-dependencies]
engine = [
    "pydantic>=2.5",   # config validation
    "QuantOPT>=0.1.1", # portfolio optimizers
]
# 无新核心依赖；所有市场引擎纯 Python + numpy/pandas
```
