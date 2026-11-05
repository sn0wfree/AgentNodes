# W4 — Backtest models 三合一

> 2026-09-29 · 消除 Position/TradeRecord/BacktestResult 多源

## 现状

| 模块 | 内容 |
|---|---|
| `core/utils/backtest_models.py` | Position / TradeRecord / EquitySnapshot **（真源）** |
| `core/backtest_models.py` | BacktestResult |
| `core/engine/models.py` | re-export utils |

## 方案

**唯一真源** → `core/utils/backtest_models.py`（含 BacktestResult）

```text
utils/backtest_models.py   ← 唯一定义
core/backtest_models.py    ← re-export BacktestResult（兼容）
engine/models.py           ← re-export Position/TradeRecord/EquitySnapshot（已有）
```

## 验收

```text
pytest tests/test_backtest_models.py tests/test_backtest_models_unified.py tests/test_engine_models.py -q
```
