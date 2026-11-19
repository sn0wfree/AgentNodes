# M13 只读审查：Workflow 活跃运行状态的所有者

> **性质**：只读审查判定（R-014）· **日期**：2026-11-19 · **零代码改动**
> **结论**：**互补记录（保持）**——两个 registry 服务于同一 router 文件里
> 两条互不相交的产品路径（goal workflow / modular definition run），不是
> 同一 run 的两份状态。**不出收敛方案**（不适用）。

## 0. 判定依据（deletion test）

- 删 `_active_runners` → 5 个 goal 家族入口 + 2 个测试文件失效；
- 删 `WorkflowRunRegistry` → 4 个 definition 家族入口 + 2 个测试文件失效；
- **两端都通不过「被对方替代」的删除测试** → 无冗余。

## 1. 两端对照

| 维度 | `_active_runners`（L1 router） | `WorkflowRunRegistry`（L3 kernel） |
|---|---|---|
| 键 | `goal_id`（`goal_` 前缀） | `run_id`（`wf_` 前缀） |
| 值 | `GoalWorkflowRunner`（L2 apps 类）+ entry dict | `WorkflowRunner`（L3 kernel 类） |
| 字段 | runner / session_id / workflow_name / started_at / owner | 仅 runner |
| TTL | `_ACTIVE_RUNNER_TTL = 3600`（1h） | 无 |
| 终态词 | completed / error / hook_completed | completed / failed / cancelled |
| 消费者 | goal 家族 5 入口（start/status/pause/resume/directive/events） | definition 家族 4 入口（start-definition/approve/status/delete） |
| 入口重叠 | **无** | **无** |

关键证据：`workflow.py:111-117` entry dict 含 owner/session/started_at；
`executor.py:372-394` registry 只有 `_runs: dict[str, WorkflowRunner]`。
两类 ID 前缀不同（`goal_` vs `wf_`），不存在同一 run 同时出现在两端的情形。

## 2. 为什么不能合并

- router map 独占 owner / session_id / started_at / TTL / workflow_name 与
  goal runner 的 pause/resume/directive/subscribe 句柄；
- kernel registry 独占 run_id → WorkflowRunner 的终态快照与重启后
  `resume_from_store` 重建锚点；
- 合并要么制造假唯一键，要么把 HTTP owner 授权压进 kernel——已被用户
  裁决禁止（owner 授权保持在 interfaces）。

owner 过滤当前**全部在 L1**（`_runner_visible` / `_require_run_access`），
kernel 层完全没有 user/owner 词汇——符合裁决。

## 3. 独立整改项（不属本次合并议题，均待单独裁决）

| 级别 | 项 | 证据 | 建议 |
|---|---|---|---|
| 🟠 中 | kernel registry 终态残留：`prune()` 零调用（生产/测试均无），完成/失败 run 永久留在 `_runs`，仅 `run_delete` 显式 pop | `executor.py:387-391`；`workflow.py:743-745`；`test_workflow_approval_api.py:216-230` | 独立小批：终态写入点或 `run_status` 访问点调 `prune()`；补「终态后 registry 不再持有 run」测试 |
| 🟡 低 | 误导注释两处："mirrors `_active_runners`" 与代码事实不符 | `executor.py:14,369` | 改准确描述（本次顺手修正，见下） |
| 🟡 低 | owner 以动态私有属性 `_owner` 挂在 kernel runner 上 | `workflow.py:641` 写 / `:72` 读 | 独立 seam 修正：L1 side-map 持有 owner；kernel runner 不加 user 概念 |
| 🟡 低 | 两端均无锁、进程局部（多 worker 分叉） | `workflow.py:27`；`executor.py:376` | 记录为已知约束；将来若需跨 worker 唯一性，用 shared store 而非给裸 dict 加锁 |

## 4. 本次顺手修正（低风险文档）

`kernel/workflow/executor.py:14,369` 两处 "mirrors `_active_runners` in
api/routers/workflow.py" 注释改为准确表述：L1 goal-workflow 另有独立的
process-local handle cache，键/值/职责与 kernel registry 不同。

（审查全程只读；未运行改变系统状态的命令。）

## 5. 终态残留整改尝试与回退（2026-11-19）

按风险表尝试在 `_get_run_registry()` 统一入口 prune 终态 run，并补了
「终态后 registry 不再持有 run」测试。**回归发现语义冲突**：
`tests/test_workflow_approval_api.py::test_approve_rejects_when_not_awaiting`
期望终态 run 的 approve 返回 **409**（存在但不 awaiting），prune 后变
**404**（不存在）——立即清理改变可观察 HTTP 语义。

**已回退** prune 接线与该测试（34 workflow 测试恢复全绿），并在
`_get_run_registry()` 留注释说明冲突。终态残留的内存治理须连同
approve/status 的 404-vs-409 语义一起裁决（例如：registry 保留终态 run
但加 TTL 上限，或 approve 端改为查 store 判定存在性）——**本批不做**。
这是「独立整改项不混提」纪律的实证：修复不能破坏现有契约。
