# Study 页面 UI 改进设计文档

> 日期: 2026-08-17
> 状态: 实施中

## 1. 问题分析

### 1.1 导航问题
- 当前需要在右侧面板底部点击"查看完整运行状况 →"按钮才能进入详情页
- 按钮位置太深，用户需要滚动才能看到
- 任务列表只能单击选择，无法快速跳转

### 1.2 Agent 工作内容缺失
- FlowCard 只显示简单的 9-agent 步进器
- 没有显示每个 agent 的实际输出内容
- 没有 DAG 可视化
- 没有聊天记录/日志显示

## 2. 改进方案

### 2.1 导航改进

#### StudyTaskSummary.tsx
- 将整个摘要卡片包裹在 Link 中，点击跳转到 `/study/{id}`
- 在面板顶部添加固定的"查看详情"按钮（始终可见）
- 保持现有内容不变

#### StudyTaskList.tsx
- 添加双击事件，直接跳转到详情页
- 保持单击选择行为

### 2.2 Agent 工作内容

#### AgentActivityPanel.tsx（新增）
- 显示 9-agent 流水线状态
- 每个 agent 的输出摘要
- 从 manifest API 获取数据

#### DAGVisualization.tsx（新增）
- 可视化 9-agent DAG
- 高亮当前执行位置
- 显示每个节点状态

#### AgentChatLog.tsx（新增）
- 显示 agent 对话历史
- 支持按角色筛选
- 显示 token 使用量

### 2.3 标签页系统

#### StudyDetailPage.tsx
- 添加标签页导航：概览 | Agent 活动 | 日志 | 任务
- 根据选中标签显示不同内容

## 3. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `StudyTaskSummary.tsx` | 修改 | 添加可点击跳转、顶部按钮 |
| `StudyTaskList.tsx` | 修改 | 添加双击跳转 |
| `StudyDetailPage.tsx` | 修改 | 添加标签页系统 |
| `AgentActivityPanel.tsx` | 新增 | Agent 工作内容面板 |
| `DAGVisualization.tsx` | 新增 | DAG 可视化组件 |
| `AgentChatLog.tsx` | 新增 | Agent 聊天记录组件 |
| `client.ts` | 修改 | 添加新 API 类型 |

## 4. 数据流

```
StudyPage
  ├─ StudyTaskList (单击选择 / 双击跳转)
  └─ StudyTaskSummary (可点击跳转 / 顶部按钮)
        └─ 显示: 摘要 + Agent 状态预览

StudyDetailPage
  ├─ 标签页: 概览 | Agent 活动 | 日志 | 任务
  ├─ 概览: 现有 KPI + RoundHistory + Metrics
  ├─ Agent 活动: AgentActivityPanel + DAGVisualization
  ├─ 日志: AgentChatLog (从 manifest API 获取)
  └─ 任务: Todos + Knowledge
```

## 5. 测试计划

| 测试项 | 验证内容 |
|--------|----------|
| 导航跳转 | 双击列表项跳转详情页 |
| 摘要面板点击 | 右侧面板点击跳转 |
| Agent 活动面板 | 显示 9-agent 状态 |
| DAG 可视化 | 正确渲染流水线 |
| 标签页切换 | 四个标签页正常切换 |
| SSE 实时更新 | Agent 状态实时刷新 |
