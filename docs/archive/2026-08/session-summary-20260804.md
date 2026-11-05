# Session Summary: AEGIS Implementation + ReAct Self-Healing Fix

**Date**: 2026-08-04
**Commit**: `76577c4`

## 1. AEGIS Implementation (S1-S6)

### 1.1 Data Layer (S1)
- **`study_rounds` table** in `goals.db` — records each study round's metrics, verdict, agent outputs
- **`goal_journal` table** — records hypothesis→attribution→outcome for each round
- `StudyRoundRecord` model in `study/models.py`
- `JournalEntry` model in `goal/models.py`
- CRUD operations in `study/store.py` and `goal/store.py`

### 1.2 Pure Logic Modules (S2)
- **`study/attribution.py`** — `classify_attribution()` maps predicted_affected × prev_passed × passed_now to {reverted, still_F, flipped, novel}
- **`goal/scoreboard.py`** — `LeverScoreboard` tracks per-lever precision with Beta posterior + fatigue detection (3-round window)

### 1.3 AEGIS Runner (S3)
- **`study/runner.py`** — `AutoresearchRunner` replaces `AutoresearchExecutor`
  - Splits `run_research_round` into 3 phases: researcher → execution → evaluation
  - Injects AEGIS hooks between phases:
    - **Novelty Gate**: `goal_store.check_novelty()` after researcher
    - **Regression Gate**: `goal_store.check_regression()` after evaluation
    - **Early-stop**: precision stagnation detection (3 rounds no improvement)
    - **Scoreboard**: `LeverScoreboard.update()` after each round

### 1.4 Wiring (S4)
- `scheduler.py` uses `AutoresearchRunner` instead of `AutoresearchExecutor`
- `/study/start` honors `executor_type` (default: autoresearch)
- `chat.py` `--executor` flag
- `AutoresearchExecutor = AutoresearchRunner` backward-compat alias

### 1.5 Documentation (S5)
- `docs/archive/undated/aegis-implementation-plan.md` — complete rewrite
- `docs/archive/undated/harnessx-research.md` — added implementation status

### 1.6 Tests (S6)
- `tests/test_attribution.py` — 13 tests
- `tests/test_scoreboard.py` — 12 tests
- `tests/test_journal.py` — 15 tests
- `tests/test_study_rounds.py` — 8 tests
- `tests/test_runner_aegis.py` — 8 tests
- **160 total tests passing**

---

## 2. ReAct Self-Healing Fix

### 2.1 Root Cause Analysis

**Why autoresearch didn't execute ReAct self-healing:**

1. **`compute_factor` swallowed all errors** (compute_factor.py:1206-1213)
   ```python
   except Exception as e:
       print(f"⚠️ 因子计算失败 ({factor_code}): {e}")
       return pd.Series(dtype=float)  # ← error lost forever
   ```
   - Detailed errors (`无法解析表达式`, `无法解析参数: returns`) printed to stdout
   - LLM only saw "factor produced no non-null values" — **non-actionable**

2. **`prepare.py` only provided `close` column** (prepare.py:118-120)
   ```python
   asset_prices.columns = ["close"]  # ← only close!
   ```
   - LLM generated expressions with `volume`, `returns`, `open` → all failed
   - LLM had no way to know only `close` was available

3. **No backtest→agent feedback loop**
   - prepare.py silently dropped failed factors
   - Agent saw "discard" but didn't know which expressions failed or why

4. **`factor_analyst.md` didn't document constraints**
   - No mention of available columns
   - No mention of available operators
   - No instruction to validate expressions first

5. **`parse_failed` triggered full agent restart**
   - `retry_agent_spawn` restarted entire agent on parse failure
   - Lost all ReAct context, new agent made same mistakes

### 2.2 Fixes Implemented

| Fix | File | Change |
|-----|------|--------|
| **FactorComputeError** | `compute_factor.py` | New exception class with `available_columns` + `available_operators` |
| **compute_factor raises** | `compute_factor.py` | Catches `ValueError`, raises `FactorComputeError` with actionable info |
| **Tool returns actionable** | `builtin_tools/__init__.py` | `ComputeFactorTool` + `FactorAnalysisTool` catch `FactorComputeError` and return structured error |
| **Prompt constraints** | `factor_analyst.md` | Added "可用列 (close only)" + "可用算子" + "验证步骤" |
| **Factor failures collected** | `prepare.py` | `compute_factors()` collects failures, writes `factor_failures.json` |
| **Backtest reads failures** | `backtest.py` | `run_backtest_script` reads `factor_failures.json`, includes in result |
| **Feedback injected** | `autoresearch.py` + `runner.py` | `factor_failures` injected into `current_state` for next round's researcher |
| **No full restart** | `autoresearch.py` | `retry_agent_spawn` returns `parse_failed` info instead of restarting |

### 2.3 Key Principle

**ReAct self-healing requires actionable error feedback.**

Before: LLM called `compute_factor("ts_std(returns, 20)")` → tool returned "factor produced no non-null values" → LLM had no idea why → couldn't self-correct.

After: LLM calls `compute_factor("ts_std(returns, 20)")` → tool returns:
```
Factor 'ts_std(returns, 20)' failed: 无法解析参数: returns
Available columns: ['close']
Available operators (sample): ['ts_return', 'ts_std', 'ts_mean', ...]
```
→ LLM knows `returns` doesn't exist, only `close` available → changes to `ts_std(close, 20)` → success.

---

## 3. Data Quality Fix

### 3.1 Duplicate Date Bug
- DuckDB `ohlcv` table has duplicate `date+asset` combinations
- `set_index("date")` created duplicate index labels
- `pd.concat` failed with `ValueError: cannot reindex on an axis with duplicate labels`

**Fix**: Added `drop_duplicates(subset=["date"], keep="last")` before `set_index("date")` in all 9 locations in `builtin_tools/__init__.py`.

---

## 4. Files Changed

### New Files
| File | Description |
|------|-------------|
| `core/study/runner.py` | AutoresearchRunner (AEGIS-powered) |
| `core/study/attribution.py` | Attribution classifier |
| `core/goal/scoreboard.py` | Lever scoreboard |
| `tests/test_attribution.py` | 13 tests |
| `tests/test_scoreboard.py` | 12 tests |
| `tests/test_journal.py` | 15 tests |
| `tests/test_study_rounds.py` | 8 tests |
| `tests/test_runner_aegis.py` | 8 tests |
| `docs/archive/undated/react-selfhealing-fix.md` | Root cause analysis |

### Modified Files
| File | Changes |
|------|---------|
| `core/compute_factor.py` | FactorComputeError + compute_factor raises |
| `core/agent/builtin_tools/__init__.py` | drop_duplicates + FactorComputeError handling |
| `core/agent/loop.py` | Tool-level retry (removed — fixed upstream) |
| `core/autoresearch.py` | Factor feedback + parse_failed handling |
| `core/backtest.py` | Read factor_failures.json |
| `templates/prepare.py` | Collect factor failures |
| `templates/.prompts/factor_analyst.md` | Document constraints |
| `core/study/scheduler.py` | Use AutoresearchRunner |
| `core/study/models.py` | EARLY_STOPPED + StudyRoundRecord |
| `core/study/store.py` | study_rounds CRUD |
| `core/goal/models.py` | JournalEntry |
| `core/goal/store.py` | goal_journal CRUD + gates |
| `docs/archive/undated/aegis-implementation-plan.md` | Full rewrite |
| `docs/archive/undated/harnessx-research.md` | Implementation status |

---

## 5. Test Results

```
160 passed in 1.44s
```

All existing tests + 56 new AEGIS tests passing.

---

## 6. Next Steps

1. **Restart server** to load new code
2. **Run a study** to verify ReAct self-healing works end-to-end
3. **Monitor** factor_analyst agent to confirm LLM uses actionable feedback
4. **Optional**: Frontend Study tab with round history + journal/scoreboard display
