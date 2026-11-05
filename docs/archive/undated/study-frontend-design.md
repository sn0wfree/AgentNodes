# Study 前端设计文档

## 背景

当前 Study tab 只显示基础状态（round、metrics、verdict），缺少：
- Round 历史记录
- Journal 日志（假设→归因→结果）
- Scoreboard 记分牌（杠杆精度追踪）
- Factor 失败详情

用户需要：RightPanel 简要视图 + 全屏详细视图，SSE + 混合更新策略。

## 架构设计

### 数据层

#### 新增 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/study/{study_id}/rounds` | GET | 获取 round 历史（分页） |
| `/api/study/{study_id}/rounds/{round_num}` | GET | 获取单轮详情（agent outputs） |
| `/api/study/{study_id}/journal` | GET | 获取 journal 条目（分页） |
| `/api/study/{study_id}/scoreboard` | GET | 获取杠杆记分牌 |
| `/api/study/{study_id}/factor-failures` | GET | 获取因子失败详情 |

#### 新增 SSE 事件

| 事件 | 载荷 | 说明 |
|------|------|------|
| `study_round_completed` | {round, metrics, verdict, factor_failures} | 轮次完成 |
| `study_journal_updated` | {goal_id, entries} | journal 更新 |
| `study_scoreboard_updated` | {lever, precision} | scoreboard 更新 |

### 前端组件

#### 1. RightPanel Study Tab（简要视图）

**现有组件增强：**
- `StudyProgress` — 增加 round 历史 timeline
- 新增 `RoundSummary` — 显示最近 3 轮的 metrics/verdict
- 新增 `ScoreboardMini` — 杠杆精度概览（3-5 个杠杆）

**布局：**
```
┌─────────────────────────┐
│ Status: Running  Round: 3│
│ Objective: ...           │
├─────────────────────────┤
│ Current Metrics          │
│ Calmar: 0.45  Sharpe: 0.3│
│ Verdict: discard         │
├─────────────────────────┤
│ Recent Rounds (3)        │
│ R3: discard calmar=0.45  │
│ R2: keep    calmar=0.52  │
│ R1: discard calmar=0.38  │
├─────────────────────────┤
│ Scoreboard               │
│ integrate: 0.72 (10/14)  │
│ remove:    0.45 (5/11)   │
├─────────────────────────┤
│ [View Details →]         │
│ [Pause] [Cancel]         │
│ [Directive: ______]      │
└─────────────────────────┘
```

#### 2. 全屏 Study 页面（详细视图）

**新增路由：** `/study/:studyId`

**组件结构：**
```
StudyDetailPage
├── StudyHeader (status, objective, controls)
├── Tabs
│   ├── Tab: Rounds (default)
│   │   └── RoundHistory
│   │       └── RoundCard × N
│   │           ├── Metrics (calmar/sharpe/max_dd)
│   │           ├── Verdict badge
│   │           ├── Factor Failures (if any)
│   │           └── Agent Outputs (collapsible)
│   ├── Tab: Journal
│   │   └── JournalTimeline
│   │       └── JournalEntry × N
│   │           ├── Hypothesis
│   │           ├── Predicted Affected
│   │           ├── Attribution (flipped/reverted/novel)
│   │           └── Outcome
│   ├── Tab: Scoreboard
│   │   └── ScoreboardTable
│   │       ├── Lever name
│   │       ├── Precision (posterior mean)
│   │       ├── Attempts / Accepted
│   │       └── Trend (sparkline)
│   └── Tab: Factor Failures
│       └── FactorFailureList
│           └── FactorFailureCard × N
│               ├── Expression
│               ├── Error message
│               ├── Available columns
│               └── Suggested fix
└── DirectiveHistory
    └── DirectiveItem × N
```

**布局：**
```
┌──────────────────────────────────────────────────────────┐
│ ← Back    Study: AEGIS 验证测试    Status: Running      │
│ Objective: 自动因子研究...        Round: 3/5             │
├──────────────────────────────────────────────────────────┤
│ [Rounds] [Journal] [Scoreboard] [Factor Failures]        │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Round 3 (2026-08-04 15:45)                              │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Metrics: Calmar 0.45 | Sharpe 0.30 | MaxDD -0.12 │  │
│  │ Verdict: discard                                    │  │
│  │                                                    │  │
│  │ Factor Failures:                                   │  │
│  │  ⚠️ ts_std(returns, 20): returns 列不存在         │  │
│  │     可用列: [close]  建议: ts_std(close, 20)       │  │
│  │                                                    │  │
│  │ Agent Outputs: [expand]                            │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  Round 2 (2026-08-04 15:30)                              │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Metrics: Calmar 0.52 | Sharpe 0.35 | MaxDD -0.10 │  │
│  │ Verdict: keep ✓                                     │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  Round 1 (2026-08-04 15:15)                              │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Metrics: Calmar 0.38 | Sharpe 0.25 | MaxDD -0.15 │  │
│  │ Verdict: discard                                    │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 数据更新策略

#### SSE 实时推送（关键事件）
- `study_round` — 轮次完成时推送 metrics/verdict
- `study_completed` / `study_failed` — 状态变更
- `study_round_completed` — 新增：推送 factor_failures + agent_outputs 摘要

#### 轮询（完整数据）
- `/api/study/{study_id}/rounds` — 每 10 秒轮询（页面可见时）
- `/api/study/{study_id}/journal` — 页面切换到 Journal tab 时加载
- `/api/study/{study_id}/scoreboard` — 页面切换到 Scoreboard tab 时加载

#### 混合策略
1. SSE 推送关键事件 → 立即更新 UI（round 数字、metrics、verdict）
2. 轮询获取完整数据 → 填充详细视图（agent outputs、factor failures）
3. 页面不可见时暂停轮询，恢复时立即刷新

### 新增 TypeScript 类型

```typescript
// Round 历史
interface StudyRound {
  round_num: number
  run_name: string
  metrics: Record<string, number>
  verdict: string
  created_at: string
  factor_failures?: FactorFailure[]
}

// Journal 条目
interface JournalEntry {
  goal_id: string
  session_id: string
  round_num: number
  hypothesis: string
  predicted_affected: string[]
  levers: string[]
  gating_outcome: string  // accepted | reverted
  attribution: Record<string, string>  // lever → {flipped, still_F, reverted, novel}
  created_at: string
}

// 杠杆记分牌
interface LeverScore {
  lever: string
  precision_mean: number  // Beta 后验均值
  attempts: number
  accepted: number
  reverted: number
  precision_history: number[]  // 最近 N 轮的 precision
}

// 因子失败
interface FactorFailure {
  factor_name: string
  factor_code: string
  error: string
  available_columns?: string[]
  suggested_fix?: string
}
```

### 文件结构

```
webui/frontend/src/
├── api/
│   └── client.ts              # 新增 study.rounds/journal/scoreboard API
├── components/
│   ├── study/
│   │   ├── StudyTab.tsx        # 增强：添加 "View Details" 按钮
│   │   ├── StudyProgress.tsx   # 增强：添加 RoundSummary + ScoreboardMini
│   │   ├── StudyCreateForm.tsx # 保持不变
│   │   ├── RoundSummary.tsx    # 新增：最近 3 轮概览
│   │   ├── ScoreboardMini.tsx  # 新增：杠杆精度概览
│   │   └── study-detail/       # 新增：全屏详细视图
│   │       ├── StudyDetailPage.tsx
│   │       ├── RoundHistory.tsx
│   │       ├── RoundCard.tsx
│   │       ├── JournalTimeline.tsx
│   │       ├── JournalEntryCard.tsx
│   │       ├── ScoreboardTable.tsx
│   │       ├── ScoreboardRow.tsx
│   │       ├── FactorFailureList.tsx
│   │       └── FactorFailureCard.tsx
│   └── layout/
│       └── RightPanel.tsx      # 增强：添加全屏链接
├── hooks/
│   └── sse/
│       └── studyHandlers.ts    # 增强：处理新 SSE 事件
├── stores/
│   └── study.ts               # 增加：rounds, journal, scoreboard 状态
└── App.tsx                    # 增强：添加 /study/:studyId 路由
```

## 实施优先级

| 优先级 | 任务 | 工作量 |
|--------|------|--------|
| P0 | 后端 API 端点（rounds, journal, scoreboard, factor-failures） | 中 |
| P0 | StudyProgress 增强（RoundSummary + ScoreboardMini） | 小 |
| P0 | SSE 事件 study_round_completed | 小 |
| P1 | 全屏 StudyDetailPage 路由 + 布局 | 中 |
| P1 | RoundHistory + RoundCard | 中 |
| P1 | JournalTimeline + JournalEntryCard | 中 |
| P1 | ScoreboardTable | 中 |
| P2 | FactorFailureList + FactorFailureCard | 小 |
| P2 | DirectiveHistory | 小 |
| P2 | 轮询优化（可见性检测） | 小 |

## 验证方法

1. 创建 study → 验证 RightPanel 显示实时状态
2. Round 完成 → 验证 SSE 推送更新 UI
3. 点击 "View Details" → 验证全屏页面加载正确
4. 切换 tab → 验证 journal/scoreboard 数据加载
5. 检查 factor failures 显示正确的错误信息和建议修复

## 相关文件

- `webui/frontend/src/components/study/` — Study UI 组件
- `webui/frontend/src/stores/study.ts` — Study Zustand store
- `webui/frontend/src/api/client.ts` — API 客户端
- `webui/frontend/src/hooks/sse/studyHandlers.ts` — SSE 事件处理
- `src/strategy_research/api/routers/study.py` — 后端 Study API
- `src/strategy_research/core/study/store.py` — Study 数据库操作
- `src/strategy_research/core/goal/store.py` — Journal 数据库操作
