# hook↔runner 公开面与 checkpoint 恢复 R-014 方案（第三轮复审候选 G3）

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **来源**：第三轮架构复审候选 G3（`C:\Users\1\AppData\Local\Temp\architecture-review-20261120-163911.html`）。
> **前置**：goal coverage 单源 / G2 完成编排归一已实施。
> **范围**：给 hook 与 runner 各加窄公开面；把 `__new__` + 15 次私有赋值换成 `from_snapshot`；私有字段全部保留。

## 0. 目标与非目标

**目标**：
- runner 暴露窄公开面（`is_cancelled` / `set_agent_status` / `current_layer` / `consume_directives`），hook 不再读 `runner._state` / `runner._goal_id`；
- hook 暴露 `state_snapshot()` / `restore_state(...)`，runner 不再读 `hook._layer_results`；
- checkpoint 恢复走 `GoalWorkflowHook.from_snapshot(...)`（经构造器），不再 `__new__` + 15 私有赋值；补上遗漏的 `_evidence_agents` / `_metric_targets`；
- 现有钉私有的测试零改动全绿；另加公开面行为测试。

**非目标**：
- **不删任何私有字段**（现有测试锚大量钉私有布局，冻结保留）；
- 不改 checkpoint 文件格式 / `CheckpointStore`；不改 SwarmRuntime 回调接口；
- 不改 `name` / `completed` / `evidence_count` 既有 property；
- 不改 goal 完成 / 证据 / 租约语义；不碰冻结删除批。

## 1. 现状证据（已回读源码核对）

### 1.1 hook → runner 私有读（5 处）
- `on_layer_start`（`workflow_hook.py:104-108`）：`getattr(self._runner, "_state")` → 写 `current_layer` / `set_agent_status`；`:111-116` 公开探测 `consume_directives`。
- `on_agent_complete`（`:136-142`）：`_state.set_agent_status(..., "skipped"/"success")`。
- `should_stop`（`:243-245`）：`_state.cancelled`。
- `_auto_checkpoint`（`:254-263`）：`_goal_id` 存在性探测 + 日志。

### 1.2 runner → hook 私有读写（3 处）
- `resume_and_continue`（`workflow.py:717-719`）：`self._hook._layer_results = layer_results_raw`。
- `checkpoint`（`:928-941`）：`getattr(hook, "_layer_results")`，fallback 读 `evidence_count`/`completed` property。
- `resume_from_checkpoint`（`:1000-1020`）：`GoalWorkflowHook.__new__` + **15 次赋值**（14 distinct 字段；`_layer_results` 赋两次），漏掉 `__init__` 的 `_evidence_agents`（hook :76，读于 :153）与 `_metric_targets`（hook :79，读于 :201）——恢复出的 hook 缺这两个属性，是潜在崩溃点。

### 1.3 既有公开面先例
runner 已有 `state` / `goal_id` / `name` / `event_bus` property（`:316-335`）；`name` 的 docstring 明示「替代 `_config` 私有访问」——本项目已认可这类收口。

### 1.4 测试锚
`test_workflow_hook.py`（私有 runner `_state` 与 hook 字段）、`test_workflow_checkpoint_resume.py`（`_seed_hook` 写 `runner._hook._layer_results`；断言恢复后 `_layer_results`）、`test_goal_workflow_v053.py`（`GoalWorkflowHook.__new__` + 私有字段 setup）、`test_goal_workflow_phase3.py`（`hook._event_bus` / `_completed` / runner `_state`）。全部保留不动。

## 2. 设计（grilling 全 A：Q7-Q9）

| # | 决策 | 落位 |
|---|---|---|
| Q7 | runner 加窄公开方法：`is_cancelled() -> bool`、`set_agent_status(agent_id, status)`、`current_layer` property（getter+setter）、`consume_directives()` 透传；hook 的 5 处私有读改走这些 | `workflow.py` + `workflow_hook.py` |
| Q7′ | hook 加 `state_snapshot() -> dict`（返回 `layer_results` + evidence_count + completed 等）+ `seed_layer_results(results)`；runner 的 checkpoint / resume_and_continue 改走它们 | `workflow_hook.py` + `workflow.py` |
| Q8 | 新增 `GoalWorkflowHook.from_snapshot(...)` classmethod：经 `__init__` 构造（补全 16 字段含 `_evidence_agents`/`_metric_targets`），再恢复 layer_results；`resume_from_checkpoint` 改调它 | `workflow_hook.py` + `workflow.py:1000-1020` |
| Q9 | 现有私有字段测试零改动；另加公开面行为测试（小 fake runner 驱动 hook，验证公开方法被调用、私有字段不再穿透） | 新测试 |

**实现要点**：
- runner 的 `consume_directives` / `set_agent_status` / `is_cancelled` / `current_layer` 都是薄方法，内部仍操作 `_state` / 既有逻辑（行为逐字不变）；hook 改调这些方法后，`getattr(runner, "_state")` 消失。
- `state_snapshot()`：`{"layer_results": ..., "evidence_count": ..., "completed": ...}`；`seed_layer_results(results)` 写 `_layer_results`（原 `resume_and_continue` 的私有写改此）。
- `from_snapshot(session_id, goal_id, evidence_map, store, *, runner, layer_results, workflow_name, event_bus, completion_strategy=None, completion_mode="", metric_targets=None)`：调 `__init__` 全参 → 赋 `_layer_results = layer_results`；返回完整 hook（16 字段齐全）。
- 保留 `checkpoint` 的 fallback 分支（若 hook 非 GoalWorkflowHook 实例）——通过 `state_snapshot()` + `getattr` 兼容。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| runner 4 公开面 + hook 5 处私有读消除 | workflow.py / workflow_hook.py |
| hook state_snapshot/seed_layer_results + runner 2 处私有读写消除 | 同上 |
| from_snapshot 替代 __new__ + 补两字段 | workflow.py:1000 |
| 现有私有字段测试零改动全绿 | 既有测试 |
| 公开面行为测试 | 新测试 |
| 数字同步 | R-007 |

**回滚**：单提交回退恢复；零残留。

## 4. 风险与回滚

- **注入 runner 的 fake 测试**：`test_workflow_hook.py` 的 fake runner 是 `SimpleNamespace(_state=...)`，无新公开方法；hook 改调公开方法后这些测试会失效——解决：hook 公开面调用保留 `getattr(runner, "method", None)` 探测 + 旧 `_state` fallback，或直接更新 fake（首选：更新测试 fake 加公开方法，行为断言不动）。实施时按最小改动择一。
- **__new__ 兼容**：`test_goal_workflow_v053.py` 直接用 `__new__` 构造 hook 并设私有字段——保留 `__init__` 与私有字段，零影响。
- **from_snapshot 参数**：必须与 `resume_from_checkpoint` 作用域可得的变量对齐（session_id / goal_id / config / runner / event_bus）；实施时核实。
- **数字漂移**：sync + check_framework_numbers。

## 5. 明确不做

① 删任何私有字段；② 改 checkpoint 文件格式 / CheckpointStore；③ 改 SwarmRuntime 回调；④ 改 existing property 语义；⑤ 改 goal 完成/证据/租约语义；⑥ 任何删除批动作。

## 6. 实施前确认

本方案来自 grilling 全 A（Q7-Q9）。**未获批不得实施任何代码**；获批后按 R-007 单提交实施，doc first 纪律已满足（本文件）。
