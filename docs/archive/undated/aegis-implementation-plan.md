# AEGIS 核心概念实施方案

> 基于 HarnessX 论文的 AEGIS 引擎，移植核心概念到 strategy-research 项目
> 创建时间: 2026-08-04（复核后扩展为 6 大机制）
> 实施时间: 2026-08-04（S1-S4 完成，S5-S6 待定）
> 关联文档: `docs/archive/undated/harnessx-research.md`

---

## 1. 目标

将 AEGIS 的 6 个核心机制内置到 `AutoresearchRunner`（AEGIS-powered study executor）中：

1. **GoalJournal** — 跨轮次追踪假设效果 + 失败编辑归档
2. **StudyAttribution** — 对 Study 结果进行归因分类（F→T / T→F）
3. **LeverScoreboard** — 追踪哪些类型的修改最有效 + 杠杆疲劳检测
4. **NoveltyGate** — 防止重复失败的假设
5. **RegressionGate** — 防止新配置回归已解决的任务（软标记不硬拒）
6. **EarlyStop** — 连续 3 轮无提升提前停止（仅 max_rounds 时生效）

---

## 2. 架构

```
/study start (默认 executor_type="autoresearch")
   └─ StudyScheduler → AutoresearchRunner（AEGIS 内置）
        每轮 = _run_one_round
        ├─ run_researcher_phase  → researcher 输出
        ├─ Journal context 注入  → researcher prompt（跨轮次记忆 + 杠杆评分）
        ├─ Novelty Gate          → 重复假设？abort 本轮
        ├─ run_execution_phase   → DQ → factor → strategist → portfolio → backtest
        ├─ run_evaluation_phase  → risk → attribution → anti-overfit → decide
        ├─ Attribution           → predicted_affected 达标对比
        ├─ Journal               → 假设/杠杆/归因入账 goal_journal 表
        ├─ Regression Gate       → 软标记 study_round_rejected
        ├─ Scoreboard            → 杠杆效果更新 Beta posterior
        └─ Early-stop            → 连续 3 轮无提升 → early_stopped

双引擎共存：
  executor_type="autoresearch" → AutoresearchRunner（默认，轮次循环）
  executor_type="workflow"     → GoalWorkflowRunner（原 DAG 路径）
```

---

## 3. 数据模型（已实施）

### 3.1 study_rounds 表（study/store.py）

```sql
CREATE TABLE study_rounds (
    round_id            TEXT PRIMARY KEY,
    study_id            TEXT NOT NULL,
    goal_id             TEXT,
    session_id          TEXT NOT NULL,
    round_num           INTEGER NOT NULL,
    run_name            TEXT NOT NULL,
    metrics_json        TEXT NOT NULL DEFAULT '{}',
    verdict             TEXT NOT NULL,
    evidence_ids_json   TEXT NOT NULL DEFAULT '[]',
    config_changes_json TEXT,
    agent_output        TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (study_id) REFERENCES studies(study_id) ON DELETE CASCADE
);
```

模型：`StudyRoundRecord`（`study/models.py`）

### 3.2 goal_journal 表（goal/store.py）

```sql
CREATE TABLE goal_journal (
    entry_id                TEXT PRIMARY KEY,
    goal_id                 TEXT NOT NULL,
    session_id              TEXT NOT NULL,
    round_num               INTEGER NOT NULL,
    hypothesis_id           TEXT NOT NULL,
    label                   TEXT NOT NULL,
    levers_json             TEXT NOT NULL DEFAULT '[]',
    predicted_affected_json TEXT NOT NULL DEFAULT '[]',
    gating_outcome          TEXT NOT NULL DEFAULT 'pending',
    gating_attribution_json TEXT NOT NULL DEFAULT '{}',
    changeset_json          TEXT,
    retry_rationale         TEXT,
    archived_reason         TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);
```

模型：`JournalEntry`（`goal/models.py`）

---

## 4. 核心模块（已实施）

### 4.1 归因分类器（study/attribution.py）

纯函数，无 DB 依赖：

```python
classify_attribution(predicted_tasks, passed_before, passed_now)
# → {metric: AttributionOutcome.FLIPPED|STILL_F|REGRESSED|STILL_T}

compute_precision(attribution)
# → (precision, hits, total)
```

### 4.2 杠杆记分牌（goal/scoreboard.py）

```python
LeverScoreboard()
  .update(levers, attribution, gating_outcome, round_num)  # 每轮更新
  .get_best_lever()     # → posterior 最高的杠杆
  .is_lever_fatigued()  # → 连续 3 轮增益 <1%
  .build_scoreboard_context()  # → Markdown 字符串注入 researcher prompt
```

4 个杠杆类别：configuration / control / action / instruction

### 4.3 GoalStore AEGIS 方法（goal/store.py）

```python
# Journal CRUD
append_journal_entry(goal_id, session_id, round_num, hypothesis_id, label, ...)
fill_journal_attribution(goal_id, session_id, round_num, outcome, attribution)
list_journal_entries(goal_id, limit=50)
get_latest_journal_entry(goal_id)

# Gates
check_novelty(goal_id, hypothesis_id, levers, predicted_affected) → (bool, reason)
check_regression(goal_id, attribution) → (bool, regressed_tasks)
archive_rejected_edit(goal_id, round_num, hypothesis_id, reason, detail)

# Context injection
build_journal_context(goal_id, current_round, recent_window=5) → str
```

---

## 5. 执行器架构（已实施）

### 5.1 run_research_round 阶段拆分（autoresearch.py）

原 300 行单体函数拆为 3 个独立阶段：

```python
run_researcher_phase(workspace, strategy, state, run_dir, ...)
  # Step 1-2: read_state + lazy_detection + researcher + hypothesis_register
  # → {"researcher_output": dict}

run_execution_phase(workspace, strategy, state, researcher_output, run_dir, ...)
  # Step 3-4: DQ → factor → strategist → portfolio → backtest
  # → {metrics, strategist_output, backtest_result, ...}

run_evaluation_phase(workspace, strategy, backtest_result, metrics, run_dir, ...)
  # Step 5-6: risk → attribution → anti-overfit → backtest_diag → decide
  # → {verdict, decision, ...}
```

`run_research_round` 保留为协调器（向后兼容），内部调用三个阶段函数。

### 5.2 AutoresearchRunner（study/runner.py）

```python
class AutoresearchRunner:
    async def run(self) → str:
        """入口：启动轮次循环"""

    async def _run_loop(self) → str:
        """主循环：检查停止条件 → 调用 _run_one_round → AEGIS 钩子"""

    def _run_one_round(self, round_num, prev_summary, directives) → dict:
        """单轮执行：阶段函数 + AEGIS 钩子（可被测试 stub）"""

    # AEGIS helpers
    _check_novelty(hypothesis, predicted_affected) → (bool, reason)
    _check_regression(attribution) → (bool, regressed)
    _archive_rejected(round_num, hypothesis, reason, detail)
    _build_journal_context() → str
    _build_scoreboard_context() → str
```

### 5.3 每轮数据流

```
_run_one_round:
  1. read_current_state + _create_run_dir
  2. 注入 journal_context + lever_scoreboard → current_state
  3. run_researcher_phase → researcher_output
  4. Novelty Gate (check_novelty)
  5. run_execution_phase → metrics, strategist_output
  6. run_evaluation_phase → verdict, decision
  7. results.tsv + summary.json (磁盘)
  8. Attribution (classify_attribution)
  9. Journal (append_journal_entry + fill_journal_attribution)
 10. Regression Gate (check_regression) → 软标记
 11. Scoreboard (update)
 12. Return {metrics, verdict, summary, ...}
```

---

## 6. Prompt 模板变更（已实施）

### researcher.md

新增字段：
```json
{
  "predicted_affected": ["calmar", "sharpe"],
  ...
}
```

新增规则：
- `predicted_affected`: 声明本轮预期改善的指标
- 参考 `<journal-history>` 跨轮次记忆
- 参考 `<lever-scoreboard>` 杠杆评分

### strategist.md

新增字段：
```json
{
  "predicted_affected": ["calmar"],
  ...
}
```

---

## 7. 接线（已实施）

### /study/start (study.py)

```python
class StudyStartRequest(BaseModel):
    executor_type: str = "autoresearch"  # 默认旧引擎
    ...

# 路由逻辑
if req.executor_type == "autoresearch":
    sched.submit(study)  # → AutoresearchRunner
else:
    GoalWorkflowRunner(config).start()  # → DAG 单次执行
```

### /study start (chat.py)

```bash
/study start --strategy my_strategy --executor autoresearch  # 默认
/study start --strategy my_strategy --executor workflow      # 切换到 DAG
```

### Scheduler (scheduler.py)

```python
from .runner import AutoresearchRunner
executor = AutoresearchRunner(study, store, control=control, emitter=emitter)
```

---

## 8. 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 默认 executor_type | `autoresearch` | 最小回归风险，新引擎渐进验证 |
| 执行引擎 | `run_research_round` 串行 9-agent | 最小改动，AEGIS 聚焦轮次层面 |
| predicted_affected | prompt 新增字段 + 回退 metric_targets | 精确且向后兼容 |
| Regression Gate | 软标记不硬拒 | 用户决策，避免误伤有整体收益的取舍轮 |
| Early-stop | 仅 max_rounds 时生效 | 无限轮模式不应自动停止 |
| 磁盘 run 目录 | 保留 runs/run_NNNN/ | 兼容 read_current_state/load_run_summary |
| AutoresearchExecutor | 别名 → AutoresearchRunner | 向后兼容现有测试/导入 |
| Journal context 注入 | current_state["journal_context"] | 复用 build_agent_prompt 已有模式 |

---

## 9. 文件清单

### 新增文件
| 文件 | 内容 |
|---|---|
| `core/study/runner.py` | AutoresearchRunner（AEGIS 轮次引擎） |
| `core/study/attribution.py` | 归因分类器（纯函数） |
| `core/goal/scoreboard.py` | 杠杆记分牌（Beta 后验 + 疲劳） |

### 修改文件
| 文件 | 改动 |
|---|---|
| `core/study/models.py` | `StudyStatus.EARLY_STOPPED` + `StudyRoundRecord` |
| `core/study/store.py` | `study_rounds` 表 + CRUD |
| `core/study/__init__.py` | 导出新类型，`AutoresearchExecutor = AutoresearchRunner` |
| `core/study/scheduler.py` | 改用 `AutoresearchRunner` |
| `core/goal/models.py` | `JournalEntry` |
| `core/goal/store.py` | `goal_journal` 表 + journal CRUD + gates |
| `core/goal/__init__.py` | 导出 `JournalEntry` |
| `core/autoresearch.py` | 3 个阶段函数 + 辅助函数 |
| `api/routers/study.py` | `executor_type` 路由 |
| `api/routers/chat.py` | `--executor` flag |
| `templates/.prompts/researcher.md` | `predicted_affected` + context 引用 |
| `templates/.prompts/strategist.md` | `predicted_affected` |

### 测试修改
| 文件 | 改动 |
|---|---|
| `tests/test_study_scheduler.py` | patch `AutoresearchRunner` 而非旧 executor |

---

## 10. 待完成（S5-S6）

### S5: 文档更新
- [x] `docs/archive/undated/aegis-implementation-plan.md`（本文档）
- [ ] `docs/archive/undated/harnessx-research.md` 补充实施状态

### S6: 新增测试
- [ ] `tests/test_attribution.py` — 归因分类器
- [ ] `tests/test_scoreboard.py` — 杠杆记分牌 + 疲劳
- [ ] `tests/test_journal.py` — Journal CRUD + novelty + regression + context
- [ ] `tests/test_study_rounds.py` — Round 追踪 CRUD
- [ ] `tests/test_runner_aegis.py` — Runner AEGIS 集成（behavior stub）
