# ReAct 自愈机制修复方案

## 背景

autoresearch 流程中 factor_analyst 生成的因子表达式在 backtest 阶段大量失败，
但 agent 无法从失败中学习（ReAct 自愈失效），导致：
- 同样的无效表达式被反复生成
- agent 最终输出非合法 JSON → `parse_failed` → 整个 agent 重启（丢失上下文）
- study 达不到 metric targets → discard

## 根因分析

### 根因 1：compute_factor 吞掉所有错误

**文件**: `src/strategy_research/core/compute_factor.py:1206-1213`

```python
def compute_factor(factor_code, prices, factor_name=""):
    try:
        result = evaluate_expression(factor_code, prices)
        return result
    except Exception as e:
        print(f"⚠️  因子计算失败 ({factor_code}): {e}")
        return pd.Series(dtype=float)  # ← 吞掉错误，返回空 Series
```

- 所有 parse 错误（`无法解析表达式`、`无法解析参数: returns`、`未知算子`）都被 catch
- 详细错误只 print 到 stdout，**永远不返回给调用方**
- 工具拿到空 Series → 返回 `"factor produced no non-null values"` 给 LLM — **不可操作**

### 根因 2：prepare.py 只传 close 列

**文件**: `src/strategy_research/templates/prepare.py:118-120`

```python
for asset in prices.columns:
    asset_prices = prices[[asset]].copy()
    asset_prices.columns = ["close"]   # ← 只有 close！
    fv = compute_factor(code, asset_prices)
```

- prepare.py 的 `compute_factors()` 只传一个 `close` 列给 compute_factor
- LLM 生成的表达式用了 `volume`、`returns`、`open` 等 → 全部失败
- LLM 完全不知道只有 close 可用

**实证**:
```
ts_mean(volume, 20) / ts_mean(volume, 60) - 1 → ⚠️ 无法解析表达式 → len=0
ts_std(returns, 20) → ⚠️ 无法解析参数: returns → len=0
ts_std(close, 20) → ✅ len=100, non_null=81
```

### 根因 3：无 backtest → agent 反馈回路

- prepare.py 行 130: `print(f"⚠️ 因子 {name} 计算失败: {e}")` — 静默丢弃
- backtest 跑空因子 → metrics 差 → verdict="discard"
- agent 看到 "discard" 但**不知道哪些表达式失败、为什么失败**
- 没有机制把 prepare.py 的因子错误信息注入 agent 的 ReAct 上下文

### 根因 4：factor_analyst prompt 未告知约束

**文件**: `templates/.prompts/factor_analyst.md`

prompt 里只说 "组合 285 个算子"，**从未说明**：
- prepare.py 只提供 `close` 列（不能用 `volume`、`returns`、`open`）
- 可用算子完整列表
- 应先用 `compute_factor` 工具验证表达式再提交

### 根因 5：factor_analysis 工具的 ValueError（已修复）

**文件**: `src/strategy_research/core/agent/builtin_tools/__init__.py:871`

```python
aligned = pd.concat([factor_series, asset_df["fwd_ret"]], axis=1).dropna()
# ValueError: cannot reindex on an axis with duplicate labels
```

DuckDB ohlcv 表有重复 date+asset 组合 → set_index("date") 产生重复索引 → pd.concat 失败。
**已在本轮修复**: 所有 set_index("date") 前加 drop_duplicates(subset=["date"], keep="last")。

### 根因 6：parse_failed 触发全量重启

**文件**: `src/strategy_research/core/autoresearch.py:249-269`

```python
def retry_agent_spawn(spawn_fn, agent_name, max_retries=3):
    for attempt in range(max_retries):
        raw_output = spawn_fn()
        parsed = parse_agent_output(raw_output)
        if "error" not in parsed:
            return parsed
        # 解析失败 → 重试整个 agent
```

agent 最终输出不是合法 JSON → `retry_agent_spawn` 重启**整个 agent** → 丢失所有 ReAct 上下文 → 新 agent 犯同样错误。

## ReAct 自愈失效的机制

```
factor_analyst agent (ReAct loop)
  ├─ LLM 生成表达式: ts_std(returns, 20)
  ├─ 调用 compute_factor 工具
  │   └─ compute_factor() 吞掉错误 → 返回空 Series
  │   └─ 工具返回: "factor produced no non-null values"
  ├─ LLM 看到通用错误，不知道 returns 不存在、不知道只有 close
  ├─ LLM 可能换个表达式: ts_mean(volume, 20) / ts_mean(volume, 60) - 1
  ├─ 同样失败 → 同样通用错误
  ├─ LLM 最终输出非合法 JSON
  └─ parse_failed → 重启整个 agent → 重复
```

**核心问题**: ReAct 自愈需要 actionable error feedback。当前错误要么被吞掉，
要么返回的是不可操作的通用消息。LLM 没有足够的信息来自我纠正。

## 修复方案

### Fix 1: compute_factor 不再吞错误

**文件**: `src/strategy_research/core/compute_factor.py`

**改动**: compute_factor() 不再 catch 所有异常，改为 raise 带结构化信息的错误。

```python
class FactorComputeError(Exception):
    """结构化因子计算错误，包含可操作信息。"""
    def __init__(self, factor_code, reason, available_columns, available_operators):
        self.factor_code = factor_code
        self.reason = reason
        self.available_columns = available_columns
        self.available_operators = available_operators
        msg = (
            f"Factor '{factor_code}' failed: {reason}\n"
            f"Available columns: {available_columns}\n"
            f"Available operators (sample): {available_operators[:20]}"
        )
        super().__init__(msg)

def compute_factor(factor_code, prices, factor_name=""):
    result = evaluate_expression(factor_code, prices)  # 不再 catch
    if factor_name:
        result.name = factor_name
    return result
```

### Fix 2: ComputeFactorTool 返回 actionable 错误

**文件**: `src/strategy_research/core/agent/builtin_tools/__init__.py`

**改动**: ComputeFactorTool.execute 捕获 FactorComputeError 并返回结构化错误。

```python
try:
    series = compute_factor(factor_code, asset_df, factor_name=factor_name)
except FactorComputeError as exc:
    return err_actionable(
        str(exc),
        received=factor_code,
        fix=f"Use only these columns: {exc.available_columns}. "
            f"Sample valid expressions: ts_return(close, 20), ts_std(close, 20)",
        tool="compute_factor",
    )
except Exception as exc:
    return err_actionable(f"compute failed: {exc}", ...)
```

### Fix 3: prepare.py 传全量 OHLCV 列

**文件**: `src/strategy_research/templates/prepare.py`

**改动**: 不再只传 close，传全量 OHLCV 列。

```python
# Before:
asset_prices.columns = ["close"]

# After:
# asset_prices 已经有 open, high, low, close, volume 列，直接传入
```

但 prepare.py 的 prices 是 wide-format（columns=assets），只有一列 close。
需要改为从 DuckDB 读取全量 OHLCV 数据，或者在 prepare.py 中也加 drop_duplicates。

**方案 A** (推荐): 修改 prepare.py 的 compute_factors，从 DuckDB 读全量 OHLCV。
**方案 B**: 修改 prepare.py 的 compute_factors，对每个 asset 传 close+volume（至少支持 volume 表达式）。
**方案 C**: 在 factor_analyst prompt 中明确约束只能用 close 列。

### Fix 4: factor_analyst prompt 增加约束说明

**文件**: `templates/.prompts/factor_analyst.md`

**改动**: 增加以下内容：

```markdown
## 因子表达式约束

### 可用列 (prepare.py backtest 上下文)
- `close` — 收盘价（必选，唯一可靠列）
- 注意: backtest 的 prepare.py 只传 close 列，不能使用 volume, returns, open 等列

### 可用算子 (部分列表)
时序: ts_return, ts_std, ts_mean, ts_sum, ts_max, ts_min, ts_corr, ts_rank,
      ts_skew, ts_kurt, ts_median, ts_var, ts_argmax, ts_argmin
截面: rank, zscore, safe_div
数学: abs, log, sign, power

### 验证步骤
1. 先用 compute_factor 工具验证表达式是否可计算
2. 确认返回 non-null 值后再提交
3. 表达式格式: ts_return(close, 20), ts_std(close, 20), rank(ts_return(close, 20))
```

### Fix 5: backtest → agent 反馈回路

**文件**: `src/strategy_research/core/autoresearch.py`

**改动**: run_execution_phase 返回 factor 计算失败信息，注入 agent 的下一轮上下文。

```python
# 在 run_execution_phase 中收集因子失败信息
factor_failures = []
for expr in factor_exprs:
    try:
        compute_factor(expr["factor_code"], prices)
    except Exception as e:
        factor_failures.append({"factor": expr, "error": str(e)})

# 注入 summary
if factor_failures:
    summary["factor_failures"] = factor_failures
```

### Fix 6: parse_failed 不再全量重启

**文件**: `src/strategy_research/core/autoresearch.py`

**改动**: retry_agent_spawn 中，parse_failed 时追加 corrective 消息，而不是重启。

```python
# Before: 全量重启
# After: 追加 corrective 消息让 loop 继续

if "error" in parsed:
    # parse_failed: 不重启，而是返回带错误信息的 dict
    return {
        "error": "parse_failed",
        "raw_output": raw_output[:500],
        "hint": "Output must be valid JSON matching the required schema",
    }
```

## 实施优先级

| 优先级 | Fix | 难度 | 效果 |
|--------|-----|------|------|
| P0 | Fix 1: compute_factor 不吞错误 | 低 | 高 — 所有下游错误变 actionable |
| P0 | Fix 2: ComputeFactorTool 返回 actionable 错误 | 低 | 高 — LLM 看到具体错误 |
| P0 | Fix 4: prompt 增加约束说明 | 低 | 高 — 预防 > 自愈 |
| P1 | Fix 3: prepare.py 传全量列 | 中 | 中 — 减少因子失败 |
| P1 | Fix 5: backtest→agent 反馈 | 中 | 中 — agent 知道失败原因 |
| P2 | Fix 6: parse_failed 不全量重启 | 低 | 低 — 减少 agent 重启开销 |

## 验证方法

1. 修复后，运行 autoresearch study，检查 factor_analyst 的 agent 输出
2. 验证 LLM 能从 actionable error feedback 中自愈：
   - LLM 调用 compute_factor("ts_std(returns, 20)") → 看到 "returns not in available columns: [close]"
   - LLM 调整为 ts_std(close, 20) → 成功
3. 验证 backtest 中因子计算不再静默失败
4. 验证 agent 不再因 parse_failed 而全量重启

## 相关文件

- `src/strategy_research/core/compute_factor.py` — 因子表达式解析器 + compute_factor 函数
- `src/strategy_research/core/agent/builtin_tools/__init__.py` — ComputeFactorTool + FactorAnalysisTool
- `src/strategy_research/templates/prepare.py` — backtest 因子计算
- `src/strategy_research/templates/.prompts/factor_analyst.md` — agent prompt
- `src/strategy_research/core/autoresearch.py` — autoresearch 主循环 + retry_agent_spawn
