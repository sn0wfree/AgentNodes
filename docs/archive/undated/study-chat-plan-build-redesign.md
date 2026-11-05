# StudyChat 重构：Plan/Build 统一模式 + 右栏关键点

> Date: 2026-08-15
> Scope: 纯前端，7 个文件，~350 行改动
> Backend: 无变更

## 背景

当前 StudyChat 有两个割裂的模式：
- **指令模式**：只读 agent 输出 + `<select>` 下拉选 round + 指令 composer
- **对话模式**：独立 LLM 会话，完整 Composer

两个模式切换丢失上下文，round 选择器是原生下拉，信息密度低。

## 目标

1. 合并为 **Plan / Build** 模式，同一页面、同一条消息流，模式只改变 composer 默认发送类型
2. Round 显示改为仿 DeepSeek 右侧面板：所有轮次卡片（精简），主区有 round 分割线
3. 右栏默认展开、可折叠

## 设计

### 布局结构

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  [📋 Plan] [🔧 Build]          Round 5 — Calmar 1.23 ▲+0.15    [☰ 右栏]  │
├──────────────────────────────────────────────────┬─────────────────────────────┤
│                                                  │  📌 关键点                    │
│  ▼ Round 5 ───────────────────────────────────── │  ┌─────────────────────────┐ │
│  🧑 researcher: "均值回归窗口过大..."              │  │ R5  ✅ keep              │ │
│  👤 你 (指令): "尝试 14 天窗口"                    │  │ 假设: 减小均值回归窗口..  │ │
│  ─────────────────────────────────────────────── │  │ Calmar ▲ +0.15          │ │
│  ▼ Round 4 ───────────────────────────────────── │  └─────────────────────────┘ │
│  🧑 researcher: "成交量因子产生伪信号"              │  ┌─────────────────────────┐ │
│  🔧 strategist: rebalance done                    │  │ R4  ❌ discard           │ │
│  ✅ verdict: discard                              │  │ 假设: 引入成交量过滤..    │ │
│  🗨️ 你 (对话): "什么条件下失效？"                  │  │ Calmar ▼ -0.22          │ │
│  🤖 assistant: "震荡市信噪比显著下降"              │  └─────────────────────────┘ │
│─────────────────────────────────────────────────│  ...                         │
│  [指令 ▼] ┌──────────────────────────────────┐  │                             │
│  对话 ●   │ 输入研究指令或对话内容...           │  │                             │
│           │              Cmd/Ctrl+Enter 发送  │  │                             │
│           └──────────────────────────────────┘  │                             │
└──────────────────────────────────────────────────┴─────────────────────────────┘
```

### 模式行为

- **Plan**：Composer 默认"指令"，可手动切"对话"
- **Build**：Composer 默认"对话"，可手动切"指令"
- 消息流在两种模式下完全一致，不做过滤

### 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `widgets/useStudyChatMode.ts` | 改 | `'directive'|'chat'` → `'plan'|'build'`；localStorage 值迁移 |
| `widgets/StudyChatComposer.tsx` | 新建 | 统一输入区 + [指令\|对话] pill；指令→directive API，对话→现有 Composer |
| `widgets/RoundCard.tsx` | 新建 | 精简卡片：Round N + verdict 徽章 + 假设摘要 + ΔCalmar |
| `widgets/KeyPointsPanel.tsx` | 新建 | 右栏 round 卡片列表（所有轮次，新→旧），懒加载 manifest |
| `chat/MessageList.tsx` | 小改 | 加 `separatorKey?: (msg) => string` prop，支持 round 分割线 |
| `widgets/StudyChat.tsx` | 重写 | 统一布局：header + flex（主区 MessageList + 右栏 KeyPointsPanel） |
| `dashboard/registry.tsx` | 微调 | 描述更新 |

### 消息流设计

所有消息进同一个 session，打 `metadata.kind` 标签：

| kind | 来源 | 示例 |
|------|------|------|
| `agent` | `GET /rounds/{n}/agent_outputs` | researcher 输出、strategist 输出 |
| `directive` | 乐观插入，附"下轮生效"提示 | 用户指令 |
| `chat` | 真实 session（服务端持久化） | 用户对话 |
| `system` | SSE 事件 | study_round、study_budget |

**Round 分割线**：在 MessageList 中注入虚拟 Message（role=system, metadata.kind='round-separator'），渲染为灰线 + 圆角 "Round N" 标签。

### 右栏关键点

- 数据源：`GET /rounds`（list）→ 懒加载 `GET /rounds/{n}/manifest`
- 每张卡片：`Round N` + verdict 徽章（✅/❌）+ 假设摘要一行截断 + ΔCalmar（vs 上轮，红绿）
- 点击卡片 → 主区滚动到对应 Round 分割线
- 右栏折叠状态持久化：`sr-study-chat-panel-{studyId}`
- SSE `study_round` 事件触发列表刷新

### localStorage 迁移

- Key `sr-study-chat-mode-{id}`：`'directive'` → `'plan'`，`'chat'` → `'build'`
- Key `sr-study-chat-panel-{id}`：新增，boolean，默认 `true`（展开）

### 数据流

1. 组件挂载 → 创建/恢复 chat session（懒）→ 加载当前 round agent 消息
2. 点击右栏 round 卡片 → 加载该 round 的 agent_outputs → 注入消息流 + 注入 round 分割线
3. SSE 事件 → 乐观追加系统消息到流
4. 发送指令 → `api.study.directive` + 乐观追加指令消息（kind=directive）
5. 发送对话 → 真实 session send + 流式响应

### 验证

1. `cd webui/frontend && npm run build` — 无报错
2. 手动验证：打开 study 页 → 右栏显示 round 卡片、点击切换主区；Plan/Build 切换改 composer 默认类型；发指令出现"下轮生效"提示；发对话正常流式；旧 localStorage（directive/chat）自动迁移
