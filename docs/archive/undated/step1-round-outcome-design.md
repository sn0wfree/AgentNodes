# Step 1 设计：RoundOutcome 类型契约

> 计划来源：`docs/archive/2026-10/phase-engine-refactor-plan-20261008.md` §3 Step 1
> 范围约束：**仅改 `src/agentnodes/core/study/phase_engine.py`**；`run_round_phases(...)` 对外签名不变；不动 aegis / runner / langgraph_engine / autoresearch
> 状态：设计定稿，待实施

## 1. 问题回顾

两分支（langgraph / phases）与共享 finalization 之间靠**同名局部变量约定**衔接：

```
分支 A (langgraph, line 143-205)          分支 B (phases, line 207-285)
  researcher_output ────────┐               researcher_output ────────┐
  metrics ──────────────────┤               metrics ──────────────────┤
  verdict ──────────────────┤               verdict ──────────────────┤
  eval_result（shim）────────┤               eval_result（原生全量）────┤
  exec_result（shim）────────┤               exec_result（原生）────────┤
  agent_outputs ────────────┤               agent_outputs ────────────┤
  ...                       │               ...                       │
                            ▼               ▼
                共享 finalization (line 287-415) 按局部变量名消费
```

Step 0 的 bug（`261a88f`）正是此约定的代价：langgraph 分支 line 205 在 phase-A capture 之后把 `captured_predictions` 重置为 `{}`，phase-B backfill 饿死——变量名相同但值的生命周期没有类型约束。

## 2. 事实基础（读码确认，2026-10-08）

### 2.1 共享 finalization 实际消费的分支产出（line 287-415）

| 字段 | shared 引用数 | 用于 |
|---|---|---|
| `metrics` | 9 | gate 检查、e2、results.tsv、manifest、state、journal |
| `verdict` | 10 | gate 可改写为 `discard`、e2、summary、manifest、keep-evidence |
| `agent_outputs` | 4 | summary、topology、max_iter 短路、return |
| `eval_result` | 3 | `decision.to_dict()` ×2、`_verdict_reason` |
| `strategist_output` | 5 | lever、journal、manifest strategy_changes |
| `hypothesis` | 3 | journal、manifest、review |
| `predicted_affected` | 3 | attribution、journal、manifest |
| `captured_predictions` | 1 | journal phase-B（Step 0 修复点） |
| `backtest_error` | 1 | return dict |

**零消费**：
- `exec_result` —— shared 段 0 引用（只在分支内使用）→ **langgraph 的 `exec_result` shim 可删**
- `researcher_output` —— shared 0 引用，也不在 return dict 中（只在分支内拼 `agent_outputs` / `hypothesis` / claims）→ 不进契约

### 2.2 `_verdict_reason`（aegis.py:50）只读两个键

`eval_result["decision"]`（`.reason`）与 `eval_result["aoa_llm_verdict"]` —— 与 langgraph shim 的最小面完全一致；phases 的 `run_evaluation_phase` 返回全量 dict（超集）。**契约保留 `eval_result: dict` 整字段**（方案 B），避免改 `aegis.py` 签名（范围外文件）。

### 2.3 提前返回路径（契约不覆盖，保持原样）

- langgraph：`paused_for_approval` → `return lg_result`；`aborted` → `return lg_result`
- phases：novelty 拒绝 → `return {"aborted": True, ...}`

这些路径**绕过** shared finalization，不属于契约面。

## 3. 契约定义

```python
@dataclass
class RoundOutcome:
    """What each engine branch must hand to the shared finalization.

    Replaces the implicit same-local-name convention between the
    langgraph/phases branches and the shared finalization. Required
    fields are enforced at construction, so a branch can no longer
    shadow or reset a value between capture and consumption (the
    phase_engine:205 defect fixed in 261a88f).
    """
    metrics: dict
    verdict: str                       # mutable: guidance gate may set "discard"
    eval_result: dict                  # phases native / langgraph {decision, aoa_llm_verdict}
    agent_outputs: dict
    strategist_output: dict
    hypothesis: str
    predicted_affected: list
    captured_predictions: dict
    backtest_error: str | None
```

9 字段，与 §2.1 消费表一一对应。默认值一律不给（构造即校验完整性）。

## 4. 改造点（全部在 phase_engine.py）

| # | 位置 | 改动 |
|---|---|---|
| 1 | 文件顶部（imports 后） | 新增 `RoundOutcome` dataclass（~20 行含 docstring） |
| 2 | langgraph 分支末尾（原 line 200-205 shim 区） | 构造 `outcome = RoundOutcome(...)`；**删除 `exec_result` shim**；`eval_result` shim 保留（收编进契约） |
| 3 | phases 分支末尾（line 285 后） | 构造 `outcome = RoundOutcome(...)`（`exec_result` 留作分支内局部变量） |
| 4 | shared 段 line 287-415 | 9 个字段的 ~30 处引用改为 `outcome.<field>`；`verdict` 的 gate 改写变 `outcome.verdict = "discard"` |
| 5 | return dict（line 404-415） | `eval_result["decision"]` → `outcome.eval_result["decision"]` 等 |

**不改**：提前返回路径、所有 helper 函数、`run_round_phases` 签名、其他文件。

**C901 预期**：分支体缩短 + shim 删除，24 → 目标 ≤18（不追求一次 ≤15，拆函数是 Step 2）。

## 5. 验收标准

| # | 项 | 通过条件 |
|---|---|---|
| 1 | 定向测试 | `test_phase_engine_langgraph_{branch,edge_cases,claims}` + `test_langgraph_parity` + `test_phase_engine_researcher_defense` + `test_study_engine_dispatch` 全绿 |
| 2 | 69 文件回归 | 0 failed（基线 1273 passed） |
| 3 | C901 | `run_round_phases` ≤18（现 24） |
| 4 | **新增 side-effect 测试** | 两分支构造后契约字段集一致 + journal phase-B 收到的 predictions == phase-A capture（Step 0 测试作为该契约的回归锚） |
| 5 | ruff | 改动文件无新增告警 |

新增测试文件：`tests/test_phase_engine_round_outcome.py`——验证 ① dataclass 缺字段时构造报错（契约强制性）② 两分支产出的 `RoundOutcome` 字段集相同（防未来某分支漏字段）。

## 6. 风险与回滚

- **风险**：shared 段 ~30 处引用替换为机械改写，漏一处 → NameError（ruff F821 可扫）
- **缓解**：替换后必跑 `ruff check --select F821` + 定向测试；Step 0 的 claims 测试锁住最高危路径
- **回滚**：单 commit，`git revert` 即回变量约定时代（行为等价，无数据格式变化）

## 7. 与 Step 2 的边界

- Step 1 **不抽函数**、**不改分派逻辑**、**不碰 `engine='dag'` / env 归一化**（line 128-141 原样）
- Step 2 在契约之上做查表分派——届时分支体已有 `RoundOutcome` 返回类型，查表只需 `Callable[..., RoundOutcome]`


## 8. 验收记录（2026-10-08，实施完成）

| # | 项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | 定向测试（phase/langgraph/claims/defense/dispatch） | 全绿 | 36 passed | ✅ |
| 2 | 69 文件回归 | 0 failed | **1277 passed / 0 failed**（基线 1273 + 4 新契约测试） | ✅ |
| 3 | C901 | ≤18 | **24 → 24（预期错误）** | ⚠️ 见下 |
| 4 | 契约测试 | 新文件 | 	ests/test_phase_engine_round_outcome.py 4 passed（字段集 / 无默认 / 缺字段 TypeError / verdict 可变）+ Step 0 claims 测试作回归锚 | ✅ |
| 5 | ruff | 无新增 | F821 清零；仅剩 C901（pre-existing 数字） | ✅ |

### C901 预期错误的说明

设计时预期「分支体缩短 + shim 删除 → 24→≤18」。实际：契约化只改**值的传递方式**（局部变量 → dataclass 字段），分支结构与条件数完全不变——cyclomatic complexity 数的是分支不是变量传递，因此 24 不动是正确结果。

**降复杂度必须抽分支函数**（把 langgraph / phases 分支体移出 
un_round_phases），这正是 plan §7 划给 Step 2 的边界（查表分派天然需要 Callable[..., RoundOutcome]）。C901 ≤15 的目标**顺延至 Step 2 验收**。

### 实施中的波折（记录备查）

批量替换脚本首轮误伤 5 处：erdict_reason 函数名被部分匹配、return dict 的 key 被加前缀、gate 赋值被 lookahead 排除。已全部修复并经 F821 + 全量回归验证。教训：**shared 段的标识符替换必须排除 dict key 与 kwargs 名**，脚本用后需逐行审计 diff。

### Step 1 达成的实质

- 分支产出从「约定同名」变「构造必填」——漏字段在构造处 TypeError，而不是 shared 段运行到一半 NameError
- exec_result shim 已删（shared 零消费证实）
- captured_predictions 的生命周期由 dataclass 字段承载，Step 0 那类「中间重置」在结构上不可表达
- Step 2 的前置就位：两个分支已产出统一类型，查表分派只差 Callable[..., RoundOutcome] 签名
