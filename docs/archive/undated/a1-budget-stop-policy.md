# A1 — 三维预算 StopPolicy 设计

> 2026-09-29 · Agent Runtime 控制面第一刀  
> 状态: Design → Implementation

---

## 1. 问题

当前停止条件只有 `max_iterations` / `no_progress` / `timeout`。

| 缺口 | 后果 |
|---|---|
| 无 token 预算 | thinking + 工具风暴可耗尽上下文/账单 |
| 无费用预算 | 长 Study 轮次无硬上限 |
| 时间与费用不进 Policy | StopStep 是 stub，语义散落在 loop 各处 |

目标：**预算成为一等停止原因**，与 `stop | max_iter | no_progress | timeout` 并列。

---

## 2. 语义

```text
finished_reason ∈ {stop, max_iter, no_progress, timeout, budget, error}
```

三维任一超限 → `should_stop=True`, `stop_reason="budget:tokens|cost|time"`。

| 维 | 累计量 | 上限字段 | 判定 |
|---|---|---|---|
| Token | Σ usage.total_tokens（无则 prompt+completion） | `budget_max_tokens` | `used >= max` |
| Cost | Σ tokens × price（可选） | `budget_max_cost_usd` | `cost >= max`（无单价则跳过） |
| Time | `time.perf_counter() - t0` | `budget_max_seconds` | `elapsed >= max` |

- **None = 不限**；显式 0 = 也不限（避免误配立刻停）。
- 预算在 **每次 LLM call 结束后** 结算，下一轮 Stop 评估即可见。
- 单次 call 即超限 → 仍记录本轮 usage，然后停（诚实记账）。

---

## 3. 接口

### LoopConfig（新增）

```python
budget_max_tokens: int | None = None
budget_max_cost_usd: float | None = None
budget_max_seconds: float | None = None
# 可选：用于费用估算（USD / 1M tokens）
price_per_mtok_input: float | None = None
price_per_mtok_output: float | None = None
```

### LoopContext（新增）

```python
tokens_used: int = 0
cost_usd: float = 0.0
budget_stop: bool = False
```

### BudgetStopStep

取代 `DefaultStopStep` 的空实现；仍兼容 `ctx.should_stop`。

```python
def evaluate(ctx) -> (should_stop, reason):
    if ctx.should_stop: return True, ctx.stop_reason
    if budget_exceeded(ctx): return True, "budget:..."
    return False, None
```

### AgentLoop

- `response.usage` → `ctx.tokens_used += ...` / `cost_usd += ...`
- `finished_reason="budget"` when policy stops

---

## 4. 计费（最小可用）

```text
cost += prompt_tokens/1e6 * price_in + completion_tokens/1e6 * price_out
```

- 单价未配 → 不做 cost 判定（只记 tokens）。
- 不引入完整 price table（后续 model_catalog 可挂）。

---

## 5. 测试

1. 未设预算 → 不触发
2. tokens 超限 → `budget:tokens`
3. time 超限（mock clock）→ `budget:time`
4. cost 超限（配单价）→ `budget:cost`
5. usage 累计正确
6. 0 / None 均不限
7. 兼容 `ctx.should_stop` 外部打断

---

## 6. 非目标

- 预算在 tool 级细粒度分摊
- 跨轮 Study 总预算（属 Study Budget，已存在）
- 真实 tokenizer（仍用 usage 字段，不估算）

---

## 7. 验收

```text
pytest tests/test_agent_budget.py -q
```
