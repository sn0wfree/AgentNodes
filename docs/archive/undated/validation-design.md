# Validation Toolkit Design (P3-c / P3-d)

> Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
> See `docs/archive/undated/vibe-trading-credits.md` for the full attribution list.

## 1. Purpose

Three independent statistical checks for backtest results:

| Tool | Question it answers |
|---|---|
| **Monte Carlo permutation** | Is the observed Sharpe / max-DD significantly better than random shuffles of the same trades? |
| **Bootstrap Sharpe CI** | How stable is the Sharpe ratio under return resampling? |
| **Walk-Forward analysis** | Is performance consistent across sequential time windows? |

Each can be invoked separately or all together via `run_validation()`.

## 2. Inputs

```python
from agentnodes.core.validation import (
    run_validation, TradeInput, MarketType,
)

results = run_validation(
    config={
        "validation": {
            "monte_carlo":  {"n_simulations": 1000, "seed": 42},
            "bootstrap":    {"n_bootstrap": 1000, "confidence": 0.95, "seed": 42},
            "walk_forward": {"n_windows": 5},
        }
    },
    equity_curve=nav_daily,        # pd.Series indexed by date
    trades=list_of_completed_trades,
    initial_capital=100_000.0,
    market=MarketType.A_SHARE,
)
```

| Input | Type | Required | Notes |
|---|---|---|---|
| `config["validation"]` | dict | Yes (keys opt-in) | Each sub-key may be `True` (defaults) or a dict override |
| `equity_curve` | `pd.Series` | Yes | Indexed by date or bar |
| `trades` | `list[TradeInput]` | Optional | Monte Carlo and Walk-Forward per-window use this |
| `initial_capital` | float | Yes | Starting capital for Monte Carlo |
| `market` | `MarketType` | No (default A_SHARE) | Multi-market interface (see §4) |

## 3. Outputs

A JSON-safe dict with the requested sections:

```json
{
  "market": "a_share",
  "bars_per_year": 252,
  "monte_carlo": {
    "actual_sharpe": 1.234, "actual_max_dd": -0.18,
    "p_value_sharpe": 0.043, "p_value_max_dd": 0.012,
    "simulated_sharpe_mean": 0.45, "simulated_sharpe_std": 0.31,
    "simulated_sharpe_p5": 0.05, "simulated_sharpe_p95": 1.10,
    "n_simulations": 1000, "n_trades": 20, "bars_per_year": 252
  },
  "bootstrap": {
    "observed_sharpe": 1.234, "ci_lower": 0.42, "ci_upper": 2.05,
    "median_sharpe": 1.21, "prob_positive": 0.97,
    "confidence": 0.95, "n_bootstrap": 1000, "n_returns": 252
  },
  "walk_forward": {
    "n_windows": 5, "windows": [...], "profitable_windows": 4,
    "consistency_rate": 0.8, "return_mean": 0.04, "return_std": 0.03,
    "sharpe_mean": 0.95, "sharpe_std": 0.42
  }
}
```

All NaN / inf are coerced to `null` via `_json_safe` so the output can
be written with `allow_nan=False`.

## 4. Multi-Market Interface (Forward Contract)

### 4.1 Current State (v0.3.0)

Only `MarketType.A_SHARE` / `HK_EQUITY` / `US_EQUITY` are fully
validated. The other markets trigger a `UserWarning` and fall back to
the same algorithm — only `bars_per_year` reflects the requested
market.

```python
>>> import warnings
>>> from agentnodes.core.validation import MarketType, run_validation
>>> with warnings.catch_warnings(record=True) as w:
...     warnings.simplefilter("always")
...     results = run_validation(
...         config={"validation": {"monte_carlo": True}},
...         equity_curve=nav, trades=trades, market=MarketType.CRYPTO,
...     )
>>> assert any("not yet implemented" in str(x.message) for x in w)
```

### 4.2 Supported Markets

| Market | `bars_per_year` | Algorithm |
|---|---|---|
| A_SHARE | 252 | Full (A-share defaults) |
| HK_EQUITY | 247 | Full (HK defaults) |
| US_EQUITY | 252 | Full (US defaults) |

### 4.3 Forward Roadmap (P4+)

| Market | Required Changes |
|---|---|
| CRYPTO | 24/7 trading → 365 days/year; funding-rate adjustments; perpetual-swap liquidation model |
| FUTURES_CN | Contract multiplier; margin model; T+0; intraday re-mark |
| FUTURES_GLOBAL | Per-exchange rules (CME / ICE / Eurex); settlement handling |
| FOREX | Swap calculation (Wed ×3 rollover); 24×5 calendar; spread cost |

Each market requires:
1. Algorithm adjustments (the `monte_carlo_test` / `walk_forward_analysis`
   inputs may need normalization per market rules).
2. Per-market benchmarks (e.g. BTC-USDT for CRYPTO, HK.03100 for HK).
3. New `MarketType` rules for cost models (already partially captured
   in `core.backtest.engines`).

### 4.4 Design Rationale

The user-confirmed P3-c decision was: **plan + reserve the multi-market
interface, do not implement algorithm branches yet**. We expose the
market tag in the input + output so downstream consumers (CLI, reports,
hypothesis linking) can react to it, but the algorithm itself stays
A-share-only until P4.

## 5. TradeInput (Lightweight)

```python
@dataclass(frozen=True)
class TradeInput:
    symbol: str
    direction: int             # 1 long, -1 short
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    size: float
    pnl: float
    pnl_pct: float
    holding_bars: int = 0
    exit_reason: str = "signal"
```

This is a minimal stand-in for vibe-trading's `backtest.engines.base.TradeRecord`
(we do not depend on the full engine classes). When the project's own
backtest engine is migrated to the full vibe-trading layout, this can be
replaced by the engine's record type.

## 6. CLI

```
quantnodes-research validate-run <run_dir> [--market ...] \
    [--monte-carlo] [--n-simulations N] \
    [--bootstrap] [--n-bootstrap N] \
    [--walk-forward] [--n-windows N] \
    [--seed S]
```

Writes `<run_dir>/validation.json` with the results.

## 7. Testing

| Test file | Count | Focus |
|---|---|---|
| `test_validation_market.py` | 6 | enum + warning |
| `test_validation_trade_input.py` | 10 | TradeInput + utils + sharpe |
| `test_validation_monte_carlo.py` | 6 | MC permutation + reproducibility |
| `test_validation_bootstrap_wf.py` | 11 | Bootstrap CI + Walk-Forward |
| `test_validation_runner.py` | 7 | Orchestration + multi-market |
| `test_validation_cli.py` | 12 | `validate-run` command |
| **Total** | **52** | |

Run:

```bash
pytest tests/test_validation_*.py -v
```