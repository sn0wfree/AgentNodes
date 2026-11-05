# Study RightPanel 设计文档

## 概述

增强 RightPanel 的 Study tab，添加流程卡片、Round 历史、Scoreboard 等功能。
保持现有 slate-900/slate-700 暗色卡片风格。

## 区块布局

### 1. Status Bar（状态栏）
```
┌────────────────────────────────────────────────────────┐
│ ● 运行中    Round 3/5                   [暂停] [取消]  │
└────────────────────────────────────────────────────────┘
```

### 2. 目标 · 进度（合并 Objective + Goal）
```
┌────────────────────────────────────────────────────────┐
│ 目标 · 进度                                           │
│                                                        │
│ 自动因子研究：验证动量+波动率因子组合                    │
│                                                        │
│  ━━━━━━━━━━━━━━━━░░░░░░░░░░  50%   (2/4 证据)        │
│                                                        │
│  ● Calmar >= 0.5    ● Sharpe >= 0.3                   │
│  ○ MaxDD >= -0.15   ○ 年化收益 > 0                    │
└────────────────────────────────────────────────────────┘
```

### 3. 当前流程（3 步聚焦）
```
┌────────────────────────────────────────────────────────┐
│ ▸ 当前流程 · Round 3                                  │
│                                                        │
│  ✓ FactorAnalyst       15:44  3.2s                     │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
│  ◐ Strategist          15:45  ⏳ ...                   │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
│  ○ Portfolio                                            │
│                                                        │
│  3/9 步骤  ·  进度 ━━━━━━━━━░░░░░░░░░░  33%            │
└────────────────────────────────────────────────────────┘
```

节点状态：
- `●` 完成（绿色实心 `bg-emerald-500`）
- `◐` 执行中（蓝色脉冲 `animate-pulse bg-sky-500`）
- `○` 等待（灰色空心 `border-slate-600`）
- 连线：实线（完成）`border-slate-500` / 虚线（等待）`border-dashed border-slate-700`

### 4. Round 历史（可内联展开）
```
┌────────────────────────────────────────────────────────┐
│ ▸ Round 历史                                           │
│                                                        │
│  ▸ R3  15:45  discard  C:0.45 S:0.30 D:-0.12         │
│  ▸ R2  15:30  keep ✓   C:0.52 S:0.35 D:-0.10         │
│  ▸ R1  15:15  discard  C:0.38 S:0.25 D:-0.15         │
└────────────────────────────────────────────────────────┘
```

展开后：
```
│  ▾ R3  15:45  discard  C:0.45 S:0.30 D:-0.12         │
│    ┌──────────────────────────────────────────────┐    │
│    │ ⚠️ ts_std(returns, 20)                      │    │
│    │    returns 列不存在，可用列: [close]          │    │
│    │    建议: ts_std(close, 20)                   │    │
│    │                                              │    │
│    │ Agent 输出: [展开]                            │    │
│    └──────────────────────────────────────────────┘    │
```

### 5. Scoreboard
```
┌────────────────────────────────────────────────────────┐
│ ▸ Scoreboard                                           │
│                                                        │
│  integrate  ████████████░░  0.72  10/14               │
│  remove     ██████░░░░░░░░  0.45   5/11               │
│  optimize   ████████░░░░░░  0.58   7/12               │
│                                                        │
│  精度趋势 ↑↑↓↑                                       │
└────────────────────────────────────────────────────────┘
```

### 6. 查看详细按钮
```
┌────────────────────────────────────────────────────────┐
│ [查看详细 →]                                           │
└────────────────────────────────────────────────────────┘
```

### 7. 注入指令
```
┌────────────────────────────────────────────────────────┐
│ 注入研究方向                                          │
│ ┌──────────────────────────────────────────────────┐  │
│ │ 改成动量因子 + 减小 top_n                        │  │
│ └──────────────────────────────────────────────────┘  │
│ [提交指令]                                            │
└────────────────────────────────────────────────────────┘
```

## 组件结构

```
components/study/
├── StudyTab.tsx              # 容器：空状态 / 创建表单 / 进度视图
├── StudyCreateForm.tsx       # 创建表单（保持不变）
├── StudyProgress.tsx         # 进度视图容器（重构）
├── StatusBadge.tsx           # 状态 badge 组件
├── ObjectiveProgress.tsx     # 目标·进度区块（合并）
├── FlowCard.tsx              # 流程卡片（3步聚焦）
├── FlowNode.tsx              # 单个流程节点
├── RoundHistory.tsx          # Round 历史列表
├── RoundItem.tsx             # 单个 Round（可展开）
├── ScoreboardMini.tsx        # Scoreboard 概览
└── FactorFailureBanner.tsx   # 因子失败横幅
```

## 数据流

### SSE 实时更新
- `study_round` → 更新 metrics/verdict
- `agent_started` / `agent_done` → 更新流程节点状态
- `study_completed` / `study_failed` → 更新状态

### 轮询获取完整数据
- `/api/study/{id}/summary` → recent_rounds + scoreboard + factor_failures
- 页面可见时每 10 秒轮询

## TypeScript 类型

```typescript
// 流程节点状态
type NodeStatus = 'pending' | 'running' | 'done'

interface FlowNodeData {
  id: string
  label: string
  status: NodeStatus
  started_at?: string
  duration_ms?: number
}

// Round 历史
interface StudyRoundSummary {
  round_num: number
  run_name: string
  metrics: { calmar?: number; sharpe?: number; max_dd?: number }
  verdict: string
  created_at: string
  factor_failures?: FactorFailure[]
}

// Scoreboard
interface LeverScoreSummary {
  lever: string
  precision_mean: number
  attempts: number
  accepted: number
  precision_history: number[]
}

// 因子失败
interface FactorFailure {
  factor_name: string
  factor_code: string
  error: string
  available_columns?: string[]
  suggested_fix?: string
}

// Study 摘要（轮询用）
interface StudySummary {
  study_id: string
  execution_status: string
  current_round: number
  max_rounds?: number
  objective: string
  last_metrics?: Record<string, number>
  last_verdict?: string
  recent_rounds: StudyRoundSummary[]
  scoreboard: LeverScoreSummary[]
  goal_snapshot?: {
    evidence_count: number
    progress_percent: number
    criteria: Array<{ criterion_id: string; text: string; status: string }>
  }
}
```

## 后端 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /api/study/{id}/summary` | GET | Study 摘要（rounds + scoreboard + goal） |

## 实施顺序

1. 后端 API: `/api/study/{id}/summary`
2. 前端类型定义
3. FlowCard + FlowNode 组件
4. ObjectiveProgress 组件
5. RoundHistory + RoundItem 组件
6. ScoreboardMini 组件
7. StudyProgress 重构整合
8. SSE 事件处理增强
