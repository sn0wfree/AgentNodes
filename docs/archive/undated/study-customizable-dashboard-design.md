# Study 详情页可配置仪表板（Customizable Study Dashboard）

> 状态: 设计中
> 日期: 2026-08-19
> 前置讨论: study 子页面优化 — 用户需要自定义页面内容

## 1. 问题

当前 `StudyDetailPage` 使用**固定布局**，所有 widget 无条件显示。用户反馈：

- "现在都不知道一个任务正在干什么，一头雾水"
- 无法隐藏不需要的信息
- 无法调整 widget 顺序
- 没有实时进度反馈（仅 10 秒轮询）

根本原因：布局硬编码在 `StudyDetailPage.tsx`（682 行），没有配置层。

## 2. 目标

**用户可配置的仪表板**：用户决定页面上显示哪些 widget、以什么顺序排列。配置持久化到 localStorage，刷新后保持。

### 2.1 核心能力

| 能力 | 描述 |
|------|------|
| Widget 开关 | 勾选/取消勾选来显示/隐藏 widget |
| 顺序调整 | 拖拽排序 widget 的显示顺序 |
| 宽度调整 | 每个 widget 占 1-12 列（grid units） |
| 配置持久化 | 保存到 localStorage，刷新后保持 |
| 恢复默认 | 一键恢复初始布局 |
| 编辑模式 | 点击按钮进入/退出编辑模式 |

### 2.2 不做的

- 跨行定位（行号自动推算）
- 响应式断点（CSS Grid 自动处理）
- 后端存储布局（纯前端 localStorage）
- 跨 study 共享布局（每个 study 独立配置）

## 3. 架构

### 3.1 数据模型

```ts
// Widget 类型定义
interface WidgetDef {
  id: string                    // "live-activity"
  label: string                 // "实时活动"
  icon: string                  // "⚡"
  defaultEnabled: boolean       // 默认是否显示
  defaultSpan: number           // 默认宽度 (1-12)
  minSpan?: number              // 最小宽度
  maxSpan?: number              // 最大宽度
  component: React.FC<WidgetProps>  // 渲染组件
}

// Widget 实例配置
interface DashboardWidget {
  id: string                    // 唯一实例 ID
  type: string                  // widget 类型 → registry 查找
  enabled: boolean              // 是否显示
  span: number                  // 宽度 (1-12 grid units)
  order: number                 // 排序位置
  config?: Record<string, any>  // widget 特定配置
}

// 整体布局配置
interface DashboardConfig {
  version: number               // 配置版本（用于迁移）
  columns: number               // grid 列数 (固定 12)
  widgets: DashboardWidget[]    // widget 列表
}
```

### 3.2 Widget Props

```ts
interface WidgetProps {
  studyId: string
  summary: StudySummaryResponse
  config?: Record<string, any>
}
```

所有 widget 统一接收 `studyId` 和 `summary`，内部自行获取所需数据。

### 3.3 文件结构

```
webui/frontend/src/
├── components/study/
│   ├── dashboard/
│   │   ├── types.ts              # WidgetDef, DashboardConfig 类型
│   │   ├── registry.ts           # WIDGET_REGISTRY 注册表
│   │   ├── defaultLayout.ts      # 默认布局配置
│   │   ├── DashboardGrid.tsx     # 网格渲染器
│   │   ├── WidgetPicker.tsx      # 编辑面板（侧边栏）
│   │   ├── WidgetCard.tsx        # 单个 widget 卡片容器
│   │   └── widgets/
│   │       ├── LiveActivity.tsx      # 实时活动面板
│   │       ├── EventTimeline.tsx     # SSE 事件时间线
│   │       ├── KnowledgeView.tsx     # 知识库 markdown
│   │       ├── TodosView.tsx         # 待办 markdown
│   │       └── JournalView.tsx       # 日志 markdown
│   └── StudyDetailPage.tsx       # 修改：集成 DashboardGrid
├── stores/
│   └── studyDashboard.ts         # 布局配置 store
└── hooks/sse/
    └── studyHandlers.ts          # 修改：接通所有 SSE 事件
```

## 4. Widget 注册表

### 4.1 可用 Widget

| ID | 标签 | 来源 | 默认 | Span | 说明 |
|----|------|------|------|------|------|
| `live-activity` | 实时活动 | **新建** | ✅ | 12 | 阶段/Agent/耗时 |
| `objective` | 目标进度 | ObjectiveProgress | ✅ | 8 | 目标+进度+证据 |
| `dag-flow` | 研究流程 | AgentFlowCanvas | ✅ | 12 | DAG 可视化 |
| `round-history` | 轮次历史 | RoundHistory | ✅ | 6 | 最近 N 轮 |
| `metrics-compare` | 指标对比 | MetricsCompare | ✅ | 6 | 轮次间对比 |
| `metrics-trend` | 指标趋势 | MetricsTrendChart | ☐ | 12 | 折线图 |
| `budget` | 预算 | BudgetBar | ☐ | 12 | 用量/上限 |
| `scoreboard` | 杠杆精度 | ScoreboardMini | ☐ | 12 | 精度表 |
| `event-timeline` | 事件流 | **新建** | ☐ | 12 | SSE 事件时间线 |
| `directives` | 指令 | (inline) | ☐ | 4 | 输入+审计 |
| `task-info` | 任务信息 | (inline) | ☐ | 4 | 路径/日期 |
| `hanging-events` | 异常事件 | (inline) | ☐ | 4 | 看门狗 |
| `agent-chat` | Agent 群聊 | AgentChatLog | ☐ | 12 | 按轮次查看 |
| `knowledge` | 知识库 | **新建** | ☐ | 12 | knowledge.md |
| `todos` | 待办 | **新建** | ☐ | 12 | todos.md |
| `journal` | 日志 | **新建** | ☐ | 12 | journal.md |

### 4.2 默认布局

```
┌──────────────────────────────────────────────┐
│ live-activity (span=12)                       │
├──────────────────────┬───────────────────────┤
│ objective (span=8)   │ directives (span=4)   │
├──────────────────────┴───────────────────────┤
│ dag-flow (span=12)                            │
├──────────────────────┬───────────────────────┤
│ metrics-compare (6)  │ round-history (6)     │
├──────────────────────┴───────────────────────┤
│ scoreboard (span=12)                          │
├──────────────────────┬───────────────────────┤
│ task-info (4)        │ hanging-events (4)    │
│                      │ budget (4)            │
└──────────────────────┴───────────────────────┘
```

## 5. 编辑模式

### 5.1 UI 设计

编辑模式下，页面左侧滑出 Widget Picker 面板：

```
┌─────────┬──────────────────────────────────────┐
│ Widget  │  Dashboard Grid (实时预览)             │
│ Picker  │  ┌──────┐ ┌──────┐ ┌──────────┐     │
│         │  │      │ │      │ │          │     │
│ ☑ 实时   │  └──────┘ └──────┘ └──────────┘     │
│ ☑ 目标   │  ┌────────────────┐ ┌─────────┐     │
│ ☑ DAG   │  │                │ │         │     │
│ ☐ 趋势   │  └────────────────┘ └─────────┘     │
│ ☐ 预算   │                                      │
│ ...     │                                      │
│         │                                      │
│ [恢复默认]│                                      │
└─────────┴──────────────────────────────────────┘
```

### 5.2 交互

1. 点击"编辑布局"按钮 → 进入编辑模式，左侧显示 Widget Picker
2. Widget Picker 中：
   - Toggle 开关控制 widget 显示/隐藏
   - 拖拽手柄调整顺序
   - Span 滑块调整宽度（1-12）
3. 网格实时反映配置变化
4. 点击"完成"或点击外部 → 退出编辑模式，配置已保存

### 5.3 持久化

```ts
// localStorage key
const STORAGE_KEY = (studyId: string) => `sr-study-dashboard-${studyId}`

// 保存
localStorage.setItem(STORAGE_KEY(studyId), JSON.stringify(config))

// 读取
const saved = localStorage.getItem(STORAGE_KEY(studyId))
const config = saved ? JSON.parse(saved) : getDefaultLayout()
```

## 6. SSE 接通

### 6.1 Store 扩展

`useStudyStore` 新增字段：

```ts
// Live activity tracking
currentPhase: string | null        // "researcher" | "execution" | "evaluation"
currentAgent: string | null        // "data_quality" | "strategist"
phaseStartedAt: number | null      // Date.now()
nodeStatuses: Record<string, NodeStatus>  // per-node DAG status
recentEvents: LiveEvent[]          // last 20 events
```

### 6.2 SSE Handler 补全

| 事件 | 动作 |
|------|------|
| `study_phase` | 更新 currentPhase, 添加事件 |
| `study_agent_complete` | 更新 nodeStatuses, 添加事件 |
| `study_graph_node` | 更新 nodeStatuses |
| `study_knowledge_*` | 添加事件 |
| `study_review` | 添加事件 |
| `study_parse_retry` | 添加事件, 弹 toast |
| `study_evidence` | 添加事件 |
| `study_directive_added` | 添加事件 |
| `study_objective_applied` | 添加事件 |

### 6.3 LiveActivity Widget

显示三栏信息：

```
┌─────────────────┬──────────────────┬─────────────────┐
│ 📍 当前阶段      │ 🤖 当前 Agent     │ ⏱ 已用时间       │
│ ● 研究           │ data_quality     │ 2m 30s          │
│   ○ 回测         │ (数据质量分析)     │                 │
│   ○ 评估         │                  │                 │
└─────────────────┴──────────────────┴─────────────────┘
```

### 6.4 EventTimeline Widget

紧凑的时间线：

```
14:32:05  ✓ Researcher 完成 (45s)
14:32:50  ✓ Backtest 完成 (1m 20s)
14:32:52  ● Data Quality 运行中
14:33:10  📝 知识更新: +2 条
```

## 7. 实施步骤

| 步骤 | 内容 | 文件 | 依赖 |
|------|------|------|------|
| 1 | 类型定义 | `dashboard/types.ts` | 无 |
| 2 | Widget 注册表 | `dashboard/registry.ts` | Step 1 |
| 3 | 默认布局 | `dashboard/defaultLayout.ts` | Step 1 |
| 4 | 布局 Store | `stores/studyDashboard.ts` | Step 1 |
| 5 | WidgetCard 容器 | `dashboard/WidgetCard.tsx` | Step 1 |
| 6 | DashboardGrid 渲染器 | `dashboard/DashboardGrid.tsx` | Step 2-5 |
| 7 | WidgetPicker 编辑面板 | `dashboard/WidgetPicker.tsx` | Step 4 |
| 8 | StudyDetailPage 集成 | `StudyDetailPage.tsx` | Step 6-7 |
| 9 | 新建 widget 组件 | `dashboard/widgets/*` | Step 1 |
| 10 | SSE store 扩展 | `stores/study.ts` | 无 |
| 11 | SSE handler 接通 | `hooks/sse/studyHandlers.ts` | Step 10 |
| 12 | LiveActivity widget | `dashboard/widgets/LiveActivity.tsx` | Step 10-11 |
| 13 | EventTimeline widget | `dashboard/widgets/EventTimeline.tsx` | Step 10-11 |

## 8. 验证清单

- [ ] 打开 study 详情页 → 看到默认 widget 布局
- [ ] 点"编辑布局" → 侧边栏弹出 widget 列表
- [ ] 关闭"指标趋势" → 网格中该 widget 消失
- [ ] 拖拽"预算"到第三位 → 顺序变化
- [ ] 调整 widget 宽度 → 网格实时更新
- [ ] 刷新页面 → 布局保持
- [ ] 点"恢复默认" → 回到初始布局
- [ ] 运行中的 study → 实时显示阶段/Agent/耗时
- [ ] SSE 事件 → 事件时间线实时更新
- [ ] SSE 断开 → 轮询 fallback 正常

## 9. 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| localStorage 配置损坏 | 页面无法渲染 | catch + reset to default |
| Widget 数量增多导致性能问题 | 渲染慢 | lazy load + virtual scroll |
| SSE 事件过多导致频繁重绘 | 卡顿 | debounce + batch update |
| 配置版本不兼容 | 旧配置无法读取 | version 字段 + migration |
