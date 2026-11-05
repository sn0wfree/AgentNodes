# Study 页面三栏 Dashboard 重构设计

> 状态: 实施中 · 关联: webui/frontend/src/pages/StudyPage.tsx
> 目标: 将 `/study` 从"当前会话 + 历史列表"重构为业界常见的三栏任务工作台
> 业界参考: Linear（三栏任务工作台）、W&B runs（实验对比表）、GitHub Actions（轮次时间线）、LangSmith（列表 + 摘要面板）

## 1. 设计目标

- **三栏信息架构**：创建（左）→ 任务列表（中）→ 任务摘要（右），符合任务工作台心智模型
- **创建交互**：聊天输入框风格，输入研究目标后自动预览配置，一键启动
- **列表语义**：全部任务 + 状态筛选，运行中置顶 + 呼吸点，选中态清晰
- **摘要只读**：右栏为静态快照（不轮询、无操作），操作统一收敛到详情页
- **详情页**：轮次历史升级为 verdict 色纵向时间线（GitHub Actions 风格），meta 移入右栏

## 2. 布局结构

```
PageShell
└── grid xl:grid-cols-[320px_minmax(0,1fr)_340px]
    ├── 左栏 StudyCreatePanel    (创建)
    ├── 中栏 StudyTaskList       (任务列表, flex-1)
    └── 右栏 StudyTaskSummary    (选中任务静态摘要)
```

响应式：`xl` 三栏 → `lg` 两栏（列表 + 摘要）→ 单栏堆叠（移动端）。

## 3. 组件设计

### 3.1 StudyCreatePanel（左栏，新建）
- 标题"新建研究任务" + 图标
- textarea 输入研究目标（Composer 风格圆角）
- 复用 `StrategyNameInput`：objective 变化 → 自动生成策略名（debounce 300ms）+ 手动修改 + 重新生成
- 折叠"高级参数"（默认收起）：验收指标（默认 calmar/sharpe/max_dd）、轮数预算、最大轮数、监控间隔、Behavior —— 从 `StudyCreateForm` 迁移
- 主按钮"启动 study"（primary 渐变）
- 成功回调：`onCreated(studyId)` → 父组件清空输入、刷新列表、选中新任务
- 无 session 时：显示引导文案（同现状"先在聊天页选择 session"）

### 3.2 StudyTaskList（中栏）
- 顶部：标题"任务列表" + 计数 + 刷新按钮
- 筛选 chips：`全部 | 进行中 | 已完成`（前端过滤，`ACTIVE_STATUSES = [running, queued, monitoring, paused, interrupted, budget_limited]`）
- 排序：进行中置顶 → `updated_at` 倒序
- 卡片沿用现有 `HistoryCard` 设计（objective + strategy badge + C/S/DD 迷你指标 + 状态 pill + Round + verdict + 时间）
- 选中态：`border-primary-500/60 ring-1 ring-primary-500/30 bg-primary-500/5`
- 点击 → `onSelect(study)`

### 3.3 StudyTaskSummary（右栏，静态）
- 无选中：EmptyState 引导"选择任务查看摘要"
- 有选中：一次 `GET /study/{id}/summary`：
  - objective + 状态 pill + verdict 徽章
  - 迷你进度条（progress_percent + evidence_count）
  - Round x/y
  - `MetricsCompare`（最近轮次指标对比，每列最优 ◆ 高亮）
  - meta（策略名/创建/更新）
  - 底部 `查看完整运行状况 →` → `/study/{id}`
- 选中切换时 `useEffect([selectedId])` 重新加载；不轮询

### 3.4 RoundHistory 时间线升级（详情页）
- 纵向时间线：左轴 verdict 色圆点（keep 翡翠 / review 琥珀 / discard 灰）+ 垂直连线；进行中当前轮 pulse
- 右侧：R号 + 时间 + run_name + verdict 徽章 + C·S·D 指标 + 展开因子失败详情
- 保留语义锚点：`Round 历史` / `暂无历史记录` / `title="查看回测产物"` / 当前行 `bg-slate-800/50`（测试兼容）

### 3.5 StudyDetailPage meta 移入右栏
- 删除顶部 meta strip → 右侧栏新增"任务信息"卡（工作区/创建/更新/完成）
- 策略名保留在 header subtitle（测试断言 `/mom_20d/`）

## 4. 状态管理

- `selectedId: string | null` + `selectedStudy: StudySummary | null` → StudyPage 本地 state
- 列表数据 `loadList()` 提升到 StudyPage（中栏 + 创建成功刷新共用）
- 创建成功 → `loadList()` + `setSelected(newStudyId)`

## 5. 兼容与保留

- `StudyTab` / `StudyProgress` / `StudyCreateForm` **保留不删**（仍有单测覆盖，StudyProgress 的轮询/指令逻辑暂不在三栏中使用）
- 详情页 KPI 带 / ObjectiveProgress / MetricsCompare / Scoreboard / 指令侧栏保持不变

## 6. 测试计划

| 文件 | 内容 |
|------|------|
| StudyPage.test.tsx | 标题断言改"任务列表"；新增：筛选 chips 切换、点击选中、右栏摘要渲染、创建成功刷新 |
| studyCreatePanel.test.tsx | 空 objective 校验、自动生成策略名、折叠参数、提交调用 start |
| studyTaskSummary.test.tsx | 空态、加载 summary 渲染、查看详情链接、切换选中重载 |
| roundHistory.test.tsx | 时间线语义保持（现有断言），必要时补时间线结构断言 |
| studyDetailPage.test.tsx | meta 移入右栏后断言更新（`mom_20d` 仍来自 subtitle） |

## 7. 验收

- `npm test` 全量通过且正常退出（pool=forks）
- `tsc --noEmit`、`npm run lint`（0 error）、`npm run build` 成功
- 手动：xl 三栏 / lg 两栏 / 移动端单栏；创建 → 自动选中；筛选；跳详情页
