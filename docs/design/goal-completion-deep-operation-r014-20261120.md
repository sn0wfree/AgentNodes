# 目标完成编排归一 R-014 方案（第三轮复审候选 G2）

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **来源**：第三轮架构复审候选 G2（`C:\Users\1\AppData\Local\Temp\architecture-review-20261120-163911.html`）。
> **前置**：第一轮候选 7 goal 覆盖单源（audit.all_criteria_covered / rows_for / build_completion_audit_rows）已实施（`48f0b941`）。
> **范围**：CompletionStrategy 6 参数接口收成单值 context；hook 与 CLI 经同一深操作完成；源码文本锚换行为测试。

## 0. 目标与非目标

**目标**：
- `CompletionStrategy.complete` 从 6 参数收成单参 `CompletionContext`（冻结 dataclass）；
- hook 的 `_auto_complete` 与 CLI 的 `cmd_complete` 都经 `apps/goal` 的一个深完成操作派发，不再各写一份 store 调用编排；
- CLI 的**全量 evidence 行形**（`build_completion_audit_rows(..., per_criterion=False)` 默认形态）经显式参数保留，行为逐字不变；
- 删两个源码文本锚，换成真实策略/store mutation 行为测试。

**非目标**：
- **不重做**已决的覆盖单源（`all_criteria_covered` / `rows_for` / `build_completion_audit_rows`）——它们保持不动；
- 不改 `apps/goal/store.py`（`update_status` / `complete_lite`）、不改 schema / 事件；
- 不改 `apps/goal/cli.py::cmd_goal_complete` / `apps/goal_tools.py::complete_goal`（其他完成面，Q5 明确不动）；
- 不改 hook 的触发逻辑（on_layer_complete 的 metric / criteria 两路）、gate 位置、`_completed` 幂等、事件发射；
- 不改 CLI 的 gate（all_criteria_covered 拒绝）、打印与返回码；
- 不碰冻结删除批；不改 GLOSSARY（"完成策略"是通用概念，非本项目特有领域名词）。

## 1. 现状证据（已回读源码核对）

### 1.1 三处编排与浅接口
- `completion_strategy.py:20-33`：`CompletionStrategy.complete(store, session_id, goal_id, criteria, evidence, workflow_name)` 6 参数；Auto 用全 6、Lite 忽略 criteria/evidence、Manual 忽略除 workflow_name 外全部。
- `workflow_hook.py:334-377` `_auto_complete`：取快照 → 选策略 → asyncio juggling（running loop → create_task；无 loop → asyncio.run）→ 固定 recap "Workflow {name} auto-completed[/ (lite)]" → 置 `_completed` + emit。
- `slash_goal.py:291-343` `cmd_complete`：取快照 → lite 直接 `complete_lite(recap or "(lite completion)")`；非 lite gate + `build_completion_audit_rows(snapshot, recap)`（**全量 evidence 行形**）+ `update_status(..., recap or None)`。

### 1.2 已决边界（不可重做）
`goal-coverage-unification-r014-20261120.md §7`：已统一 (a) 覆盖判定 → `audit.all_criteria_covered`、`workflow_hook._check_all_criteria_covered` 已切；(b) 行形 → `audit.rows_for`、`completion_strategy.complete` 已切（CLI 保持 `per_criterion=False` 全量）。**未**动工厂选择、CLI 流程、store/schema/事件、触发/异步编排。

### 1.3 源码文本锚
`test_goal_audit.py:138-150` 断言 `"all_criteria_covered" in text`、`"evidence_by_criterion:" not in text`；`:153-165` 断言 `"rows_for" in text`、`"cid_evidence_ids" not in text`。二者执行时不调用任何策略/CLI/hook，是假绿。其余：`test_workflow_hook.py:218-255` 用 FakeStrategy 测 hook 触发（6 参边界）；`test_cli_goal_cmd.py:85-112` 对 cmd_complete 断言 `rc in (0,1)`（空泛）。

### 1.4 层
`completion_strategy.py` / `workflow_hook.py` 在 L2 apps/goal，`slash_goal.py` 在 L1 interfaces/cli。L1→L2 合法，深操作放 `apps/goal` 可被三者共用（`audit.py` 已有同款下沉先例）。

## 2. 设计（grilling 全 A：Q4-Q6）

| # | 决策 | 落位 |
|---|---|---|
| Q4 | 冻结 `CompletionContext`（字段：`store`, `session_id`, `goal_id`, `snapshot`, `mode`, `recap: str|None`, `workflow_name`, `audit_rows: list|None=None`）；`CompletionStrategy.complete(self, context)` 单参；6 参数 Protocol 删除 | `completion_strategy.py` |
| Q5 | 深操作 `apps/goal/complete_goal(context)`：选策略 + 异步派发（running loop → create_task；无 loop → asyncio.run）单点；hook 与 CLI 都调它 | `completion_strategy.py`（与策略同模块） |
| Q5′ | CLI 保留 gate 与打印/rc；经 `audit_rows=build_completion_audit_rows(snapshot, recap)` 显式传全量行形；recap 由调用方显式给（hook 传固定串、CLI 传 `recap or None` / `recap or "(lite completion)"`） | `slash_goal.py` |
| Q6 | 删 `test_goal_audit.py:138-150,153-165` 两源码文本锚，换行为测试：Auto 策略真调 store 断言 audit 行形；Lite 断言 complete_lite；hook covered 判定行为 | `test_goal_audit.py` / 新测试 |

**实现要点**：
- 策略实现逐字保行为：
  - Auto：`rows = context.audit_rows if context.audit_rows is not None else rows_for(context.snapshot.get("criteria", []), context.snapshot.get("evidence", []), f"Auto-completed by workflow {context.workflow_name}")`；`update_status(session_id, goal_id, expected_goal_id=goal_id, status=COMPLETE, audit=rows, recap=context.recap)`。
  - Lite：`complete_lite(..., recap=context.recap)`。
  - Manual：log workflow_name，no-op。
- goal_id 由调用方从 snapshot 取（两调用方现都取得到），放 context 显式字段。
- 异步派发：hook 仍需在 `create_task` 分支后置 `_completed=True` + emit（fire-and-forget 语义不变）；`asyncio.run` 分支同步返回 bool。深操作返回 `bool | None`（None = 已调度未等待）。
- CLI：非 lite 分支先 gate（不变），再 `complete_goal(CompletionContext(..., mode="auto", recap=recap or None, audit_rows=build_completion_audit_rows(snapshot, recap)))`；lite 分支 `mode="lite", recap=recap or "(lite completion)"`。
- 保留 `CompletionStrategyFactory`（mode → strategy 选择不变）。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| CompletionContext 冻结 + complete 单参 | completion_strategy.py |
| 深操作 complete_goal 单点派发 | completion_strategy.py |
| hook / CLI 都经深操作 | workflow_hook.py / slash_goal.py |
| CLI 全量行形 + recap 逐字不变 | 显式参数保留 |
| 两源码文本锚删除 + 行为测试替代 | test_goal_audit.py |
| hook 触发 / gate / CLI 打印 rc 全绿 | 既有测试不动 |
| 数字同步 | R-007 |

**回滚**：单提交回退恢复；零残留。

## 4. 风险与回滚

- **recap 差异**：hook 固定串 vs CLI 调用方串——由 context.recap 显式承载，策略不再自己拼；两调用方各自传原值，逐字等价。
- **行形差异**：Auto 用 rows_for（per-criterion 过滤），CLI 用 build_completion_audit_rows（全量）——CLI 经 audit_rows 显式注入，原有差异保留（这是已决口径，不是要消掉的漂移）。
- **异步语义**：hook 在 running loop 中 fire-and-forget（不等待结果、先置 completed），深操作必须保留该分支；CLI 无 loop → asyncio.run 同步执行，rc 语义不变。
- **FakeStrategy 契约**：`test_workflow_hook.py:210-216` 的 FakeStrategy 是 6 参 `complete`——深操作改为单参后需同步该 fake（测试锚迁移，非删除）；其行为断言（触发/幂等）不动。
- **数字漂移**：sync + check_framework_numbers。

## 5. 明确不做

① 重做覆盖单源（已决）；② 改 store/schema/事件；③ 改 `apps/goal/cli.py` / `goal_tools.py` 完成面；④ 改 hook 触发/gate/事件、CLI gate/打印/rc；⑤ 任何删除批动作。

## 6. 实施前确认

本方案来自 grilling 全 A（Q4-Q6）。**未获批不得实施任何代码**；获批后按 R-007 单提交实施，doc first 纪律已满足（本文件）。

## 7. 执行记录（2026-11-20 · 已实施 · 用户"请继续做完所有的"授权）

- `completion_strategy.py`：新增冻结 `CompletionContext`（含 `strategy` 显式注入字段，保 hook 注入策略的测试路径），`CompletionStrategy.complete(context)` 单参取代 6 参；新增深操作 `complete_goal(context)` 独占选策略 + 异步派发（running loop → create_task 返回 None；无 loop → asyncio.run 返回 bool）。
- `workflow_hook.py::_auto_complete`：构造 context（recap 固定串、注入 strategy 经 context 字段）+ 调 `complete_goal`；`_completed` 置位 / 事件 / 异常吞噬逐字不变。
- `slash_goal.py::cmd_complete`：两分支均构造 context 调深操作；非 lite 经 `audit_rows=build_completion_audit_rows(snapshot, recap)` 显式保留全量行形；gate / 打印 / rc 逐字不变。
- `apps/goal/__init__.py`：导出新符号（CompletionContext / complete_goal）。
- 测试：`test_goal_audit.py` 两源码文本锚换 3 个行为测试（hook 覆盖判定经 audit、Auto 行形 store mutation、Lite complete_lite）；`test_workflow_hook.py` FakeStrategy 6 参 → 单参（行为断言不动）。
- 已决边界守住：覆盖单源 / store / schema / 事件 / CLI gate / hook 触发零改动；其他完成面（apps/goal/cli.py、goal_tools.py）零改动。
- 验证：受影响面 78 passed；全量 `-n 6` **8072 passed** / 0 failed（+1 净新例）；batch_gate ALL GREEN；数字 464/97,029（L2 11,546）。
