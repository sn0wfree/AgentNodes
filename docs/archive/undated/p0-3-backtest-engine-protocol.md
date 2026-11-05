# P0-3 BacktestEngine Protocol

> **Status:** Completed (branch `p0-3-backtest-engine-protocol`, merged to `main` as `7921bb8`)
> **承接:** P0-2 统一了 `BacktestResult` dataclass（`core/backtest_models.py`）。
> 本步把 `BacktestEngine` + `Strategy` 收敛成 Protocol，让 YAML / Callback 路径走同一接口。

## 完成状态

| 步骤 | 标题 | 状态 | 提交 |
|------|------|------|------|
| 母文档 | 设计 | ✅ | `cc9b540` |
| E1 | Protocol (Strategy + BacktestEngine) | ✅ | `d2e3bd5` |
| E2 | StrategyEngineAdapter | ✅ | `d2e3bd5` |
| E3 | CallbackEngineAdapter | ✅ | `d2e3bd5` |
| E4 | Factory + Registry | ✅ | `d2e3bd5` |
| E5 | 测试 (11 个) | ✅ | `d2e3bd5` |
| 合并 | merge commit | ✅ | `7921bb8` |

**测试**：11 个 P0-3 新增 + 315 个回归（合计 326）全绿。
**ruff**：P0-3 新增文件 0 错误。

## 目标

定义 `BacktestEngine` Protocol + `Strategy` Protocol + 三套路径适配器：

```python
class Strategy(Protocol):
    """单一策略接口（合并 BaseStrategy + FactorStrategy + 未来 Callback）。"""
    def compute_weights(
        self, date, price_panel, nav_history,
    ) -> dict[str, float]: ...
    def on_risk_check(
        self, weights, nav_history, date,
    ) -> dict[str, float]: ...

class BacktestEngine(Protocol):
    def run(
        self, *, strategy: Strategy, price_panel: pd.DataFrame,
        config: BacktestConfig | None = None,
    ) -> BacktestResult: ...
```

## 三套路径收敛

| 路径 | 当前入口 | 适配方式 |
|------|---------|----------|
| **YAML** | `core/utils/strategy_engine.py::StrategyEngine.run(strategy)` | 已是 `Strategy` Protocol（`compute_weights` 签名一致） |
| **Callback** | `core/utils/backtest_engine.py::run_backtest(callbacks)` | 加 `CallbackEngineAdapter`：把 `BacktestCallbacks` 包成 `Strategy` |
| **Engine runner** | `core/engine/runner.py::BaseEngine.run_backtest()` | 返回 `Dict[str, Any]`（metrics only）—— v0.1 不在 P0-3 范围 |

## 实施步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| E1 | `core/backtest_engine/protocol.py` — `Strategy` + `BacktestEngine` Protocol | runtime_checkable 通过 |
| E2 | `core/backtest_engine/strategy_engine_adapter.py` — `StrategyEngineAdapter` 把现有 `StrategyEngine` 适配成 Protocol | run() 返回统一 `BacktestResult` |
| E3 | `core/backtest_engine/callback_engine_adapter.py` — `CallbackEngineAdapter` 把 `BacktestCallbacks` 包成 `Strategy` | 端到端等价 |
| E4 | `core/backtest_engine/factory.py` — `get_engine(name=None)` 注册表 | duckdb-style registry |
| E5 | 测试 12+ 个 + 性能/等价 oracle | 195+ 现有测试全绿 |

## 文件结构

```
core/backtest_engine/
    __init__.py
    protocol.py            # Strategy + BacktestEngine Protocol
    strategy_engine_adapter.py   # 包装现有 StrategyEngine
    callback_engine_adapter.py   # 包装 BacktestCallbacks
    factory.py             # get_engine() 工厂
    (strategy_engine.py + backtest_engine.py 保留 legacy 路径)
```

## 风险

| 风险 | 缓解 |
|------|------|
| 三路径行为不一致 | Adapter 内部直接调用 legacy；195+ 现有测试即等价 oracle |
| Callback 五步法不能 1:1 表达为 compute_weights | Adapter 内部按"五步法"组装 weights；与 BaseStrategy 同协议输出 |
| 循环依赖（backtest_engine ↔ backtest_models） | Protocol 在 core 顶层；adapter 各自 import |
| Engine runner 路径不收敛 | v0.1 不动；后续 v0.2 加 BaseEngineAdapter |

## 不在 P0-3 范围

- 完整 runtime sandbox 替换（已在 P0-2.C 标记）
- Engine runner 路径三套对齐
- FactorStrategy 物理位置独立（仍是 config_runner.py 内联）