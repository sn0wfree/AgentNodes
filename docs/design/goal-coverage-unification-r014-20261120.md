# 目标覆盖判定统一（候选 7）R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：架构复审候选 7（Strong）；M5 已修 all_covered 幻影键，本项是那次修复的口径收尾。
> **范围**：统一「全覆盖判定」与「AuditRow 合成」两处领域口径；不改 store/schema/事件/接口。

## 0. 目标与非目标

**目标**：把覆盖判定的三口径（status+evidence / 仅 evidence / aggregated）收成
`apps/goal/audit.py::all_criteria_covered` 单一深函数，修 workflow_hook 漏判
status 覆盖与 required 语义不一致；AuditRow 行形收成单一函数。

**非目标**：① 不改 `AuditRow` 模型 / store.update_status 签名；② 不改 slash_goal
的 CLI 渲染；③ 不改 completion_strategy 的工厂选择逻辑；④ 不改事件。

## 1. 现状证据（file:line）

| 口径 | 位置 | 判定 |
|---|---|---|
| status+evidence | `context.py:67-80` `criterion_is_covered` / `goal_progress_tuple` | status 非 open **或** evidence 匹配即 covered；但 `goal_progress_tuple` 统计**所有** criteria（含非 required） |
| 仅 evidence | `workflow_hook.py:320-341` `_check_all_criteria_covered` | 只建 evidence_by_criterion，**漏 status 覆盖**；只算 required（:336） |
| aggregated | `audit.py:49-62` `all_criteria_covered` | 调 goal_progress_tuple，但用 `covered >= len(criteria)`（含非 required）→ 非必填未覆盖也会拒绝 |

行形：`completion_strategy.py:58-72` 按 criterion 过滤 evidence_ids（空回退全量首个）；
`audit.py:65-93` 全量 evidence_ids。两种行形并存。

## 2. 设计

**单源覆盖函数**（`audit.py::all_criteria_covered`）：改为逐条 required criterion
用 `criterion_is_covered(snapshot, c)` 判定——统一 status+evidence 口径 + 只算
required（与 workflow_hook 一致）。无 required criteria → True。

**单源行形**（`audit.py::build_completion_audit_rows`）：增 `per_criterion: bool = True`
参数（默认按 criterion 过滤，对齐 completion_strategy 的更精确口径；空则回退
全量首个，保持兼容）。`slash_goal` 调用保持默认全量需显式传 `per_criterion=False`
以维持原 CLI 行为（已声明的行为变更需记录）。

**调用方切换**：
- `workflow_hook._check_all_criteria_covered`：删自有 evidence_by_criterion 循环，
  改 `return all_criteria_covered(snapshot)`（修 status 漏判 + required 统一）。
- `completion_strategy.complete`：删自建 audit_rows 循环，改调
  `build_completion_audit_rows(snapshot, recap=..., per_criterion=True)`（需从
  criteria/evidence 构造 snapshot dict 或改函数签名接收 criteria/evidence）。

**保留**：`criterion_is_covered` 仍是 context.py 的底层单源，audit 委托它。

## 3. 分批与验收（单提交）

| 验收项 | 落点 |
|---|---|
| 三口径收一 | workflow_hook / completion_strategy 不再自建覆盖/行形逻辑 |
| status 覆盖不漏判 | 新测试：criterion status=covered 无 evidence → covered |
| required 语义统一 | 非 required 未覆盖不阻塞完成 |
| 行形单一 | 两调用方共用 build_completion_audit_rows |
| 既有 audit 测试不退化 | test_goal_audit.py 全绿；全量 + gate |

**回滚**：单提交整批回退即恢复当前三口径，零 schema/接口变化。

## 4. 风险与回滚

- `completion_strategy.complete` 签名收 criteria/evidence 两 list（不是 snapshot）；
  为不改其调用方，`build_completion_audit_rows` 增一个重载入口
  `rows_for(criteria, evidence, notes)` 供它用——仍是同一模块内的单源实现。
- slash_goal 完成闸行为：原全量 evidence_ids 行形保留（显式 per_criterion=False）。
- workflow_hook 修 status 漏判后，原本"status 已覆盖但无 evidence"的会首次通过——
  这是修 bug，需在测试里钉住。

## 5. 明确不做

① store/schema/事件；② CLI 渲染；③ completion_strategy 工厂选择；④ 删除批候选。

## 6. 实施前确认

本方案来自用户「继续」推进候选 7 的授权，确认后按 R-007 单提交实施。

## 7. 执行记录（2026-11-20 · 已实施）

- `audit.py::all_criteria_covered` 改为逐条 required criterion 用
  `criterion_is_covered(snapshot, c)` 判定——统一 status+evidence 口径
  + 只算 required（修 workflow_hook 漏判与 goal_progress_tuple 的 len 口径）。
- `audit.py` 新增 `rows_for(criteria, evidence, notes)` 行形单源（按
  criterion 过滤 + 无匹配回退全量首个）；`build_completion_audit_rows`
  增 `per_criterion=False` 参数（默认全量保留 CLI 行为，True 委托 rows_for）。
- 两调用方切换：`workflow_hook._check_all_criteria_covered` 删自建
  evidence_by_criterion 循环 → `all_criteria_covered(snapshot)`；
  `completion_strategy.complete` 删自建 AuditRow 循环 → `rows_for(...)`。
- 新测试 `test_goal_audit.py::TestCoverageUnification` 5 例 + `TestCompletionStrategyUsesSingleShape` 1 例：status 覆盖无 evidence、非必填未覆盖不阻塞、必填未覆盖阻塞、rows_for 过滤+回退、两调用方源码锚。

验证：goal 面 116 passed（audit/context/workflow_hook/workflow_v051/
api_goal_router）；全量 + gate 见提交前记录。零 schema/接口/事件变化。

**架构复审七候选全部收口**：1（事件）/2（编排）/3（部分，from_seam_result）/
4（入口）/5（旋钮）/6（RSI 宿主）/7（goal 口径）。
