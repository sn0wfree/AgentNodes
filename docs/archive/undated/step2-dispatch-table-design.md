# Step 2 设计：分派查表 + 分支函数抽取 + 引擎归一化收口

> 计划来源：`docs/archive/2026-10/phase-engine-refactor-plan-20261008.md` §3 Step 2
> 前置：Step 1 已交付 `RoundOutcome` 产出契约（`68c891f`）
> 范围约束：**仅改 `src/agentnodes/core/study/phase_engine.py`**；`run_round_phases` 对外签名不变；不碰 API/schema/models（`'dag'` 的类型注释收口另立任务）
> 状态：设计定稿，待实施

## 1. 现状（Step 1 后）

- `run_round_phases` 仍为 421 行单函数（line 52-473）：前置准备 → 引擎归一化（158）→ if/else 两分支（产出 `RoundOutcome`）→ shared finalization（341+）
- C901 = **24**（Step 1 预期 ≤18 落空：契约化不改分支结构，见 Step 1 doc §8）
- 归一化内联 3 个条件：默认 `"phases"`、`AN_STUDY_DAG_ENGINE=1` remap、`engine='dag'` remap

## 2. 关键事实：dag/env **不能删**

| 证据 | 结论 |
|---|---|
| `tests/test_study_engine_dispatch.py` 活跃测试锁定 dag→langgraph 映射与 env remap 行为 | 删映射 = 破坏绿测试 |
| `api/schemas/study.py:71`、`api/routers/study.py:225`、`core/study/models.py:172` 三处类型注释仍列 `'dag'` | 删映射 = API 层接受值与执行层不一致（breaking） |
| `docs/archive/undated/langgraph-dual-engine-design.md` / `study-subsystem-tech-debt` 记录映射是刻意安全网 | 是设计而非疏漏 |

**收口策略（本轮）**：抽 `_resolve_engine(study) -> str` 独立函数 + **remap 时 `logger.warning` 可观测化**——行为不变（测试继续绿），但每次走假选项/遗留 env 都留日志，为未来删除提供数据。

**删除（后续任务，非本轮）**：需 API/schema/models 三处同步拒绝 `'dag'` + 移除 env 分支 + 更新 2 个锁定测试 + 检查 DB 存量值迁移。

## 3. 设计

### 3.1 输入契约 `BranchContext`

```python
@dataclass
class BranchContext:
    """Everything a branch function needs from the shared preamble."""
    runner: Any
    study: Any
    round_num: int
    previous_summary: dict | None
    directive_text: str | None
    path: Path
    strategy: str
    current_state: dict
    run_dir: Path
    runs_dir: Path
    run_name: str
    results_tsv: Path
    graph: Any
    session: str
    sid: str
    metric_targets: list
    guidance: Any
```

与 Step 1 的 `RoundOutcome`（产出契约）对称：**输入契约 + 产出契约 = 分支黑盒**。分支体内的自由变量由 ruff F821 + 测试兜底查漏。

### 3.2 分支函数

```python
def _run_langgraph_branch(ctx: BranchContext) -> RoundOutcome | dict: ...
def _run_phases_branch(ctx: BranchContext) -> RoundOutcome | dict: ...
```

- 正常路径返回 `RoundOutcome`
- 提前返回（paused / aborted / novelty-rejected）返回原有 `dict` payload——主函数 `isinstance(result, dict)` 识别并直接 return（语义与现状完全一致）

### 3.3 查表分派

```python
_ENGINE_BRANCHES: dict[str, Callable[[BranchContext], RoundOutcome | dict]] = {
    "phases": _run_phases_branch,
    "langgraph": _run_langgraph_branch,
}
```

主函数：

```python
engine = _resolve_engine(study)
result = _ENGINE_BRANCHES[engine](ctx)
if isinstance(result, dict):
    return result                    # early path unchanged
outcome = result
# shared finalization ...
```

未知 engine 值：查表 `KeyError` → 捕获后回退 `"phases"` + warning（现状 `if engine == "langgraph"` 对任意非 langgraph 值都走 phases——**必须保持此回退语义**，防止 DB 里脏值炸 round）。

### 3.4 `_resolve_engine`

```python
def _resolve_engine(study: Any) -> str:
    """Normalize the study's engine to a dispatch-table key.

    Behavior-preserving extraction of the inline remaps:
      - AN_STUDY_DAG_ENGINE=1  -> langgraph (legacy env switch)
      - engine='dag'           -> langgraph (fake option; no dag executor)
    Both remaps now log a warning so the eventual removal (API/schema/
    models must stop accepting 'dag' first) can be data-driven.
    """
```

**C901 拆分预期**：3 个归一化条件随函数搬走。

### 3.5 C901 预算（主函数剩余分支）

| 来源 | 估计 |
|---|---|
| 前置准备（gap check / factor_failures / src exists ×2 等） | ~4 |
| 查表 + isinstance early | ~2 |
| shared（gate if / e2 / short-circuit if-else / keep if） | ~5-6 |
| 其他（guidance / score board hasattr） | ~2 |
| **合计** | **~13-14 ≤ 15** ✅ |

若超 15，后备：把「前置准备」也抽成 `_prepare_branch_context(...) -> BranchContext`（它天然是无分支数据准备 + 少量条件）。

## 4. 改造点清单

| # | 位置 | 改动 |
|---|---|---|
| 1 | RoundOutcome 后 | 新增 `BranchContext` dataclass |
| 2 | helpers 区 | 新增 `_resolve_engine`（3 归一化条件迁入 + warning） |
| 3 | helpers 区 | 新增 `_run_langgraph_branch` / `_run_phases_branch`（两分支体整体搬移，含 RoundOutcome 构造） |
| 4 | helpers 区 | 新增 `_ENGINE_BRANCHES` 查表 |
| 5 | `run_round_phases` | 前置准备 → 构造 ctx → 查表调用 → isinstance 分流 → shared；行数 421 → 预期 ~180 |

**不改**：shared finalization 逻辑、提前返回 payload 结构、helper 函数、对外签名、其他文件。

## 5. 验收标准

| # | 项 | 通过条件 |
|---|---|---|
| 1 | **C901** | `run_round_phases` **≤15**（Step 1 顺延目标） |
| 2 | 定向测试 | phase/langgraph/claims/defense/dispatch/round_outcome/parity 全绿（含 dag/env 两个锁定测试） |
| 3 | 69 文件回归 | 0 failed（基线 1277） |
| 4 | ruff | 改动文件 F821/F811 清零；无新增 |
| 5 | 行为等价 | `engine='dag'` 与 `AN_STUDY_DAG_ENGINE=1` 仍 remap 到 langgraph（warning 出现） |

## 6. 风险与回滚

- **搬移体量大**（两分支体 ~200 行）：靠 F821（漏传 ctx 字段）+ 36+4 个定向测试 + 69 回归三重兜底
- **回退语义陷阱**：现状对任意未知 engine 走 phases——查表实现必须保留 fallback，不能 KeyError 炸（专门测试见 §5-5 已有 dispatch 测试覆盖 dag；补一条未知值回退断言进现有 dispatch 测试类？**不动别人测试文件**——改为在新契约测试文件里补）
- **回滚**：单 commit revert

## 7. 与 Step 3 的边界

- Step 3 是 `runner._run_loop` 生命周期状态机（另一个文件），本轮不碰 `runner.py`
- 本轮产出的 `BranchContext` / 查表是纯 phase_engine 内部结构，不向 runner 泄漏

## 8. 验收记录（2026-10-08，实施完成）

| # | 项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | C901 `run_round_phases` | ≤15 | **8**（24 → 8） | ✅ 超额 |
| 2 | 定向测试 | 全绿（含 dag/env 锁定） | **42 passed** | ✅ |
| 3 | 69 文件回归 | 0 failed | **1279 passed / 0 failed**（基线 1277 + 2 新） | ✅ |
| 4 | ruff | 无新增 | 改动文件全绿（F821/F811/C901 清） | ✅ |
| 5 | 行为等价 | dag/env 仍 remap | `_resolve_engine` 行为测试 ×2 绿 | ✅ |

### 结构变化

| 指标 | 前 | 后 |
|---|---|---|
| `run_round_phases` 行数 | 421 | **184** |
| C901 | 24 | **8** |
| 文件总行数 | 862 | 1019（+ 新函数定义） |

新成员：`BranchContext`（输入契约）、`_prepare_round_preamble`、`_resolve_engine`、`_run_langgraph_branch`、`_run_phases_branch`、`_ENGINE_BRANCHES`。

### 测试适配（1 个文件，有意为之）

`tests/test_study_engine_dispatch.py` 的两个测试**锁的是源码位置而非行为**（`inspect.getsource(run_round_phases)` 断言含 "langgraph" 字符串；env 测试只 setenv 再读回、从未触碰 phase_engine）。Step 2 把 remap 搬进 `_resolve_engine` 后前者必红。已改为对 `_resolve_engine` 的**行为断言**——比字符串断言更强，且 env 测试从空测试变成真测试。行为等价由它们 + 69 回归共同保证。

### 未做（按 §2 收口策略留给后续）

- 删除 `engine='dag'` 映射：需 API/schema/models 三处同步 + 更新测试 + DB 存量值检查
- 删除 `AN_STUDY_DAG_ENGINE`：同上
- 本轮两者**保留但 warning 可观测**，为数据驱动删除铺路
