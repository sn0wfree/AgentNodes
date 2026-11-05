# Study 中止/归档/改目标/流程优化 设计文档

> 日期: 2026-08-18
> 状态: 已规划，待实施
> 关联: `study-longhorizon-v2-design.md`、`study-ui-improvement.md`

## 1. 背景与目标

研究任务（Study）在长期运行中存在三类体验痛点：

1. **缺乏全生命周期的控制能力** —— 任务完成后只能"看着"，无法做软删除（归档）让列表清爽
2. **目标僵硬** —— 一旦设定研究目标，跑到一半发现方向错了，只能中止重来
3. **流程可视化粗糙** —— 当前 Agent 流水线只显示 3 态（pending / running / completed），缺少轮次切换、耗时、ETA 等关键信息

本文档给出三组改进：

| 编号 | 需求 | 设计 |
|------|------|------|
| A | 中止 + 归档 | 新增 `CANCEL` + `ARCHIVE` 两个 action；新增 `ARCHIVED` 状态 |
| B | 中途修改研究目标 | 新增 `REPLACE_OBJECTIVE` action；新表 `objective_history`；下一轮生效 |
| C | 流程显示优化 | 保留 ReactFlow，升级节点视觉 / 进度条 / 轮次切换 / 状态色 |

## 2. 状态机扩展（A: 中止 + 归档）

### 2.1 状态枚举扩展

```python
class StudyStatus(str, Enum):
    # 现有
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    BUDGET_LIMITED = "budget_limited"
    MONITORING = "monitoring"
    NEEDS_REFRESH = "needs_refresh"
    EARLY_STOPPED = "early_stopped"
    # 新增
    ARCHIVED = "archived"  # 软删除标记，详情可看可重启，列表默认隐藏
```

### 2.2 Action 矩阵扩展

```python
class StudyAction(str, Enum):
    # 现有
    PAUSE = "pause"
    RESUME = "resume"
    RESUME_INTERRUPTED = "resume_interrupted"
    CANCEL = "cancel"
    REDO = "redo"
    DIRECTIVE = "directive"
    # 新增
    ARCHIVE = "archive"
    UNARCHIVE = "unarchive"
    REPLACE_OBJECTIVE = "replace_objective"

ACTION_MATRIX: dict[StudyStatus, frozenset[StudyAction]] = {
    StudyStatus.QUEUED:         frozenset({StudyAction.CANCEL, StudyAction.ARCHIVE, StudyAction.REPLACE_OBJECTIVE}),
    StudyStatus.RUNNING:        frozenset({StudyAction.PAUSE, StudyAction.CANCEL, StudyAction.ARCHIVE, StudyAction.REPLACE_OBJECTIVE}),
    StudyStatus.PAUSED:         frozenset({StudyAction.RESUME, StudyAction.CANCEL, StudyAction.ARCHIVE, StudyAction.REPLACE_OBJECTIVE}),
    StudyStatus.INTERRUPTED:    frozenset({StudyAction.RESUME_INTERRUPTED, StudyAction.ARCHIVE, StudyAction.REPLACE_OBJECTIVE}),
    StudyStatus.MONITORING:     frozenset({StudyAction.PAUSE, StudyAction.CANCEL, StudyAction.ARCHIVE, StudyAction.REPLACE_OBJECTIVE}),
    StudyStatus.COMPLETE:       frozenset({StudyAction.ARCHIVE}),
    StudyStatus.CANCELLED:      frozenset({StudyAction.ARCHIVE}),
    StudyStatus.ERROR:          frozenset({StudyAction.ARCHIVE}),
    StudyStatus.BUDGET_LIMITED: frozenset({StudyAction.ARCHIVE}),
    StudyStatus.NEEDS_REFRESH:  frozenset({StudyAction.ARCHIVE}),
    StudyStatus.EARLY_STOPPED:  frozenset({StudyAction.ARCHIVE}),
    StudyStatus.ARCHIVED:       frozenset({StudyAction.UNARCHIVE}),  # 详情页可"取消归档"
}
```

### 2.3 归档语义

| 维度 | 行为 |
|------|------|
| 数据持久化 | **保留所有数据**（study、rounds、directives、journal、todos、knowledge、goal、objective_history） |
| 列表过滤 | `list_studies()` 默认 `WHERE execution_status != 'archived'`，需 `include_archived=True` 才显示 |
| 详情页 | 完全可访问，KPI / 流程 / 日志 / 历史轮次全部可看 |
| 重启 | `UNARCHIVE` action 将状态置为 `INTERRUPTED`，需用户手动 `RESUME_INTERRUPTED` |
| 运行时 executor | 归档前若有 active runner，先 `cancel` 防止脏写 |

### 2.4 Schema 迁移

`studies` 表新增列：

```sql
ALTER TABLE studies ADD COLUMN archived_at TEXT;
ALTER TABLE studies ADD COLUMN archived_by TEXT;
```

### 2.5 API 端点

复用统一 action 入口：

```
POST /api/study/{study_id}/actions/{action_name}
```

请求体（仅 `replace_objective` / `unarchive` 需要）：
```json
{ "reason": "string" }   // 可选：操作原因，记录到 objective_history 或 journal
```

响应：标准 `StudyActionResponse`。

### 2.6 前端集成

| 组件 | 改动 |
|------|------|
| `StudyDetailPage.tsx` | 顶部按钮组：`[⏸ 暂停] [▶ 恢复] [🛑 中止] [📦 归档]` 依据 `available_actions` 动态显示；`ARCHIVED` 状态显示 `[🔄 取消归档]` |
| `StudyTaskList.tsx` | `HistoryCard` 加 3 点菜单（`⋯`）：根据 `available_actions` 显示对应项；顶部加 "显示已归档" toggle |
| `StudyTaskSummary.tsx` | 右上"查看详情"旁加 `⋯` 菜单：至少包含"📦 归档"（终态时显示） |
| `_ACTION_META` | 新增 `"archive": {"label": "归档", "destructive": True}`、`"unarchive": {"label": "取消归档", "destructive": False}` |

### 2.7 测试覆盖

`tests/test_study_actions.py` 新增：

- `test_archive_queued_study` —— `QUEUED` 状态可归档
- `test_archive_running_study_cancels_executor` —— 运行中归档会先 cancel
- `test_archive_terminal_state_keeps_data` —— `COMPLETE` 归档后 `get_study` 仍返回完整数据
- `test_archive_already_archived_rejected` —— 重复归档返回 409
- `test_list_excludes_archived_by_default` —— 默认列表不显示归档
- `test_list_with_include_archived_returns_all`
- `test_unarchive_sets_status_to_interrupted`
- `test_unarchive_from_non_archived_rejected`

## 3. 中途修改研究目标（B: REPLACE_OBJECTIVE）

### 3.1 设计目标

| 属性 | 取值 |
|------|------|
| 生效时机 | **下一轮生效**（与现有 `directive` 机制对齐，避免 round 中途状态错乱） |
| 历史保留 | ✅ 所有旧目标写入 `objective_history` 表，可追溯 |
| 校验 | 复用 `reject_live_execution_objective()`（禁止 live-trading 字样） |
| 乐观锁 | 必须传 `expected_goal_id`（防 stale write，与 `GoalStore.update_goal` 对齐） |
| 跨实体一致性 | study 表 + goal 表同步更新（同一事务） |

### 3.2 数据库 schema

新增表 `objective_history`：

```sql
CREATE TABLE IF NOT EXISTS objective_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    replaced_by TEXT,        -- user/session id
    expected_goal_id TEXT NOT NULL,
    reason TEXT,
    applied_at TEXT NOT NULL,    -- 入队/修改时间
    applied_round INTEGER,        -- NULL=pending, N=第 N 轮已生效
    FOREIGN KEY (study_id) REFERENCES studies(study_id)
);
CREATE INDEX idx_objective_history_study ON objective_history(study_id, applied_at DESC);
```

`studies` 表无需新增列（objective 仍存在 `studies.objective`）。

`goals` 表：复用现有 `objective` 列；同步 `goal_claims.thesis`。

### 3.3 执行流程

```
用户 UI 点 "✏️ 修改目标"
   ↓
POST /api/study/{id}/actions/replace_objective
   {new_objective, expected_goal_id, reason}
   ↓
routers/study.py::study_replace_objective
   ├─ _owned_study(request, study_id)        # IDOR 校验
   ├─ validate: 非空、长度 10..2000
   ├─ reject_live_execution_objective(new)   # 复用 policy
   ├─ StudyStore.queue_objective_replace(
   │     study_id,
   │     new_objective,
   │     expected_goal_id,
   │     replaced_by = session_id,
   │     reason = req.reason,
   │  )
   │  ├─ 事务 1: INSERT objective_history (applied_round=NULL)
   │  ├─ 事务 2: UPDATE studies.objective = new_objective, updated_at=now
   │  ├─ 事务 3: UPDATE goals.objective = new_objective（同步 goal ledger）
   │  └─ 事务 4: UPDATE goal_claims.thesis（同步 active thesis）
   └─ 发出 SSE: study_objective_replaced (history_id)

runner 在 _run_one_round 入口前
   ↓
study_store.mark_pending_objectives_applied(sid, round_num)
   ├─ UPDATE objective_history SET applied_round=? WHERE applied_round IS NULL
   ├─ invalidate_study_cache()                  # 让 _get_study(force=True) 拿到新 objective
   └─ emit study_objective_applied (round_num)
```

### 3.4 数据模型

```python
@dataclass(frozen=True)
class ObjectiveHistoryEntry:
    id: int
    study_id: str
    session_id: str
    objective: str
    replaced_by: str | None
    expected_goal_id: str
    reason: str | None
    applied_at: str
    applied_round: int | None  # None=pending
```

### 3.5 API

```python
class StudyReplaceObjectiveRequest(BaseModel):
    new_objective: str = Field(..., min_length=10, max_length=2000)
    expected_goal_id: str
    reason: str | None = None

class ObjectiveHistoryEntryModel(BaseModel):
    id: int
    study_id: str
    objective: str
    replaced_by: Optional[str]
    expected_goal_id: str
    reason: Optional[str]
    applied_at: str
    applied_round: Optional[int]

class StudyObjectiveHistoryResponse(BaseModel):
    status: str
    study_id: str
    history: list[ObjectiveHistoryEntryModel]

@router.post("/{study_id}/actions/replace_objective",
             response_model=StudyActionResponse)
async def study_replace_objective(...): ...

@router.get("/{study_id}/objective_history",
            response_model=StudyObjectiveHistoryResponse)
async def study_objective_history(...): ...
```

### 3.6 前端集成

**新组件 `EditObjectiveDialog.tsx`**

```
┌──────────────────────────────────────┐
│ ✏️ 修改研究目标                  [×] │
├──────────────────────────────────────┤
│ 当前目标（只读）:                    │
│ ┌──────────────────────────────────┐ │
│ │ 高动量因子选股策略 ...            │ │
│ └──────────────────────────────────┘ │
│                                      │
│ 新目标:                              │
│ ┌──────────────────────────────────┐ │
│ │ 低估值反转因子 ...                │ │
│ │                       8 / 2000   │ │
│ └──────────────────────────────────┘ │
│                                      │
│ 修改原因（可选）:                     │
│ ┌──────────────────────────────────┐ │
│ │ 最近 backtest 显示动量失效 ...    │ │
│ └──────────────────────────────────┘ │
│                                      │
│ ⚠ 新目标将从**下一轮**生效            │
│ 历史目标会自动保留。                  │
│                                      │
│            [取消]   [提交修改]        │
└──────────────────────────────────────┘
```

**`StudyDetailPage.tsx`**
- 顶部按钮组新增 "✏️ 修改目标" 按钮（依据 `canReplaceObjective`）
- 点击 → 打开 `EditObjectiveDialog`
- 成功后：刷新 `summary` + `objectiveHistory` + 跳到 `flow` 标签

**`ObjectiveProgress.tsx`**
- 标题旁加 ⓘ 图标 → 弹出抽屉 `StudyObjectiveHistory`
- 抽屉显示每次变更：
  - 时间 / 操作者 / 目标预览（前 100 字）/ 生效轮次
  - "已生效 ✓" 标签（如果 `applied_round != null`）
  - "等待下一轮生效 ⟳" 标签（如果 `applied_round == null`）

**`client.ts`**
```typescript
study: {
  replaceObjective: (studyId, newObjective, expectedGoalId, reason?) =>
    api.study.dispatchAction(studyId, 'replace_objective', {
      new_objective: newObjective,
      expected_goal_id: expectedGoalId,
      reason,
    }),
  objectiveHistory: (studyId) =>
    api.request<StudyObjectiveHistoryResponse>(
      `/api/study/${studyId}/objective_history`
    ),
}
```

### 3.7 测试覆盖

`tests/test_study_objective.py`（新文件，≥8 个测试）：

- `test_replace_objective_writes_history` —— history 表新增一行
- `test_replace_objective_updates_study_and_goal` —— study.objective + goal.objective 同步
- `test_replace_objective_rejects_empty` —— 空目标返回 400
- `test_replace_objective_rejects_live_trading` —— live-trading 字样返回 400
- `test_replace_objective_rejects_short` —— < 10 字返回 400
- `test_replace_objective_stale_goal_id` —— expected_goal_id 不匹配返回 409
- `test_objective_history_ordered_desc` —— history 按 applied_at DESC
- `test_pending_objective_marked_applied_in_next_round` —— runner 集成测试

## 4. 流程显示优化（C: 视觉升级，保留 ReactFlow）

### 4.1 优化目标

保留 ReactFlow（已被用户在上一次迭代中确认接受），只升级视觉/交互：

| 维度 | 现状 | 升级后 |
|------|------|--------|
| 节点状态 | 3 态（pending/running/done） | 5 态（pending/running/done/error/skipped） |
| 当前活动节点 | 边框略变 | ring + animate-pulse + 角标序号 |
| 进度条 | 底部小条 + 9px 字号 | 大色条 + 百分比 + 耗时 + ETA |
| 节点耗时 | 无 | 每节点卡片底部 ⏱ Xs |
| 轮次导航 | 只看当前轮 | 顶部下拉切换历史轮（≥2 轮时显示） |
| 轮次标识 | 顶部小字 | 顶部大字号 "Round X / Y" + 状态色 |
| 整体风格 | 散乱 | 集中封装 `StudyPipelineHeader/Footer` |

### 4.2 不变更

- ReactFlow 库版本
- `layoutWithWrapping` 算法（4 个一行，蛇形）
- DAGNode / DAGEdge 内部组件名（保留 import 兼容）
- 后端 API（`round_manifest` 已存在）

### 4.3 组件拆分

```
AgentFlowCanvas.tsx (主容器)
├── StudyPipelineHeader       (新) - 轮次选择 + 标题 + 刷新
├── ReactFlow                 (保留)
│   ├── DAGNode / DAGEdge     (扩展)
│   └── Background + Controls
└── StudyPipelineFooter       (新) - 进度条 + 耗时 + ETA
```

### 4.4 节点视觉规范

| 状态 | 边框 | 背景 | 角标 | 动效 |
|------|------|------|------|------|
| pending | `border-slate-700` | `bg-slate-900/40` | — | — |
| running | `border-cyan-400` | `bg-cyan-500/10` | `ring-2 ring-cyan-400/60` | `animate-pulse` |
| done | `border-emerald-500/60` | `bg-emerald-500/10` | ✓ + 序号 #N | — |
| error | `border-rose-500` | `bg-rose-500/10` | ⚠ | — |
| skipped | `border-slate-800` | `bg-slate-900/20 opacity-50` | — | — |

每节点卡片内容：

```
┌────────────────┐
│ [✓] #3         │   ← 完成序号 / 当前图标
│                │
│  Researcher    │   ← agent 名称
│                │
│  ⏱ 12.3s       │   ← 耗时（manifest.duration_s）
└────────────────┘
```

### 4.5 顶部头部

```
┌──────────────────────────────────────────────────────────┐
│ ⏱ Round 2 / 5 [切换▾]      ⟳ 刷新   ✓6/8 (75%)        │
└──────────────────────────────────────────────────────────┘
```

- 左侧：大字号 `Round 2 / 5`
- 中间：轮次下拉（仅当 `rounds.length >= 2` 时显示）
- 右侧：完成度大字号（动态绑定 status 色）

### 4.6 底部进度条

```
┌──────────────────────────────────────────────────────────┐
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░  75%       │
│                                  耗时 12.3s · ETA 5.2s    │
└──────────────────────────────────────────────────────────┘
```

- 大色条（高度从 4px → 6px）
- 渐变色（已完成为 emerald，进行中为 cyan，未开始为 slate）
- 百分比 + 总耗时（sum of `duration_s`）+ ETA（剩余节点 × 平均耗时）

### 4.7 测试

`webui/frontend/src/components/study/__tests__/AgentFlowCanvas.test.tsx`（可选）：

- 状态映射：done/running/pending/error/skipped → 对应 className
- 进度计算：8 节点中 6 done → 75%
- 轮次下拉：只有 1 轮时不渲染

视觉快照：playwright 截图（可选）

## 5. 实施计划

| 步骤 | 内容 | 工时 | 顺序 |
|------|------|------|------|
| A1 | 后端：`ARCHIVED` 状态 + `ARCHIVE/UNARCHIVE` action + store 方法 | 0.5 天 | 1 |
| A2 | 前端：DetailPage 按钮 + TaskList 菜单 + Summary 三点菜单 | 0.3 天 | 2 |
| A3 | 测试：`test_study_actions.py` 8 个新测试 | 0.2 天 | 3 |
| B1 | 后端：`objective_history` 表 + `REPLACE_OBJECTIVE` action + store + router | 1 天 | 4 |
| B2 | 前端：`EditObjectiveDialog` + `StudyObjectiveHistory` 抽屉 | 0.5 天 | 5 |
| B3 | runner：下一轮生效时 mark pending + invalidate cache | 0.3 天 | 6 |
| B4 | 测试：`test_study_objective.py` 8 个新测试 | 0.2 天 | 7 |
| C1 | 前端：节点视觉升级 + 5 状态 + 耗时 | 0.5 天 | 8 |
| C2 | 前端：`StudyPipelineHeader/Footer` 拆分 + 轮次选择器 | 0.5 天 | 9 |
| C3 | 测试：组件单元测试 | 0.2 天 | 10 |
| D | E2E + 文档 + changelog | 0.3 天 | 11 |
| **合计** | | **4.5 天** | |

## 6. 风险点

| 风险 | 影响 | 缓解 |
|------|------|------|
| 归档 active executor 时数据竞争 | runner 可能写出 race | `archive()` 先调 `cancel()` 再改状态 |
| 目标修改后 runner 缓存读到旧值 | 当前轮仍用旧目标 | `_run_one_round` 入口 `invalidate_study_cache()` + `mark_pending_applied` |
| `round_manifest` 还未生成 | 新 study 第一轮会 404 | 已有 `catch {}` 容错，加 loading state |
| openapi-typescript 与后端不同步 | 前端类型错误 | 改后端后必须 `npm run gen:types` |
| 视觉升级破坏现有布局 | DAGNode 高度/宽度变化 | 保持 `nodeWidth=180 / nodeHeight=80` 不变 |

## 7. 关联文档

- `docs/archive/undated/study-longhorizon-v2-design.md` —— study v2 整体设计
- `docs/archive/undated/study-ui-improvement.md` —— study UI 改进（导航/标签/聊天日志）
- `docs/archive/undated/study-page-three-columns.md` —— study 三栏布局
- `docs/archive/undated/aegis-implementation-plan.md` —— AEGIS 早停机制