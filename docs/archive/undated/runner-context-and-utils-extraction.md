# RunnerContext + 工具函数分类提取方案

> Date: 2026-08-20
> 目标: 引入 RunnerContext 解耦依赖，提取 5 个工具模块（~550 行）

## 1. RunnerContext 设计

### 目的

各工具模块函数需要访问 runner 的状态（study、store、emit 等）。直接传递 `runner` 对象会导致循环依赖和紧耦合。引入 `RunnerContext` 数据类作为依赖注入容器。

### 定义

```python
@dataclass
class RunnerContext:
    """Shared context for all extracted engine/utility modules."""
    study_id: str
    session: str
    study: StudyRecord
    study_store: StudyStore
    control: ControlToken
    emit_fn: Callable[[str, str, dict], None]  # session_id, event, data
    goal_store: Any
    # AEGIS state
    prev_passed: set[str]
    best_score: float
    idle_rounds: int
    # Budget state
    total_used_time: float
    total_used_turns: int
    # Trace
    trace_id: str
```

### 使用方式

```python
# 在 runner.py
def _to_context(self) -> RunnerContext:
    return RunnerContext(
        study_id=self.study_id,
        session=self._get_study().session_id,
        study=self._get_study(),
        study_store=self.study_store,
        control=self.control,
        emit_fn=self._emit,
        goal_store=self._goal_store,
        prev_passed=self._prev_passed,
        best_score=self._best_score,
        idle_rounds=self._idle_rounds,
        total_used_time=self._total_used_time,
        total_used_turns=self._total_used_turns,
        trace_id=self._trace_id,
    )
```

## 2. 工具模块提取

### 2.1 aegis.py（AEGIS 工具）

| 函数 | 来源行数 | 依赖 |
|------|---------|------|
| `check_novelty(ctx, hypothesis, predicted_affected)` | 1823 | ctx.best_score |
| `check_regression(attribution)` | 1830 | 无状态 |
| `archive_rejected(ctx, round_num, hypothesis, reason, detail)` | 1835 | ctx.study_store |
| `verdict_reason(eval_result, strategist_output)` | 1808 | 无状态 |
| `build_journal_context(ctx)` | 1842 | ctx.goal_store |
| `build_scoreboard_context(ctx)` | 1847 | ctx.goal_store |

### 2.2 budget.py（预算工具）

| 函数 | 来源行数 | 依赖 |
|------|---------|------|
| `account_round_budget(ctx, exec_result)` | 1854 | ctx.total_used_* |
| `budget_exceeded(ctx)` | 1860 | ctx.study |
| `budget_summary(ctx)` | 1868 | ctx.total_used_* |
| `complete_goal(ctx, exec_result)` | 1873 | ctx.goal_store |
| `round_cooldown(study)` | 1944 | study 参数 |
| `maybe_load_previous_summary(ctx, study)` | 1950 | ctx.study_store |

### 2.3 knowledge.py（知识/证据工具）

| 函数 | 来源行数 | 依赖 |
|------|---------|------|
| `collect_knowledge(ctx, topics)` | 1697 | ctx.study_store, ctx.goal_store |
| `record_keep_evidence(ctx, round_num, run_name, metrics)` | 1751 | ctx.goal_store, ctx.study_store |

### 2.4 study_io.py（文件 I/O 工具）

| 函数 | 来源行数 | 依赖 |
|------|---------|------|
| `update_results_tsv(runs_dir, run_name, verdict, ...)` | 1907 | 无状态 |
| `emit_topology(ctx, graph, sid, round_num)` | 1658 | ctx.emit_fn |
| `save_agent_output(run_dir, agent_id, result)` | 1570 | 无状态 |
| `build_round_task_text(state, directive_text)` | 1556 | 无状态 |

### 2.5 state_utils.py（状态管理工具）

| 函数 | 来源行数 | 依赖 |
|------|---------|------|
| `mark_terminal(ctx, study_store, status, ...)` | 1969 | ctx.study_store |
| `wait_until_resumed(control)` | 1988 | ctx.control |
| `emit(ctx, session_id, event, data)` | 1992 | ctx.emit_fn |
| `open_goal_store()` | 2012 | 无状态 |
| `format_directives(directives)` | 2017 | 无状态 |

## 3. 实施顺序

1. RunnerContext 定义 + runner._to_context()
2. aegis.py（最独立，无状态依赖）
3. study_io.py（最独立，无状态依赖）
4. budget.py（依赖 ctx 状态）
5. knowledge.py（依赖 ctx + goal_store）
6. state_utils.py（依赖 ctx）

## 4. 验证

每个模块提取后运行测试，最终 72 tests 全部通过。
