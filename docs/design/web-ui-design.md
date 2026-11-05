# Web UI 设计文档

> Strategy Research — 前端重设计方案
> 版本: v0.1 | 日期: 2026-07-28

---

## 1. 概述

### 1.1 目标

将现有 Jinja2 + HTMX 的 Web UI 重写为 React SPA，实现：
- 聊天界面（流式渲染、工具调用展示、多轮对话）
- 多 Agent 协作展示（活动时间线 + 状态卡片）
- Goal Workflow 可视化（DAG 图 + 实时状态 + 启动/暂停）
- 用户认证 + 会话持久化
- 深色主题 + 中文界面

### 1.2 设计参考

| 项目 | 借鉴点 |
|---|---|
| **OpenCode** | Message + Parts 模型、SSE 流式、Plan/Build 双模式、子会话导航、命令面板 |
| **Nanobot** | Activity Timeline（折叠活动组）、ReasoningRow（推理展示）、React SPA 架构 |
| **Codex CLI** | MarkdownStreamCollector（流式渲染）、ExecCell（工具调用）、Agent Picker（状态点） |
| **当前 TUI** | StreamingText（流式打字机）、ToolCall 行内展示、GoalPanel、WorkflowEventBus |

### 1.3 技术选型

| 层 | 技术 | 理由 |
|---|---|---|
| **框架** | React 18 + TypeScript | 生态最成熟，Radix UI 组件库支持好 |
| **构建** | Vite 5 | 快速 HMR，原生 ESM |
| **样式** | Tailwind CSS 4 | 实用优先，深色主题开箱即用 |
| **UI 组件** | Radix UI | 无障碍、可定制、无样式 opinions |
| **图标** | Lucide React | 轻量、一致的图标库 |
| **状态管理** | Zustand | 比 Redux 轻量，比 Context 强大 |
| **Markdown** | react-markdown + remark-gfm + react-syntax-highlighter | 成熟的 Markdown 渲染链 |
| **Diff** | react-diff-viewer-continued | 语法高亮 diff 展示 |
| **DAG** | @xyflow/react (React Flow) | 专业流程图/DAG 可视化库 |
| **实时通信** | SSE (EventSource API) | 浏览器原生支持，无需 WebSocket |
| **认证** | JWT (python-jose + bcrypt) | 无状态、易集成 |
| **数据库** | SQLite (已有 SessionDB) | 复用现有基础设施 |

---

## 2. 架构设计

### 2.1 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        React 前端                               │
│                   (webui/frontend/)                              │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ 聊天模块  │  │ Agent    │  │ Workflow │  │ Goal     │       │
│  │          │  │ 面板模块  │  │ DAG 模块 │  │ 面板模块  │       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
│       │             │             │             │               │
│       └─────────────┴─────────────┴─────────────┘               │
│                           │                                     │
│                    Zustand Store                                │
│                           │                                     │
│                    API Client + SSE                              │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP + SSE
┌───────────────────────────┴─────────────────────────────────────┐
│                       FastAPI 后端                               │
│                                                                 │
│  /api/auth/*    ─ 认证 (JWT)                                    │
│  /api/chat/*    ─ 聊天 (SSE 流式)                               │
│  /api/session/* ─ 会话管理                                      │
│  /api/goal/*    ─ Goal + Workflow                               │
│  /api/agent/*   ─ Agent 状态                                    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                        数据层                                    │
│                                                                 │
│  users.db     ─ 用户 + Web 会话 + 消息 (新增)                    │
│  sessions.db  ─ CLI 会话 (已有，保留)                             │
│  goals.db     ─ Goal + Evidence (已有，保留)                     │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 前端目录结构

```
webui/frontend/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.ts
├── index.html
└── src/
    ├── main.tsx                          # 入口
    ├── App.tsx                           # 根组件 + 路由
    ├── api/                              # API 客户端层
    │   ├── client.ts                     # fetch wrapper (JWT 自动注入)
    │   ├── sse.ts                        # SSE 连接管理器
    │   └── types.ts                      # TypeScript 类型定义
    ├── stores/                           # Zustand 状态管理
    │   ├── auth.ts                       # 认证状态 (user, token, login/logout)
    │   ├── session.ts                    # 会话状态 (current_session, sessions_list)
    │   ├── chat.ts                       # 聊天状态 (messages: Map, streaming, thinking)
    │   ├── agents.ts                     # Agent 状态 (agents: Map, iterations_detail)
    │   ├── workflow.ts                   # Workflow 状态 (dag, execution, progress)
    │   ├── layout.ts                     # 布局状态 (左导航宽度、右主区可见性、Tab、比例)
    │   ├── commandPalette.ts             # 命令面板状态
    │   └── toast.ts                      # Toast 通知状态 (队列, 最多 3 条)
    ├── components/
    │   ├── layout/                       # 布局骨架
    │   │   ├── AppShell.tsx              # 整体布局容器 (三栏 + TopBar)
    │   │   ├── TopBar.tsx                # 顶部导航栏
    │   │   ├── IconNav.tsx               # 左侧图标导航栏 (64px)
    │   │   ├── NavPopover.tsx            # 导航栏 hover 浮层面板
    │   │   ├── MainSplit.tsx             # 左右主区分隔 (可拖拽)
    │   │   ├── RightPanel.tsx            # 右主区容器 (Tab 切换)
    │   │   ├── CommandPalette.tsx        # Cmd+K 命令面板
    │   │   └── ResizablePanel.tsx        # 可拖拽面板通用组件
    │   ├── auth/                         # 认证页面
    │   │   ├── LoginPage.tsx             # 登录页
    │   │   ├── RegisterPage.tsx          # 注册页
    │   │   └── AuthGuard.tsx             # 路由守卫
    │   ├── chat/                         # 聊天模块
    │   │   ├── MessageList.tsx           # 消息列表 (虚拟滚动: react-virtuoso)
    │   │   ├── MessageBubble.tsx         # 用户消息气泡
    │   │   ├── AssistantMessage.tsx      # 助手消息 (Markdown + parts)
    │   │   ├── StreamingText.tsx         # 流式文本 (打字机效果)
    │   │   ├── ThinkingBlock.tsx         # 推理过程 (默认折叠, 每条独立展开)
    │   │   ├── ToolCallGroup.tsx         # 工具调用组 (默认折叠, 合并 call+result)
    │   │   ├── ToolCallItem.tsx          # 单个工具调用条目
    │   │   ├── FileEditBlock.tsx         # 文件 diff 展示 (默认展开)
    │   │   ├── TableBlock.tsx            # 数据表格 (默认展开)
    │   │   ├── ChartBlock.tsx            # 图表 (默认展开)
    │   │   ├── ImageBlock.tsx            # 图片展示
    │   │   ├── Composer.tsx              # 输入框 (@agent/@file + 图片粘贴)
    │   │   ├── MentionDropdown.tsx       # @mention 下拉菜单
    │   │   └── MarkdownRenderer.tsx      # Markdown 渲染器
    │   ├── agents/                       # Agent 展示模块
    │   │   ├── AgentList.tsx             # Agent 列表 (右主区 Tab, 列表式)
    │   │   ├── AgentItem.tsx             # 单个 Agent 条目 (运行中展开详情)
    │   │   └── AgentDetailPanel.tsx      # Agent 详情侧栏 (点击展开)
    │   ├── workflow/                     # Workflow 模块
    │   │   ├── WorkflowDAG.tsx           # React Flow DAG 可视化 (滚轮缩放/拖拽平移)
    │   │   ├── DAGNode.tsx               # 自定义 DAG 节点 (状态色+左边框)
    │   │   ├── DAGEdge.tsx               # 自定义 DAG 边 (灰色→蓝高亮)
    │   │   ├── DAGToolbar.tsx            # DAG 顶部工具栏 (名称+控制按钮)
    │   │   ├── DAGProgressBar.tsx        # DAG 底部进度条
    │   │   ├── DAGNodeDetail.tsx         # 节点详情侧栏 (右侧滑出)
    │   │   ├── WorkflowList.tsx          # Preset 列表 (卡片式)
    │   │   └── WorkflowStart.tsx         # 启动表单
    │   ├── goal/                         # Goal 模块
    │   │   ├── GoalTab.tsx               # Goal Tab 容器 (标准在上, 时间线在下)
    │   │   ├── CriteriaList.tsx          # 标准清单 (带 Evidence 计数+Agent 来源)
    │   │   └── GoalTimeline.tsx          # Evidence 时间线 (倒序, 最新在上)
    │   ├── common/                       # 通用组件
    │   │   ├── Badge.tsx                 # 状态徽章（颜色映射 + 尺寸变体）
    │   │   ├── Spinner.tsx               # 加载动画（旋转/脉冲 + 尺寸）
    │   │   ├── EmptyState.tsx            # 空状态（图标 + 文案 + 操作按钮）
    │   │   ├── ConfirmDialog.tsx         # 确认弹窗（标题 + 描述 + 双按钮）
    │   │   ├── Toast.tsx                 # Toast 通知（自动消失/手动关闭/最多 3 条）
    │   │   ├── ToastManager.tsx          # Toast 队列管理
    │   │   ├── Skeleton.tsx              # 通用骨架屏（矩形/圆形/文字行）
    │   │   ├── ImageLightbox.tsx         # 图片全屏查看（基于 Radix Dialog）
    │   │   └── ErrorBoundary.tsx         # 全局错误边界（fallback UI）
    │   └── hooks/                        # 自定义 Hooks
    │       ├── useKeyboardShortcuts.ts   # 键盘快捷键统一管理（Cmd+K/G/W/B/1/2/3）
    │       ├── useSSE.ts                 # SSE 连接管理（重连/心跳/Last-Event-ID）
    │       └── useMention.ts             # @mention 搜索逻辑（agent/file）
    └── styles/
        └── globals.css                   # Tailwind 入口 + 全局样式
```

---

## 3. 页面布局设计

### 3.1 设计原则

1. **内容优先**：导航占用最小空间，把最多区域留给实际内容
2. **场景自适应**：根据工作模式（聊天/监控/专注）动态调整布局
3. **双主区并存**：聊天与 DAG/Goal 同时可见，避免来回切换丢失上下文
4. **可拖拽弹性**：面板宽度可拖拽调整，记忆用户偏好
5. **深浅一致**：深色主题为默认，所有组件遵循同一套色板

### 3.2 整体布局（双主区 + 图标导航栏）

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ║
║  🔍 ⌘K  动量因子研究 · 3h前             ● 2 running               ⏸    👤   ║  ← 玻璃拟态 TopBar (h:52px)
║ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ║
╠═══╦══════════════════════════════════╦═══════════════════════════════════════╣
║   ║                                  ║  📊 DAG     🎯 Goal     🤖 Agents    ║  ← 右主区 Tab
║   ║  ┌────────────────────────────┐  ║                                       ║
║ ┃ ║  │ 你帮我分析下A股动量因子表现    │  ║      ┌──────────────┐             ║
║ 💬║  └────────────────────────────┘  ║      │   researcher   │ ← 微蓝光呼吸   ║
║ 🎯║                                  ║      └──────┬───────┘             ║
║ 📊║  ┌────────────────────────────┐  ║             ↓                         ║
║ 📚║  │ ┌─┐                        │  ║      ┌──────────────────┐           ║
║ ⚙️║  │ │R│ researcher             │  ║      │   data_quality   │ ← 运行中    ║
║ 🌙║  │ └─┘ 好的，让我先看数据...    │  ║      └────────┬─────────┘           ║
║   ║  │                            │  ║               ↓                       ║
║   ║  │ ┌ read_file ────────────┐ │  ║      ┌──────────────────┐           ║
║   ║  │ │ ✔ data.py  0.3s       │ │  ║      │  factor_analyst  │ ← 待运行    ║
║   ║  │ └──────────────────────┘ │  ║      └────────┬─────────┘           ║
║   ║  │ ┌ execute_python ───────┐ │  ║               ↓                       ║
║   ║  │ │ ⏳ momentum_analysis   │ │  ║      ┌──────────────────┐           ║
║   ║  │ └──────────────────────┘ │  ║      │   risk_review    │ ← 待运行    ║
║   ║  │                            │  ║      └──────────────────┘           ║
║   ║  │ "动量因子近 3 个月表现..."    │  ║                                       ║
║   ║  └────────────────────────────┘  ║  ▓▓▓▓▓▓░░░░  66%   2/4 节点完成        ║
║   ║                                  ║                                       ║
║   ╠══════════════════════════════════╣                                       ║
║   ║ ┌─ ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ─ ┐      ║
║   ║   📎  📷  输入消息...                                    ⌘↵ 发送  │      ║  ← 玻璃拟态输入框 (仅左主区)
║   ║ └─ ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ─ ┘      ║
╚═══╩══════════════════════════════════╩═══════════════════════════════════════╝
    ↑                              ↑
  64px 图标栏               可拖拽分隔条
  (选中项左侧蓝色竖条)
```

| 区域 | 宽度 | 高度 | 说明 |
|---|---|---|---|
| **TopBar** | 100% | 52px | 玻璃拟态 · 搜索/命令 + 会话标题 + 状态 + 控制 + 用户 |
| **图标导航栏** | 64px | calc(100vh - 52px) | 左侧垂直图标栏，hover 展开浮层，选中项左侧蓝色竖条 |
| **左主区（聊天）** | flex 1 (默认 ~50%) | calc(100vh - 52px) | 消息列表 + 底部输入框（玻璃拟态） |
| **右主区（Tab）** | flex 1 (默认 ~50%) | calc(100vh - 52px) | DAG / Goal / Agent 三 Tab 切换，独立滚动 |
| **底部状态栏** | — | — | 移除，改用 Cmd+K Command Palette |

### 3.3 三种工作模式

#### 模式 A：纯聊天模式（右主区折叠）
```
┌──────┬───────────────────────────────────────────────────────────┐
│ 💬   │                                                           │
│ 🎯   │              消息列表（占满宽度）                           │
│ 📊   │                                                           │
│      │                                                           │
│      ├───────────────────────────────────────────────────────────┤
│      │  [输入消息...]                                             │
└──────┴───────────────────────────────────────────────────────────┘
```
- 触发：点击右主区折叠按钮 / 快捷键 `Cmd+B`
- 适用：普通对话、写代码、不需要监控 Workflow 的场景

#### 模式 B：Workflow 监控模式（双主区，默认）
```
┌──────┬──────────────────────────┬───────────────────────────────┤
│ 💬   │                          │  [🗺 DAG] [🎯 目标] [📋 Agent] │
│ 🎯   │       聊天流              │                               │
│ 📊   │                          │        DAG 图 / Goal 进度       │
│      │                          │                               │
│      ├──────────────────────────┤                               │
│      │  [输入...]               │                               │
└──────┴──────────────────────────┴───────────────────────────────┘
```
- 默认：左 50% / 右 50%，可拖拽中间分隔条调整
- 适用：Workflow 运行时，需要同时看输出和进度

#### 模式 C：专注模式（左主区折叠为窄条）
```
┌──────┬───────────────────────────────────────────────────────────┐
│ 💬   │  [🗺 DAG] [🎯 目标] [📋 Agent]                             │
│ 🎯   │                                                           │
│ 📊   │                DAG 大图 / Goal 详情                        │
│      │                                                           │
│      │                                                           │
│      │                                                           │
└──────┴───────────────────────────────────────────────────────────┘
```
- 左主区折叠为 60px 窄条（显示最新消息预览气泡）
- 适用：复盘 Workflow、查看 Goal 详情、不需要频繁输入
- 点击窄条可快速展开回聊天模式

### 3.4 图标导航栏

```
┌──────┐
│ 🔍   │  ← Logo / 首页 (点击打开 Command Palette)
├──────┤
│ 💬   │  ← 聊天 (hover 展开会话列表浮层, 260px 宽)
│ 🎯   │  ← 目标 (hover 展开目标列表浮层)
│ 📊   │  ← 工作流 (hover 展开工作流 preset 列表浮层)
│ 📚   │  ← 知识库 / 历史记录
├──────┤
│ ⚙️   │  ← 设置
│ 🌙   │  ← 主题切换 (深色/浅色)
│      │
│      │  ← 底部：用户头像
└──────┘
```

**交互细节**：
- Hover 图标 → 显示 tooltip + 对应内容浮层面板（如会话列表）
- 点击图标 → 切换当前视图/模式，图标高亮
- 拖拽右边缘 → 临时展开为 260px 宽侧边栏（带文字标签）
- 宽度记忆到 localStorage

### 3.5 TopBar 设计

```
┌───────────────────────────────────────────────────────────────────────┐
│ 🔍 ⌘K  动量因子研究 (session-2024-01-15)   ●  2 个 Agent 运行中   ⏸ 👤 │
│ ───                                                          ─────── │
│  搜索/命令                                                         用户 │
└───────────────────────────────────────────────────────────────────────┘
```

从左到右：

| 元素 | 说明 |
|---|---|
| **搜索/命令** | 点击或 `Cmd+K` 打开 Command Palette（搜索会话、执行命令、切换设置） |
| **会话标题** | 当前会话名称，点击可重命名；显示最后活跃时间 |
| **状态指示** | 运行中 Agent 数量（脉冲绿点 + 数字）；无运行时隐藏 |
| **暂停/恢复** | Workflow 运行时显示 ⏸，暂停时显示 ▶；醒目位置方便操作 |
| **用户头像** | 点击展开用户菜单（个人设置、登出等） |

### 3.6 右主区 Tab 设计

右主区有 3 个 Tab，点击切换，内容独立滚动：

| Tab | 图标 | 内容 | 红点提示 |
|---|---|---|---|
| **DAG** | 🗺 | Workflow DAG 图 + 全局进度条 + 节点详情弹窗 | 节点状态变化 |
| **目标** | 🎯 | Goal 描述 + 标准清单 + Evidence 时间线 | 新 evidence / 标准覆盖变化 |
| **Agent** | 📋 | Agent 状态卡片列表（完整信息） | Agent 状态变化 |

**交互**：
- 点击 Tab 切换，当前 Tab 底部有蓝色指示条
- 有新事件时 Tab 右上角显示红点
- `Cmd+1/2/3` 快速切换 Tab
- 可拖拽 Tab 调整顺序（可选）

### 3.7 可拖拽分隔条

两条分隔条支持拖拽调整：

| 分隔条 | 位置 | 拖拽范围 | 默认宽度 |
|---|---|---|---|
| **左分隔条** | 导航栏 ↔ 左主区 | 64px ~ 260px | 64px |
| **中间分隔条** | 左主区 ↔ 右主区 | 20% ~ 80% | 50% |

**视觉反馈**：
- 鼠标悬停时分隔条变亮（2px → 3px，颜色从 border-muted → accent-blue）
- 拖拽时鼠标变为 col-resize
- 释放后宽度写入 localStorage，下次恢复

### 3.8 响应式断点

| 断点 | 布局 | 说明 |
|---|---|---|
| **≥1440px** | 完整三栏（导航 + 左主区 + 右主区） | 桌面端默认，双主区并排 |
| **1024-1439px** | 导航 + 单主区（聊天为主） | 笔记本，右主区折叠为浮层按钮 |
| **768-1023px** | 导航 + 单主区 + 底部 Tab | 平板，底部 Tab 切换聊天/DAG/Goal |
| **<768px** | 全屏单区 + 底部导航栏 | 手机，底部图标切换视图 |

### 3.9 视觉设计系统

#### 3.9.1 设计风格

**Dark Mode (OLED) + 点缀玻璃拟态 + 蓝色主调 + 克制发光**

| 原则 | 说明 |
|---|---|
| **信息优先** | 所有视觉效果服务于信息可读性，不为装饰牺牲清晰度 |
| **克制表达** | 发光/动效只用在状态指示上（运行中、加载中），不滥用 |
| **玻璃点缀** | 玻璃拟态只用于 TopBar / 输入框 / 弹窗 / 浮层，内容区用实色保证可读性 |
| **OLED 优化** | 深底色减少 OLED 屏幕功耗，同时护眼 |

**玻璃拟态使用范围**：

| 区域 | 风格 |
|---|---|
| TopBar | ✅ 玻璃拟态 |
| 图标导航栏 | ❌ 实色深底 |
| 消息卡片 | ❌ 实色深底 |
| DAG 节点卡片 | ❌ 实色深底 |
| 聊天输入框 | ✅ 玻璃拟态（仅左主区底部） |
| 弹窗 / Command Palette | ✅ 玻璃拟态 |
| 导航 hover 浮层 | ✅ 玻璃拟态 |
| ToolCall 代码块 | ❌ 实色深底 |

#### 3.9.2 字体

- **正文**：Inter + 系统中文字体（PingFang SC / 微软雅黑 / Noto Sans CJK）
- **代码/数字/Agent 名**：JetBrains Mono（字符区分度高，阅读代码更舒适）
- **行高**：正文 1.5-1.6，代码 1.4

```css
font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC',
             'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif;
font-family: 'JetBrains Mono', 'Fira Code', Consolas, 'Courier New', monospace;
```

#### 3.9.3 主题色板（深色 OLED + 蓝色主调）

```css
/* 背景 — 从深到浅 5 层 */
--bg-canvas:     #020617;    /* 最底层（OLED 深蓝黑） */
--bg-surface:    #0F172A;    /* 面板/导航栏背景 */
--bg-elevated:   #1E293B;    /* 卡片/消息气泡背景 */
--bg-highlight:  #334155;    /* hover/选中背景 */
--bg-active:     #475569;    /* 按下/激活 */

/* 玻璃拟态（点缀用） */
--glass-bg:      rgba(15, 23, 42, 0.75);
--glass-border:  rgba(255, 255, 255, 0.08);
--glass-blur:    12px;

/* 文字 — 4 层 */
--text-primary:    #F8FAFC;  /* 主要文字 */
--text-secondary:  #CBD5E1;  /* 次要文字 */
--text-tertiary:   #64748B;  /* 辅助文字/时间戳 */
--text-disabled:   #475569;  /* 禁用/占位符 */

/* 品牌色 — 蓝色为主 */
--brand-500:   #3B82F6;     /* 主强调色（选中/链接/主按钮） */
--brand-600:   #2563EB;     /* hover */
--brand-400:   #60A5FA;     /* 浅色标签 */
--brand-900:   #1E3A8A;     /* 深色背景 */

/* 语义色 */
--success:  #22C55E;   /* 成功/通过/正收益 */
--error:    #EF4444;   /* 错误/删除/危险 */
--warning:  #EAB308;   /* 警告/运行中 */
--info:     #06B6D4;   /* 信息/代码高亮 */
--purple:   #A855F7;   /* Agent 标识色 */

/* 边框 — 3 层 */
--border-default:  rgba(255, 255, 255, 0.1);
--border-muted:    rgba(255, 255, 255, 0.05);
--border-focus:    #3B82F6;

/* 阴影 */
--shadow-sm:   0 1px 2px rgba(0, 0, 0, 0.3);
--shadow-md:   0 4px 12px rgba(0, 0, 0, 0.4);
--shadow-lg:   0 8px 32px rgba(0, 0, 0, 0.5);
--shadow-glow: 0 0 12px rgba(59, 130, 246, 0.15);  /* 克制发光，仅运行状态 */

/* 圆角 */
--radius-sm:  4px;
--radius-md:  6px;
--radius-lg:  8px;
--radius-xl:  12px;

/* 动效 */
--transition-fast:   150ms ease-out;
--transition-base:   200ms ease-out;
--transition-slow:   300ms ease-out;
```

#### 3.9.4 动效规范

| 场景 | 动效 | 时长 | 缓动 |
|---|---|---|---|
| 按钮 hover | 背景色 + 边框色变化 | 150ms | ease-out |
| 面板展开/收起 | 宽度/高度过渡 | 200ms | ease-out |
| 消息出现 | 淡入 + 轻微上移 | 200ms | ease-out |
| 工具调用状态切换 | 图标 + 颜色切换 | 150ms | ease-out |
| DAG 节点运行中 | 外发光呼吸（克制） | 2s 循环 | ease-in-out |
| 加载骨架屏 | 渐变脉冲 | 1.5s 循环 | ease-in-out |

**反模式**：
- 不在装饰元素上使用无限循环动画（仅加载/运行状态用）
- 不使用 linear 线性运动（感觉机械）
- 不忽略 `prefers-reduced-motion` 系统设置
- 不让异步加载内容导致布局跳动（预留空间 / 骨架屏）

### 3.10 Command Palette（Cmd+K）

替代底部状态栏的快捷键提示，提供统一的命令入口：

```
┌──────────────────────────────────┐
│  🔍 输入命令或搜索...              │
├──────────────────────────────────┤
│  💬 新建会话        Cmd+N        │
│  🎯 启动 Goal       Cmd+G        │
│  📊 打开工作流       Cmd+W        │
│  ⏸  暂停 Workflow   Ctrl+G       │
│  ▶  恢复 Workflow   Ctrl+R       │
│  👁  切换右面板       Cmd+B        │
│  🌙 切换主题         Cmd+Shift+L  │
│  ⚙️  设置...                      │
└──────────────────────────────────┘
```

- 触发：`Cmd+K` 或点击 TopBar 搜索框
- 支持模糊搜索命令、会话、文件
- 显示对应快捷键
- 最近使用的命令置顶

---

## 4. 核心模块设计

### 4.0 跨模块联动

四个核心模块的层级关系和数据流向：

```
        ┌─────────────┐
        │   Goal      │  ← 最高层级：目标 + 标准 + Evidence
        └──────┬──────┘
               │
        ┌──────▼──────┐
        │  Workflow   │  ← DAG 图：Agent 依赖关系 + 全局进度
        │    DAG      │
        └──────┬──────┘
               │
      ┌────────▼────────┐
      │                 │
┌─────▼─────┐   ┌───────▼───────┐
│  聊天模块  │   │   Agent 面板   │
│ (左主区)   │   │  (右主区 Tab)  │
│ 消息流     │   │  状态卡片列表  │
│ + 输入框   │   │               │
└───────────┘   └───────────────┘
```

**联动关系**（点击跳转）：

| 触发点 | 跳转到 | 行为 |
|---|---|---|
| DAG 节点 click | Agent Tab | 切到对应 Agent，高亮该 Agent |
| Agent 卡片 click | 聊天区 | 滚动到该 Agent 最近消息 |
| Goal Evidence click | 聊天区 | 滚动到产出该 Evidence 的消息 |
| TopBar 状态指示 | 全局 | 实时显示 running Agent 数量 |
| TopBar 暂停按钮 | DAG + TopBar | 同步暂停/恢复状态 |

---

### 4.1 聊天模块

#### 4.1.1 消息数据模型

```typescript
// api/types.ts

interface Message {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system';
  agent_id?: string;                  // 产出此消息的 Agent（辅助信息区显示）
  content: string;                    // 完整文本（最终态）
  parts: MessagePart[];               // 结构化内容块
  created_at: number;
  metadata?: MessageMetadata;
}

type MessagePart =
  | TextPart
  | ToolCallPart            // 合并了 call + result，用 status 演进
  | ThinkingPart
  | FileEditPart
  | TablePart
  | ChartPart
  | ImagePart;

interface TextPart {
  type: 'text';
  content: string;
}

// 合并版：ToolCall + ToolResult 合为一体
interface ToolCallPart {
  type: 'tool_call';
  call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: 'pending' | 'running' | 'completed' | 'error';
  result?: string;
  duration_ms?: number;
  error?: string;
}

interface ThinkingPart {
  type: 'thinking';
  id: string;                          // 唯一 ID，支持独立展开/折叠
  content: string;                     // 推理过程文本
}

interface FileEditPart {
  type: 'file_edit';
  file_path: string;
  additions: number;
  deletions: number;
  diff: string;                        // unified diff 格式
}

interface TablePart {
  type: 'table';
  headers: string[];
  rows: (string | number)[][];
  caption?: string;
}

interface ChartPart {
  type: 'chart';
  chart_type: 'bar' | 'line' | 'pie' | 'scatter';
  data: Record<string, unknown>;
  title?: string;
}

interface ImagePart {
  type: 'image';
  url: string;                         // base64 data URL 或远程 URL
  mime_type: string;
  alt?: string;
}

interface MessageMetadata {
  model?: string;                      // 使用的模型
  tokens_used?: number;
  iteration?: number;                  // AgentLoop 迭代次数
}
```

#### 4.1.2 SSE 事件流

```
// 客户端连接: GET /api/chat/events?session_id=X

// 事件类型：
event: text_delta
data: {"delta": "根据分析", "message_id": "msg_123"}

event: thinking_start
data: {"message_id": "msg_123", "thinking_id": "th_1"}

event: thinking_delta
data: {"delta": "让我先检查...", "message_id": "msg_123", "thinking_id": "th_1"}

event: thinking_done
data: {"message_id": "msg_123", "thinking_id": "th_1"}

event: tool_call
data: {"call_id": "tc_1", "tool_name": "read_file", "arguments": {"path": "src/core.py"}, "message_id": "msg_123"}

event: tool_result
data: {"call_id": "tc_1", "status": "completed", "result": "file content...", "duration_ms": 300, "message_id": "msg_123"}

event: file_edit
data: {"file_path": "src/strategy.py", "additions": 23, "deletions": 5, "diff": "...", "message_id": "msg_123"}

event: table
data: {"headers": ["因子","年化收益","夏普比"], "rows": [...], "message_id": "msg_123"}

event: assistant_message
data: {"message_id": "msg_123", "agent_id": "researcher", "content": "完整回复文本", "parts": [...]}

event: image
data: {"url": "data:image/png;base64,...", "mime_type": "image/png", "message_id": "msg_123"}

event: error
data: {"error": "rate limit exceeded", "message_id": "msg_123"}

event: heartbeat
data: {"timestamp": 1234567890}
```

#### 4.1.3 流式渲染流程

```
用户输入 → POST /api/chat/send_async → 返回 204
       ↓
SSE 连接 → GET /api/chat/events?session_id=X
       ↓
┌───────────────────────────────────────────────────────┐
│  text_delta → StreamingText.append(delta)             │
│              → 原地更新 DOM (打字机效果)               │
│                                                        │
│  thinking_start → ThinkingBlock 渲染（默认折叠）       │
│  thinking_delta → ThinkingBlock 追加文本               │
│  thinking_done  → 保留，不自动折叠/展开                 │
│                                                        │
│  tool_call → ToolCallGroup 添加条目（默认折叠）         │
│  tool_result → ToolCallGroup 更新状态 ✔/✘ + 耗时       │
│                                                        │
│  table → TablePart 渲染（默认展开）                    │
│  file_edit → DiffBlock 渲染（默认展开）                 │
│  chart → ChartPart 渲染（默认展开）                     │
│                                                        │
│  assistant_message → MarkdownRenderer 渲染             │
│                      StreamingText → 隐藏              │
│                      ThinkingBlock → 保留（可展开）     │
│                      ToolCallGroup → 保留状态           │
└───────────────────────────────────────────────────────┘
```

#### 4.1.4 消息渲染布局

单条 AssistantMessage 的视觉结构（按 Agent 分组，头部显示 Agent 标识）：

```
┌─ AssistantMessage ───────────────────────────────────────────────────┐
│  🤖 researcher  · 14:32  · 2,340 tokens  · iteration 3/10           │ ← 头部
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │  ┌ ThinkingBlock (折叠) ─────────────────────────────────────┐  │ │
│ │  │  ▶ 思考中... (850 字)                                      │  │ │
│ │  └───────────────────────────────────────────────────────────┘  │ │
│ │                                                                  │ │
│ │  ┌ ToolCallGroup ────────────────────────────────────────────┐  │ │
│ │  │  ✔ read_file · src/data.py · 0.3s                        │  │ │ ← 默认折叠
│ │  │  ⏳ execute_python · momentum_analysis.py · running...    │  │ │
│ │  └───────────────────────────────────────────────────────────┘  │ │
│ │                                                                  │ │
│ │  ┌ TablePart (默认展开) ─────────────────────────────────────┐  │ │
│ │  │  ┌────────────┬──────────┬────────┬──────────┐            │  │ │
│ │  │  │ 因子名称   │ 年化收益  │ 夏普比  │ 最大回撤  │            │  │ │
│ │  │  ├────────────┼──────────┼────────┼──────────┤            │  │ │
│ │  │  │ 动量因子   │ 15.2%    │ 1.83   │ -12.5%   │            │  │ │
│ │  │  │ 反转因子   │ 8.7%     │ 1.24   │ -18.3%   │            │  │ │
│ │  │  └────────────┴──────────┴────────┴──────────┘            │  │ │
│ │  │  共 12 行 · 点击展开全部                                   │  │ │
│ │  └───────────────────────────────────────────────────────────┘  │ │
│ │                                                                  │ │
│ │  根据分析结果，动量因子在近 3 个月表现优于反转因子...               │ │ ← 最终文本
│ │                                                                  │ │
│ │  ┌ FileEditPart ─────────────────────────────────────────────┐  │ │
│ │  │  📝 src/strategy.py  (+23 / -5)                           │  │ │
│ │  │  + def momentum_strategy(data):                           │  │ │
│ │  │  +     ...                                                │  │ │
│ │  └───────────────────────────────────────────────────────────┘  │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

**关键交互**：
- ThinkingBlock：默认折叠，每条独立可展开/折叠，不自动展开
- ToolCallGroup：默认折叠（只显示工具名 + 状态 + 耗时），点击展开详情
- TablePart / ChartPart / DiffBlock：默认展开（量化研究核心输出，用户最关心）
- 长输出（>5行表格或>20行 diff）截断显示前 5 行，底部"展开全部"
- Agent 头部用小图标 + 名称 + 品牌色标识（researcher=蓝, data_quality=绿, factor_analyst=紫, risk_review=橙）
- 虚拟滚动：消息列表使用 react-virtuoso 或 @tanstack/react-virtual，只渲染可视区域

#### 4.1.5 输入框（Composer）

```
┌──────────────────────────────────────────────────────┐
│  📎  📷  输入消息...                        ⌘↵ 发送 │ ← 玻璃拟态（仅左主区底部）
└──────────────────────────────────────────────────────┘
```

**@mention 交互**：
- 输入 `@` 弹出下拉菜单，分两组：
  - `@agent`：显示可用 Agent 列表（名称 + 状态图标）
  - `@file`：显示文件搜索结果（输入关键词实时搜索工作区文件）
- 选中后插入标签，输入框显示为高亮标签样式

**其他功能**：
- 支持图片粘贴（Ctrl+V）和拖拽上传
- 支持 Shift+Enter 换行
- 发送后清空输入框，自动滚动到底部
- 历史上箭头翻阅历史输入
- 输入框高度自适应内容（最多 200px，超过后内部滚动）

---

### 4.2 Agent 展示模块

#### 4.2.1 Agent 状态模型

```typescript
interface AgentState {
  id: string;                         // agent_id
  name: string;                       // 显示名称
  color: string;                      // 品牌色（蓝/绿/紫/橙/青）
  status: 'idle' | 'pending' | 'running' | 'completed' | 'error' | 'skipped';
  layer: number;                      // DAG 层级
  started_at?: number;
  completed_at?: number;
  duration_ms?: number;
  current_task?: string;              // 当前正在做什么
  output_summary?: string;            // 输出摘要（前 100 字）
  tools?: string[];                   // 使用的工具列表
  tokens_used?: number;               // 已消耗 token（总）
  tokens_limit?: number;              // token 上限
  iteration?: number;                 // 当前迭代
  iteration_limit?: number;           // 迭代上限
  error?: string;

  // ── ReAct Loop 扩展字段 ──
  finished_reason?: 'stop' | 'max_iter' | 'no_progress' | 'error';  // 循环结束原因
  tool_calls_count: number;           // 已执行工具调用总数
  compaction_count: number;           // 上下文压缩次数（丢失早期信息的标志）
  context_tokens: number;             // 当前上下文窗口使用量
  context_tokens_limit: number;       // 上下文窗口上限（如 128k）
  iterations_detail: IterationDetail[]; // 每次迭代的详细摘要
}

// ── 单次迭代详情（ReAct 循环的一轮 Think→Act→Observe）──

interface IterationDetail {
  iteration: number;                  // 迭代序号
  thinking_summary?: string;          // 推理摘要（前 200 字）
  tool_calls: IterationToolCall[];    // 本迭代的工具调用列表
  tokens_used?: number;               // 本迭代消耗 token
  duration_ms?: number;               // 本迭代耗时
  compaction_applied?: boolean;       // 是否触发了上下文压缩
}

interface IterationToolCall {
  call_id: string;
  tool_name: string;
  status: 'running' | 'completed' | 'error';
  duration_ms?: number;
  result_preview?: string;            // 结果摘要（前 100 字）
}
```

**Agent 颜色标识**（按 preset 顺序自动分配）：

| 顺序 | Agent | 颜色 |
|---|---|---|
| 1 | researcher | 蓝 `#3B82F6` |
| 2 | data_quality | 绿 `#22C55E` |
| 3 | factor_analyst | 紫 `#A855F7` |
| 4 | risk_review | 橙 `#F59E0B` |
| 5+ | 自动分配 | 青 `#06B6D4` / 红 `#EF4444` |

**finished_reason 语义**：

| 值 | 含义 | 用户看到 | 颜色 |
|---|---|---|---|
| `stop` | LLM 不再调用工具，正常结束 | ✔ 正常完成 | 绿 |
| `max_iter` | 达到最大迭代次数 | ⚠ 达到上限 | 黄 |
| `no_progress` | 连续 3 次相同 tool_calls，陷入循环 | ✖ 无进展 | 红 |
| `error` | 工具执行或 LLM 调用出错 | ✖ 出错 | 红 |

#### 4.2.2 Agent Tab（右主区）— 列表式 + 迭代历史

```
┌─ Agent Tab ────────────────────────────────────────────────┐
│  🔍 搜索 Agent...                                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ── running ────────────────────────────────────────────── │
│                                                             │
│  ┌─ 🤖 researcher ──── iter 3/10 ── 1,234 tok ── 12.3s ─┐ │
│  │  正在分析动量因子表现...                                  │ │ ← 运行中：展开
│  │  ┌──────────────────────────────────────────────────┐ │ │
│  │  │ Iter 3:                                          │ │ │
│  │  │  🧠 "让我检查因子收益分布..." (折叠)               │ │ │
│  │  │  ✔ read_file · data.py · 0.3s                    │ │ │
│  │  │  ⏳ execute_python · analysis.py                  │ │ │
│  │  │                                                   │ │ │
│  │  │ Iter 2:                                          │ │ │
│  │  │  🧠 "需要先验证数据完整性..." (折叠)               │ │ │
│  │  │  ✔ read_file · raw.csv · 0.1s                    │ │ │
│  │  │  ✔ validate_data · 0.2s                          │ │ │
│  │  │                                                   │ │ │
│  │  │ Iter 1:                                          │ │ │
│  │  │  🧠 "先看看数据结构..." (折叠)                     │ │ │
│  │  │  ✔ list_files · 0.05s                            │ │ │
│  │  └──────────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  ── completed ──────────────────────────────────────────── │
│                                                             │
│  ✔ data_quality ── iter 2/10 ── 580 tok ── 1.8s ── stop  │ ← 紧凑（一行）
│                                                             │
│  ── pending ────────────────────────────────────────────── │
│                                                             │
│  ○ factor_analyst · 等待中                                  │
│  ○ risk_review · 等待中                                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**关键交互**：
- running 的 Agent 自动展开：显示迭代历史（最新迭代在上）
- 每次迭代可折叠：点击展开 thinking 摘要 + tool_calls 列表
- completed 的 Agent 紧凑一行：显示 ✔ + finished_reason
- 点击任意 Agent → 聊天区滚动到该 Agent 的最近消息
- 状态变化 → 右主区自动重排（running 始终在顶部）
- 红点提示：Agent 状态变化（completed/error）时 Tab 上显示红点

**状态色 + 图标映射**：

| 状态 | 颜色 | 图标 | 动效 |
|---|---|---|---|
| running | 黄 `#EAB308` | ⏳ | 蓝光呼吸（克制） |
| completed | 绿 `#22C55E` | ✔ | 无 |
| error | 红 `#EF4444` | ✗ | 无 |
| skipped | 灰 `#64748B` | ⊘ | 无 |
| pending | 深灰 `#475569` | ○ | 无 |

---

### 4.3 Workflow DAG 模块

#### 4.3.1 DAG 数据结构

```typescript
interface WorkflowDAG {
  nodes: DAGNode[];
  edges: DAGEdge[];
}

interface DAGNode {
  id: string;                         // agent_id
  label: string;                      // 显示名称
  color: string;                      // Agent 品牌色
  status: 'pending' | 'running' | 'completed' | 'error' | 'skipped';
  prompt_file: string;
  evidence_criterion: string;         // Evidence 标准描述
  tools: string[];
  position: { x: number; y: number }; // dagre 自动布局坐标
}

interface DAGEdge {
  id: string;
  source: string;                     // 上游 agent_id
  target: string;                     // 下游 agent_id
  label?: string;                     // 边上的条件标签
}
```

#### 4.3.2 DAG Tab 布局（React Flow）

```
┌─ DAG Tab ────────────────────────────────────────────────────────┐
│  📊 因子研究工作流                         [▶ 启动] [⏸] [🔄]   │ ← 顶部工具栏
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│          ┌────────────┐                                          │
│          │  researcher  │  ← 完成：绿色左边框 + ✓                 │
│          │  ✔ 3.2s     │                                          │
│          └──────┬─────┘                                          │
│                 ↓  （灰色连线 → 运行中变蓝高亮）                    │
│          ┌──────────────┐                                        │
│          │ data_quality  │  ← 运行中：蓝光呼吸 + 左边框高亮         │
│          │ ⏳ 正在检查... │                                        │
│          └──────┬───────┘                                        │
│                 ↓                                                │
│      ┌──────────┴──────────┐                                     │
│      ↓                     ↓                                     │
│    ┌────────────┐  ┌──────────────┐                               │
│    │ factor_    │  │ risk_review  │  ← 待运行：灰色 + 虚线边框     │
│    │ analyst    │  │              │                               │
│    └────────────┘  └──────────────┘                               │
│                                                                   │
├───────────────────────────────────────────────────────────────────┤
│  ▓▓▓▓▓▓░░░░  66%  ·  2/4 完成  ·  已运行 12m 35s                  │ ← 底部进度条
└───────────────────────────────────────────────────────────────────┘
```

**节点详情侧栏（点击节点，从右侧滑出，不遮挡 DAG 图）**：

```
┌─ 节点详情 ─────────────────────────────────────────┐
│  ✕  data_quality                                    │
├─────────────────────────────────────────────────────┤
│  状态: 运行中 · 1分45秒                              │
│  Agent 颜色: 绿色                                    │
│                                                      │
│  ┌ Prompt ────────────────────────────────────────┐ │
│  │ 你是一个数据质量检查专家，负责验证...             │ │ ← 折叠/展开
│  └────────────────────────────────────────────────┘ │
│                                                      │
│  ┌ 触发条件 ──────────────────────────────────────┐ │
│  │ upstream: researcher                           │ │
│  │ condition: has_data == true                     │ │
│  └────────────────────────────────────────────────┘ │
│                                                      │
│  ┌ 工具 (3) ─────────────────────────────────────┐ │
│  │  ✔ read_file     ✔ execute_python              │ │
│  │  ○ search_arxiv                                │ │
│  └────────────────────────────────────────────────┘ │
│                                                      │
│  ┌ Evidence 标准 ────────────────────────────────┐ │
│  │ 至少 2 条高质量数据验证                          │ │
│  └────────────────────────────────────────────────┘ │
│                                                      │
│  [跳转到聊天记录]  [复制 prompt]                      │
└─────────────────────────────────────────────────────┘
```

**DAG 图交互**：
- 滚轮缩放，拖拽平移
- 双击节点 = 跳转到该 Agent 的聊天记录
- 悬停节点 = tooltip（状态 + 耗时 + 当前任务摘要，300ms 延迟显示）
- 运行中节点：左边框高亮 + 微蓝光呼吸（`box-shadow: 0 0 0 2px rgba(59,130,246,0.3)`）
- 已完成节点：绿色 ✓ 图标 + 左边框绿色
- 待运行节点：灰色 + 虚线边框
- 错误节点：红色 ✗ + 红色左边框

**坐标自动布局**：
- 使用 `dagre` 或 `@xyflow/react` 内置布局算法
- 按 DAG 拓扑分层，同层水平排列，层间垂直间距固定
- 节点宽度根据名称自适应
- 初始加载自动计算坐标，用户不手动编辑位置（v1 不支持拖拽编辑）

---

### 4.4 Goal 面板模块

#### 4.4.1 Goal 数据模型

```typescript
interface Goal {
  id: string;
  session_id: string;
  objective: string;                  // 目标描述
  status: 'active' | 'completed' | 'paused' | 'error';
  criteria: GoalCriteria[];           // 标准清单
  progress_pct: number;               // 进度百分比 (0-100)
  progress_detail: string;            // "5/7 标准已覆盖"
  evidence_count: number;             // Evidence 总数
  started_at: number;
  completed_at?: number;
}

interface GoalCriteria {
  id: string;
  description: string;
  status: 'pending' | 'in_progress' | 'covered';
  evidence_count: number;             // 关联 Evidence 数量
  agent_id?: string;                  // 负责的 Agent
}

interface GoalEvidence {
  id: string;
  criteria_id: string;
  agent_id: string;
  summary: string;                    // Evidence 摘要
  created_at: number;
}
```

#### 4.4.2 Goal Tab 布局（右主区）

```
┌─ Goal Tab ──────────────────────────────────────────────────────┐
│  🎯 目标: 研究动量因子在A股的表现                                  │
│                                                                  │
│  ▓▓▓▓▓▓▓░░░░  70%  ·  5/7 标准已覆盖                             │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ── 标准清单 ────────────────────────────────────────────────── │
│                                                                  │
│  ✅ 收集市场数据（价格、成交量、市值）                             │
│     └─ 📎 3 条 Evidence · data_quality  · 点击跳转               │
│                                                                  │
│  ✅ 计算动量因子（1M/3M/6M/12M）                                  │
│     └─ 📎 2 条 Evidence · factor_analyst · 点击跳转              │
│                                                                  │
│  ⏳ 分析因子表现（收益、风险、稳定性）← 进行中                    │
│     └─ 📎 1 条 Evidence · factor_analyst · 点击跳转              │
│                                                                  │
│  ⚪ 与基准指数对比                                                │
│                                                                  │
│  ⚪ 风控审查                                                     │
│                                                                  │
│  ⚪ 形成研究结论                                                  │
│                                                                  │
│  ⚪ 生成回测报告                                                  │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ── 时间线 ──────────────────────────────────────────────────── │
│                                                                  │
│  14:40  factor_analyst  ⏳ 因子表现分析中...                       │
│  14:37  factor_analyst  ✔ 反转因子计算完成                         │
│  14:35  factor_analyst  ✔ 动量因子计算完成                         │
│  14:32  data_quality    ✔ 数据分布合理性确认                       │
│  14:30  data_quality    ✔ 数据完整性验证通过                       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**关键设计决策**：
1. **进度计算** = 已覆盖标准数 / 总标准数（简单透明）
2. **标准带 Evidence 计数**：每条标准下方显示关联了几条 Evidence、来自哪个 Agent
3. **时间线倒序**：最新的在上，自动滚动到底部（可暂停）
4. **控制按钮不在这里**：暂停/恢复在 DAG 工具栏 + TopBar
5. **点击 Evidence** → 跳转到聊天区对应消息（滚动 + 高亮 2s 淡出）

**状态图标映射**：
| 标准状态 | 图标 | 颜色 |
|---|---|---|
| covered | ✅ | 绿 `#22C55E` |
| in_progress | ⏳ | 黄 `#EAB308` |
| pending | ⚪ | 灰 `#475569` |

---

### 4.5 SSE 事件可靠性

本节定义 SSE 事件流的可靠性机制，解决异步状态下的核心问题：消息确认、断连补偿、崩溃恢复、并发控制、连接管理、双流合并、事件节流。

#### 4.5.1 事件 ID + 重放机制

**解决**：SSE 断连后错过的事件无法追赶（P3-1），页面刷新后状态丢失（P2-3）。

```
后端:
  - 每个 SSE 事件分配递增 event_id（格式: {timestamp_ms}-{seq}）
  - 保留最近 5 分钟的事件缓冲区（环形缓冲，最大 10000 条）
  - 重连请求携带 Last-Event-ID header
  - 后端从该 id 起重放缓冲区内的事件

前端:
  - SSE 连接建立后记录最后收到的 event_id
  - 断连重连时发送 Last-Event-ID header
  - 重连后先收重放事件（replayed=true），再收新事件
  - 重放事件与本地 store 做去重（基于 event_id）
```

**事件格式扩展**：

```json
{
  "event_id": "1722153600000-042",
  "event": "text_delta",
  "replayed": false,
  "data": {"delta": "根据分析", "message_id": "msg_123"}
}
```

#### 4.5.2 端到端确认链

**解决**：`send_async` 返回 204 无 `message_id`，用户消息发送后无确认（P1-1, P2-1, P3-4）。

```
用户输入 → POST /api/chat/send_async
       ↓
后端:
  1. 创建 user message（status=pending）
  2. 返回 {message_id, event_id, status: "accepted"}
  3. 启动 AgentLoop（异步）
       ↓
前端:
  1. 收到响应 → 立即插入 user message（确认态）
  2. 清空输入框
  3. SSE 流开始 → assistant 消息出现
       ↓
失败场景:
  1. 网络错误 → catch → 保留输入内容 + 显示 toast
  2. 后端 4xx/5xx → 保留输入内容 + 显示 toast + 消息列表插入"发送失败"系统消息
  3. 后端返回 status: "rejected" → 保留输入内容 + 显示原因
```

**send_async 响应格式变更**：

```typescript
interface SendAsyncResponse {
  message_id: string;      // user message ID
  event_id: string;        // 首个事件的 event_id
  status: 'accepted' | 'rejected';
  error?: string;          // rejected 时的原因
}
```

#### 4.5.3 Agent 崩溃恢复

**解决**：Agent 在流式输出中途崩溃，前端已累积部分内容但永远等不到 `assistant_message`（P3-2）。

```
AgentLoop 崩溃时后端发送:
  1. error 事件（包含 error + partial_content）
  2. assistant_message 事件（如果有的话，包含已累积的完整内容）

前端 error 事件处理:
  1. 将当前 streaming 状态标记为 "aborted"
  2. 在 UI 中显示 "⚠ 生成中断" 提示（红色提示条）
  3. 保留已收到的部分内容（不丢弃）
  4. pending 状态的 tool_call 标记为 error
  5. Agent 状态设为 error + finished_reason = "error"
```

**error 事件扩展**：

```json
{
  "event": "error",
  "data": {
    "error": "LLM API rate limit exceeded",
    "message_id": "msg_123",
    "partial_content": "根据分析，动量因子...",
    "agent_id": "researcher",
    "fatal": true
  }
}
```

#### 4.5.4 前端 Store 原子更新

**解决**：多 Agent 事件交错时 Store 更新无并发控制（P4-1），流式→最终态切换可能闪烁（P2-2）。

```
数据结构:
  - chat.messages: Map<message_id, Message>（O(1) 查找，不数组 splice）
  - chat.streamingMessageId: string | null（标记当前流式消息）
  - agents.map: Map<agent_id, AgentState>

更新策略:
  - 所有事件处理基于 message_id + call_id 做精确 patch
  - 不全量替换 messages 数组，只更新单条消息的 parts
  - 使用 Zustand immer 中间件确保不可变更新
  - text_delta 使用 requestAnimationFrame 批量合并到下一帧

流式→最终态切换:
  1. 收到 assistant_message（event_id=X）
  2. 查找 streamingMessageId 对应的消息
  3. 原子替换: content = final_content, parts = final_parts
  4. 清除 streamingMessageId = null
  5. StreamingText 组件检测到 streamingMessageId 变化后卸载
```

**Store 更新伪代码**：

```typescript
// text_delta: 增量追加（合并到下一帧）
case 'text_delta':
  requestAnimationFrame(() => {
    const msg = messages.get(data.message_id);
    if (msg) {
      msg.content += data.delta;
      messages.set(data.message_id, msg);
    }
  });

// tool_call: 精确插入 tool_call part
case 'tool_call':
  const msg = messages.get(data.message_id);
  msg.parts.push({ type: 'tool_call', call_id: data.call_id, ... });
  messages.set(data.message_id, msg);

// assistant_message: 原子替换
case 'assistant_message':
  const msg = messages.get(data.message_id);
  msg.content = data.content;
  msg.parts = data.parts;
  streamingMessageId = null;
  messages.set(data.message_id, msg);
```

#### 4.5.5 连接生命周期管理

**解决**：两条独立 SSE 流的并行管理未定义（P1-2），JWT 过期对 SSE 长连接的影响（P3-3），多标签页状态不同步（P4-3）。

```
SSEManager（单例）:
  - 管理所有 SSE 连接的生命周期
  - 按需建立: 只有 workflow 运行时才开 workflow SSE
  - session 切换时: 断开旧 chat SSE → 建立新 chat SSE
  - workflow 完成时: 关闭 workflow SSE

连接状态机:
  disconnected → connecting → connected → reconnecting → disconnected
                                      ↘ disconnected (主动关闭)

JWT 过期处理:
  - SSE 连接建立时携带当前 token
  - 后端心跳事件中附带 token_expiry 字段
  - 前端检测到 token 即将过期（<5min）→ 刷新 token → 重建 SSE 连接
  - 后端收到过期 token 的 SSE 请求 → 返回 401 → 前端刷新 token 后重连

双流合并:
  - chat SSE 和 workflow SSE 的事件统一加 source 字段
  - source: "chat" | "workflow"
  - 前端 Store 按 event_id 全局排序（跨流）
  - 两条流的事件时间戳差异 <100ms 时视为同一时刻

多标签页（v1 不处理）:
  - 后续用 BroadcastChannel API 同步关键状态变更
  - 或监听 storage event
```

**SSEManager 接口**：

```typescript
interface SSEManager {
  connectChat(sessionId: string): void;
  connectWorkflow(goalId: string): void;
  disconnectAll(): void;
  onEvent(callback: (event: SSEEvent) => void): void;
  getLastEventId(): string | null;
  refreshToken(newToken: string): void;
}
```

#### 4.5.6 SSE 事件节流与批处理

**解决**：多 Agent 并行时事件频率过高（20-30/s），导致 Store 更新和 React 重渲染性能瓶颈（P5-2）。

```
后端节流:
  - text_delta: 合并为 50ms 间隔发送（攒多个 delta 一次性发）
  - tool_heartbeat: 合并为 5s 间隔（不每个 heartbeat 都发）
  - agent_iteration / agent_iteration_end: 不节流（低频事件）

前端批处理:
  - text_delta: 使用 requestAnimationFrame 合并到下一帧
  - 多个 tool_call/tool_result 在同一帧内到达 → 批量更新 Store
  - React 渲染优化: MessageList 使用 React.memo + areEqual
  - AgentList 使用 useMemo 缓存排序结果

性能目标:
  - 正常场景: <5 事件/s → 无需优化
  - 高峰场景: 20-30 事件/s → 后端节流后 <10/s + 前端批处理
```

#### 4.5.7 图片上传→消息关联流程

**解决**：图片上传到消息关联的完整流程未闭环（P1-6）。

```
1. 用户在 Composer 粘贴/拖拽图片
2. 前端预览（本地 blob URL）+ 上传到 /api/chat/upload
3. 后端保存到 /tmp/uploads/，返回 {image_url, mime_type}
4. 用户点击发送 → send_async({content, images: [image_url]})
5. 后端创建 user message，parts_json 中包含 ImagePart
6. Agent 收到图片 → 可能通过 tool 产出图片
7. Agent 通过 image SSE 事件发回图片 → 前端渲染 ImageBlock
8. 图片消息持久化到 web_messages.parts_json
```

**图片存储策略**：
- ≤5MB：base64 inline（存在 parts_json 的 ImagePart.url 中）
- >5MB：存 `/tmp/uploads/{uuid}.{ext}`，url 为相对路径
- 过期清理：7 天未访问的图片自动删除

---

## 5. 认证设计

### 5.1 数据库

```sql
-- users.db (新增)

-- 用户表
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,       -- bcrypt
    display_name TEXT,
    avatar_url TEXT,
    created_at REAL DEFAULT (unixepoch()),
    last_login REAL,
    is_active INTEGER DEFAULT 1
);

-- Web 会话表（独立于 CLI sessions.db）
CREATE TABLE web_sessions (
    id TEXT PRIMARY KEY,               -- UUID
    user_id INTEGER REFERENCES users(id),
    title TEXT DEFAULT '新会话',
    created_at REAL DEFAULT (unixepoch()),
    updated_at REAL DEFAULT (unixepoch()),
    is_active INTEGER DEFAULT 1
);

-- Web 消息表（支持新的 MessagePart 结构）
CREATE TABLE web_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES web_sessions(id),
    goal_id TEXT,                       -- 关联的 Goal（可选，用于 Evidence→消息反查）
    role TEXT NOT NULL,                -- user/assistant/system
    agent_id TEXT,                     -- 产出此消息的 Agent（assistant 消息才有）
    content TEXT,                      -- 完整文本（最终态）
    parts_json TEXT,                   -- JSON MessagePart[]（新结构）
    created_at REAL DEFAULT (unixepoch()),
    metadata_json TEXT                 -- model/tokens_used/iteration
);

-- Agent 实时状态表（Workflow 运行时写入，含 ReAct Loop 扩展）
CREATE TABLE web_agent_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT,                        -- 品牌色 hex
    status TEXT DEFAULT 'pending',     -- idle/pending/running/completed/error/skipped
    layer INTEGER,
    started_at REAL,
    completed_at REAL,
    duration_ms REAL,
    current_task TEXT,
    output_summary TEXT,
    tools_json TEXT,                   -- JSON string[]
    tokens_used INTEGER DEFAULT 0,
    tokens_limit INTEGER DEFAULT 0,
    iteration INTEGER DEFAULT 0,
    iteration_limit INTEGER DEFAULT 0,
    error TEXT,
    -- ReAct Loop 扩展字段
    finished_reason TEXT,              -- stop/max_iter/no_progress/error
    tool_calls_count INTEGER DEFAULT 0,-- 已执行工具调用总数
    compaction_count INTEGER DEFAULT 0,-- 上下文压缩次数
    context_tokens INTEGER DEFAULT 0,  -- 当前上下文窗口使用量
    context_tokens_limit INTEGER DEFAULT 0, -- 上下文窗口上限
    iterations_detail_json TEXT,       -- JSON IterationDetail[]
    updated_at REAL DEFAULT (unixepoch())
);

CREATE INDEX idx_messages_session ON web_messages(session_id, created_at);
CREATE INDEX idx_agent_states_goal ON web_agent_states(goal_id, agent_id);
```

### 5.2 认证流程

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  前端     │     │  后端     │     │  SQLite  │
│          │     │          │     │          │
│ POST     │────▶│ 验证密码  │────▶│ 查询用户  │
│ /login   │     │ 生成 JWT │     │          │
│          │◀────│          │◀────│          │
│ 存储     │     │          │     │          │
│ token    │     │          │     │          │
│          │     │          │     │          │
│ GET      │────▶│ 验证 JWT │     │          │
│ /api/*   │     │ 注入 user│     │          │
│          │◀────│ 响应数据  │     │          │
└──────────┘     └──────────┘     └──────────┘
```

### 5.3 JWT 配置

```python
# auth 配置
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
```

### 5.4 密码安全

- 使用 `bcrypt` 哈希（cost factor = 12）
- 密码最少 8 位
- 登录失败 5 次后锁定 15 分钟（可选，v1 不实现）

### 5.5 Session 隔离

- CLI 会话存 `sessions.db`，Web 会话存 `users.db`
- 两个数据库完全隔离，互不影响
- Web 会话通过 `user_id` 关联用户，CLI 会话不需要用户认证

---

## 6. API 端点清单

### 6.1 认证 API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/auth/register` | POST | `{username, password, display_name?}` | `{user_id, username}` | 注册 |
| `/api/auth/login` | POST | `{username, password}` | `{access_token, refresh_token, user}` | 登录 |
| `/api/auth/me` | GET | — | `{user}` | 当前用户 |
| `/api/auth/refresh` | POST | `{refresh_token}` | `{access_token}` | 刷新 token |

### 6.2 聊天 API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/chat/send` | POST | `{session_id, content, images?, agent_id?}` | `{message_id, reply}` | 同步发送（保留给非流式场景，如 webhook/批处理） |
| `/api/chat/send_async` | POST | `{session_id, content, images?, agent_id?}` | `{message_id, event_id, status}` | 异步发送（返回确认，不返回 204） |
| `/api/chat/events` | GET | `?session_id=X` | SSE stream | 实时事件流 |
| `/api/chat/history` | GET | `?session_id=X&limit=50&before=msg_id` | `{messages[]}` | 历史消息（游标分页） |
| `/api/chat/upload` | POST | multipart form | `{image_url, mime_type}` | 上传图片 |
| `/api/chat/message` | DELETE | `?message_id=X` | 204 | 删除消息 |

**SSE 事件类型**（含 ReAct Loop 桥接事件）：

| 事件 | data 字段 | 说明 | 来源 |
|---|---|---|---|
| `text_delta` | `{delta, message_id}` | 流式文本增量 | AgentLoop |
| `thinking_start` | `{message_id, thinking_id}` | 思考开始 | AgentLoop |
| `thinking_delta` | `{delta, message_id, thinking_id}` | 思考增量 | AgentLoop |
| `thinking_done` | `{message_id, thinking_id}` | 推理文本结束 | AgentLoop |
| `thinking_end` | `{message_id, thinking_id}` | thinking block 可关闭（loading 状态结束） | AgentLoop |
| `tool_call` | `{call_id, tool_name, arguments, message_id, agent_id}` | 工具调用开始 | AgentLoop |
| `tool_result` | `{call_id, status, result, duration_ms, message_id, agent_id}` | 工具调用结果 | AgentLoop |
| `file_edit` | `{file_path, additions, deletions, diff, message_id}` | 文件编辑 | Tool |
| `table` | `{headers, rows, caption, message_id}` | 数据表格 | Tool |
| `chart` | `{chart_type, data, title?, message_id}` | 图表数据 | Tool |
| `assistant_message` | `{message_id, agent_id, content, parts}` | 完整消息 | AgentLoop |
| `image` | `{url, mime_type, message_id}` | 图片 | Tool |
| `error` | `{error, message_id}` | 错误 | AgentLoop |
| `heartbeat` | `{timestamp}` | 心跳（30s 间隔） | Server |
| `agent_iteration` | `{agent_id, iteration, max_iterations}` | ReAct 迭代开始 | AgentLoop |
| `agent_iteration_end` | `{agent_id, iteration, tool_calls_count, duration_ms, tokens_used}` | ReAct 迭代结束 | AgentLoop |
| `agent_tool_progress` | `{agent_id, tool, call_id, stage, current, total, message}` | 工具执行进度 | AgentLoop |
| `agent_compaction` | `{agent_id, level, kept_messages, removed_messages}` | 上下文压缩 | AgentLoop |
| `agent_usage` | `{agent_id, prompt_tokens, completion_tokens, total_tokens}` | Token 用量 | AgentLoop |
| `agent_finished` | `{agent_id, finished_reason, tool_calls_total, compaction_count}` | Agent 循环结束 | AgentLoop |

**事件来源映射**（AgentLoop → SSE）：

```
AgentLoop._emit()           →  SSE Event              →  Web UI Store 更新
─────────────────────────────────────────────────────────────────────────────
thinking_start/done/end     →  thinking_start/done/end  →  chat.thinking
text_delta                  →  text_delta              →  chat.streaming
tool_call                   →  tool_call               →  chat.toolCalls
tool_result                 →  tool_result             →  chat.toolCalls
tool_progress               →  agent_tool_progress     →  agents.iterations
tool_heartbeat              →  (忽略，仅内部保活)       →  —
iter_start                  →  agent_iteration         →  agents.iterations
iter_end                    →  agent_iteration_end     →  agents.iterations
assistant_message           →  assistant_message       →  chat.messages
compact                     →  agent_compaction        →  agents.compaction
llm_usage                   →  agent_usage             →  agents.tokens
error                       →  error                   →  chat.error
```

### 6.3 会话 API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/session/create` | POST | `{title?}` | `{session_id, title}` | 创建会话 |
| `/api/session/list` | GET | `?limit=50` | `{sessions[]}` | 列出会话 |
| `/api/session/update` | PATCH | `{session_id, title}` | `{ok}` | 更新标题 |
| `/api/session/delete` | DELETE | `?session_id=X` | 204 | 删除会话 |

### 6.4 Goal/Workflow API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/goal/start` | POST | `{session_id, objective, risk_tier?}` | `{goal_id}` | 创建 goal |
| `/api/goal/status` | GET | `?session_id=X` | `{goal}` | goal 状态（含 criteria + evidence） |
| `/api/goal/workflow/start` | POST | `{session_id, workflow_name, objective}` | `{goal_id}` | 启动 workflow |
| `/api/goal/workflow/status` | GET | `?goal_id=X` | `{progress, agents[]}` | workflow 进度 + agent 状态 |
| `/api/goal/workflow/events` | GET | `?goal_id=X` | SSE stream | workflow 事件（agent 状态变化 + workflow 事件） |
| `/api/goal/workflow/pause` | POST | `{goal_id, immediate?}` | `{ok}` | 暂停 |
| `/api/goal/workflow/resume` | POST | `{goal_id}` | `{ok}` | 恢复 |
| `/api/goal/workflow/list` | GET | — | `{workflows[]}` | 列出 presets |
| `/api/goal/workflow/{name}/dag` | GET | — | `{dag}` | DAG 结构（nodes + edges） |
| `/api/goal/workflow/{name}/agents` | GET | — | `{agents[]}` | agent 详情（含 color 字段） |

**Workflow SSE 事件类型**（含 AgentLoop 桥接）：

| 事件 | data 字段 | 说明 | 来源 |
|---|---|---|---|
| `agent_status` | `{agent_id, status, layer?, duration_ms?, current_task?}` | Agent 状态变化 | WorkflowEventBus |
| `agent_output` | `{agent_id, summary}` | Agent 输出摘要 | GoalWorkflowHook |
| `agent_loop` | `{agent_id, event, data}` | Agent 内部 ReAct 事件透传 | AgentLoop → SSE 适配器 |
| `workflow_progress` | `{pct, completed, total, elapsed_ms}` | 全局进度更新 | GoalWorkflowRunner |
| `workflow_complete` | `{goal_id, status}` | Workflow 完成 | GoalWorkflowRunner |
| `workflow_error` | `{goal_id, error}` | Workflow 错误 | GoalWorkflowRunner |
| `evidence_collected` | `{agent_id, criteria_id, evidence_id}` | Evidence 采集 | GoalWorkflowHook |

**agent_loop 事件透传**（Workflow SSE 的子通道）：

```json
{
  "event": "agent_loop",
  "data": {
    "agent_id": "researcher",
    "event": "agent_iteration",
    "data": {
      "iteration": 3,
      "max_iterations": 10,
      "tool_calls_count": 5,
      "tokens_used": 1234
    }
  }
}
```

前端 Store 处理逻辑：
```
agent_loop.event == "agent_iteration"     → 更新 agents[agent_id].iteration
agent_loop.event == "agent_iteration_end" → 追加 iterations_detail[]
agent_loop.event == "agent_compaction"    → agents[agent_id].compaction_count++
agent_loop.event == "agent_finished"      → agents[agent_id].finished_reason = data.reason
```

### 6.5 Agent API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/agent/list` | GET | — | `{agents[]}` | 可用 agents（含 color） |
| `/api/agent/status` | GET | `?goal_id=X` | `{agents_status[]}` | 运行状态（含 ReAct 扩展字段） |
| `/api/agent/{goal_id}/{agent_id}/iterations` | GET | — | `{iterations: IterationDetail[]}` | 迭代历史详情 |
| `/api/agent/{goal_id}/{agent_id}/events` | GET | — | SSE stream | 单个 Agent 的 ReAct 事件流 |

---

## 7. 开发计划

### 7.1 阶段划分

| 阶段 | 内容 | 天数 | 依赖 | 产出 |
|---|---|---|---|---|
| **P1: 脚手架** | React 项目 + Vite + Tailwind + 路由 + 布局骨架 + 认证 | 3 | 无 | 可登录的空壳应用 |
| **P2: 聊天核心** | 消息列表 + 流式渲染 + Markdown + 输入框 + SSE + 后端 chat API | 4 | P1 | 可对话的聊天应用 |
| **P3: 工具调用** | ToolCallGroup + ThinkingBlock + TableBlock + FileEditBlock + 图片 | 3 | P2 | 完整的消息渲染 |
| **P4: Agent + Goal** | AgentList + GoalTab + 跨模块联动 | 2 | P2 | Agent 状态 + Goal 进度 |
| **P5: Workflow DAG** | React Flow DAG + 节点详情侧栏 + 启动/暂停 + 实时状态 | 3 | P2, P4 | 可视化工作流 |
| **P6: Command Palette + 优化** | Cmd+K + 虚拟滚动 + 响应式 + 性能优化 | 2 | P1-P5 | 完整交互体验 |
| **P7: 集成测试 + 部署** | 全链路测试 + 打包 + 静态文件部署 | 2 | P1-P6 | 可部署版本 |
| **总计** | | **~19 天** | | |

### 7.2 P1 详细任务（脚手架 + 认证）— 3 天

**Day 1：React 项目初始化**
1. `npm create vite@latest webui/frontend -- --template react-ts`
2. 安装依赖：tailwindcss, @tailwindcss/vite, zustand, react-router-dom, lucide-react, @radix-ui/react-dialog, @radix-ui/react-popover, @radix-ui/react-tabs, @radix-ui/react-tooltip
3. 配置 Tailwind 深色主题 + CSS 变量（色板）
4. 配置 Vite proxy → FastAPI 后端

**Day 2：布局骨架**
5. 实现 AppShell（三栏布局 + TopBar + IconNav）
6. 实现 TopBar（搜索框 + 会话标题 + 状态 + 用户头像）
7. 实现 IconNav（64px 图标栏 + hover 浮层）
8. 实现 MainSplit（可拖拽分隔条 + localStorage 记忆）
9. 实现 RightPanel（三 Tab 容器）
10. 实现 ResizablePanel 通用组件

**Day 3：认证 + SSE 可靠性基础**
11. 后端 `api/routers/auth.py`（register/login/me/refresh）
12. 后端 `api/middleware/auth.py`（JWT 验证中间件）
13. 后端 `users.db` 初始化（表创建）
14. 前端 LoginPage + RegisterPage
15. 前端 AuthGuard 路由守卫
16. 前端 API Client（fetch wrapper + JWT 自动注入 + token 刷新）
17. 前端 Zustand auth store
18. 后端 `api/routers/session.py`（create/list/update/delete）
19. 前端 Zustand session store + sidebar 会话列表
20. 后端 SSE 事件缓冲区（环形缓冲，5 分钟 / 10000 条）
21. 后端 `send_async` 返回 `{message_id, event_id, status}`（不再是 204）
22. 前端 SSEManager 单例（连接状态机 + Last-Event-ID 重连）
23. 前端 `hooks/useSSE.ts`（SSE 连接管理 + 心跳检测）
24. 前端 Toast 组件（Toast.tsx + ToastManager.tsx + toast store）
25. 前端 ErrorBoundary（全局错误边界 + fallback UI）

### 7.3 P2 详细任务（聊天核心）— 4 天

**Day 4-5：消息渲染 + 原子更新**
1. 实现 chat store（messages: Map + streamingMessageId + immer 中间件）
2. 实现 MessageList（react-virtuoso 虚拟滚动）
3. 实现 MessageBubble（用户消息）
4. 实现 AssistantMessage（Agent 头部 + parts 容器 + aborted 状态）
5. 实现 StreamingText（打字机效果 + requestAnimationFrame 批处理）
6. 实现 MarkdownRenderer（react-markdown + remark-gfm + react-syntax-highlighter）
7. 实现 SSE 事件处理：text_delta/tool_call/tool_result → Store 原子更新

**Day 6：输入框 + 确认链**
8. 实现 Composer（textarea + 高度自适应 + Shift+Enter 换行）
9. 实现 MentionDropdown（@agent / @file 两组下拉 + 键盘导航）
10. 实现图片粘贴/拖拽上传 + 本地预览
11. 实现 send_async 确认链（发送→确认→插入→清空→失败回滚）
12. 实现 keyboard shortcuts hook（Cmd+K/G/W/B/1/2/3）
13. 实现 Skeleton 加载组件（MessageSkeleton + AgentSkeleton）

**Day 7：后端 chat API**
11. 后端 `api/routers/chat.py`（send/send_async/events/history/upload/message）
12. 实现 SSE 事件流（StreamingResponse + async generator）
13. 实现游标分页（based on created_at + id）
14. 实现图片上传（/tmp/uploads/，限制 10MB）
15. 前端 Zustand layout store（右主区可见性 + Tab 状态 + 面板比例）

### 7.4 P3 详细任务（工具调用 + 思考）— 3 天

**Day 8：工具调用**
1. 实现 ToolCallGroup（折叠容器，默认折叠，点击展开）
2. 实现 ToolCallItem（状态图标 + 工具名 + 参数摘要 + 耗时）
3. 实现 tool_call / tool_result SSE 事件处理

**Day 9：思考 + 文件编辑**
4. 实现 ThinkingBlock（默认折叠，每条独立展开/折叠）
5. 实现 FileEditBlock（unified diff 渲染，默认展开）
6. 实现 file_edit SSE 事件处理

**Day 10：表格 + 图表 + 图片**
7. 实现 TableBlock（表格渲染，默认展开，>5行截断）
8. 实现 ChartBlock（简单图表：bar/line，基于 SVG 或 recharts）
9. 实现 table/chart SSE 事件处理
10. 实现 ImageBlock（图片展示 + 点击放大）
11. 实现 ImageLightbox（全屏图片查看，基于 Radix Dialog）

### 7.5 P4 详细任务（Agent + Goal）— 2 天

**Day 11：Agent 列表 + 详情**
1. 实现 AgentList（右主区 Tab，列表式布局）
2. 实现 AgentItem（running 展示迭代历史，其余紧凑一行）
3. 实现 Agent 颜色标识（按顺序分配蓝/绿/紫/橙/青）
4. 实现 agent_status / agent_loop SSE 事件处理
5. 实现 AgentDetailPanel（点击 Agent 展开详情侧栏）

**Day 12：Goal 面板**
5. 实现 GoalTab（目标描述 + 进度条 + 标准清单 + 时间线）
6. 实现 CriteriaList（标准带 Evidence 计数 + Agent 来源）
7. 实现 GoalTimeline（倒序时间线，最新在上）
8. 实现点击 Evidence → 聊天区滚动跳转（scrollIntoView + 高亮 2s 淡出）

### 7.6 P5 详细任务（Workflow DAG）— 3 天

**Day 13：DAG 可视化**
1. 安装 @xyflow/react + dagre
2. 实现 WorkflowDAG（React Flow 容器 + dagre 自动布局）
3. 实现 DAGNode（自定义节点：状态色左边框 + Agent 颜色 + 状态图标）
4. 实现 DAGEdge（自定义边：灰色 → 运行中蓝高亮）

**Day 14：DAG 交互**
5. 实现 DAGToolbar（名称 + 启动/暂停/恢复按钮）
6. 实现 DAGProgressBar（底部进度条 + 百分比 + 耗时）
7. 实现 DAGNodeDetail（右侧滑出面板：prompt/条件/tools/evidence）
8. 实现节点交互：滚轮缩放、拖拽平移、双击跳转、悬停 tooltip

**Day 15：Workflow 启动 + 实时**
9. 实现 WorkflowList（preset 卡片列表，hover 展开详情）
10. 实现 WorkflowStart（启动表单：选择 preset + 输入 objective）
11. 接入 workflow SSE 事件（agent_status/workflow_progress/workflow_complete）
12. 实现 TopBar 暂停/恢复按钮（与 DAG 工具栏同步）

### 7.7 P6 详细任务（Command Palette + 优化）— 2 天

**Day 16：Command Palette**
1. 实现 CommandPalette（Cmd+K 触发 + 模糊搜索 + 键盘导航）
2. 实现命令注册机制（可扩展的命令列表）
3. 实现命令分类（会话/工作流/设置/视图）

**Day 17：性能 + 响应式 + SSE 节流**
4. 实现后端 text_delta 合并（50ms 间隔发送）
5. 实现前端 text_delta 批处理（requestAnimationFrame 合并）
6. 优化虚拟滚动（消息列表 + Agent 列表 + GoalTimeline）
7. 实现响应式断点（≥1440px 三栏 / 1024-1439px 两栏 / <1024px 单栏）
8. 实现 `prefers-reduced-motion` 支持
9. 实现 NavPopover（导航栏 hover 浮层：会话列表/工作流列表）
10. 错误边界 + 全局错误处理 + SSE 连接状态 toast

### 7.8 P7 详细任务（测试 + 部署）— 2 天

**Day 18：测试**
1. 组件单元测试（关键组件：MessageList, ToolCallGroup, DAGNode）
2. API 集成测试（认证 + 聊天 + session）
3. E2E 测试（登录 → 发消息 → 看到回复 → 启动 workflow → 看到 DAG）

**Day 19：部署**
4. Vite build 配置（output → `webui/static/`）
5. FastAPI 静态文件 mount（`/` → `webui/static/index.html`）
6. 生产环境配置（JWT_SECRET, CORS, 日志）
7. 文档更新（README + API 文档）

---

## 8. 文件变更清单

### 8.1 新增文件

| 文件 | 说明 |
|---|---|
| `webui/frontend/` | React 项目目录 |
| `webui/frontend/package.json` | 前端依赖配置 |
| `webui/frontend/vite.config.ts` | Vite 配置（proxy + build output） |
| `webui/frontend/tailwind.config.ts` | Tailwind 深色主题配置 |
| `webui/frontend/src/main.tsx` | 入口 |
| `webui/frontend/src/App.tsx` | 根组件 + 路由 |
| `webui/frontend/src/styles/globals.css` | CSS 变量 + Tailwind 入口 |
| `webui/frontend/src/api/client.ts` | fetch wrapper + JWT 注入 |
| `webui/frontend/src/api/sse.ts` | SSE 连接管理器 |
| `webui/frontend/src/api/types.ts` | TypeScript 类型定义 |
| `webui/frontend/src/stores/auth.ts` | 认证状态 |
| `webui/frontend/src/stores/session.ts` | 会话状态 |
| `webui/frontend/src/stores/chat.ts` | 聊天状态 |
| `webui/frontend/src/stores/agents.ts` | Agent 状态 |
| `webui/frontend/src/stores/workflow.ts` | Workflow 状态 |
| `webui/frontend/src/stores/layout.ts` | 布局状态 |
| `webui/frontend/src/stores/commandPalette.ts` | 命令面板状态 |
| `webui/frontend/src/components/layout/AppShell.tsx` | 整体布局容器 |
| `webui/frontend/src/components/layout/TopBar.tsx` | 顶部导航栏 |
| `webui/frontend/src/components/layout/IconNav.tsx` | 左侧图标导航栏 |
| `webui/frontend/src/components/layout/NavPopover.tsx` | 导航栏 hover 浮层 |
| `webui/frontend/src/components/layout/MainSplit.tsx` | 左右主区分隔 |
| `webui/frontend/src/components/layout/RightPanel.tsx` | 右主区 Tab 容器 |
| `webui/frontend/src/components/layout/CommandPalette.tsx` | Cmd+K 命令面板 |
| `webui/frontend/src/components/layout/ResizablePanel.tsx` | 可拖拽面板通用组件 |
| `webui/frontend/src/components/auth/LoginPage.tsx` | 登录页 |
| `webui/frontend/src/components/auth/RegisterPage.tsx` | 注册页 |
| `webui/frontend/src/components/auth/AuthGuard.tsx` | 路由守卫 |
| `webui/frontend/src/components/chat/MessageList.tsx` | 消息列表（虚拟滚动） |
| `webui/frontend/src/components/chat/MessageBubble.tsx` | 用户消息气泡 |
| `webui/frontend/src/components/chat/AssistantMessage.tsx` | 助手消息 |
| `webui/frontend/src/components/chat/StreamingText.tsx` | 流式文本 |
| `webui/frontend/src/components/chat/ThinkingBlock.tsx` | 推理过程 |
| `webui/frontend/src/components/chat/ToolCallGroup.tsx` | 工具调用组 |
| `webui/frontend/src/components/chat/ToolCallItem.tsx` | 单个工具调用 |
| `webui/frontend/src/components/chat/FileEditBlock.tsx` | 文件 diff |
| `webui/frontend/src/components/chat/TableBlock.tsx` | 数据表格 |
| `webui/frontend/src/components/chat/ChartBlock.tsx` | 图表 |
| `webui/frontend/src/components/chat/ImageBlock.tsx` | 图片展示 |
| `webui/frontend/src/components/chat/Composer.tsx` | 输入框 |
| `webui/frontend/src/components/chat/MentionDropdown.tsx` | @mention 下拉 |
| `webui/frontend/src/components/chat/MarkdownRenderer.tsx` | Markdown 渲染器 |
| `webui/frontend/src/components/agents/AgentList.tsx` | Agent 列表 |
| `webui/frontend/src/components/agents/AgentItem.tsx` | Agent 条目 |
| `webui/frontend/src/components/agents/AgentDetailPanel.tsx` | Agent 详情侧栏 |
| `webui/frontend/src/components/workflow/WorkflowDAG.tsx` | DAG 可视化 |
| `webui/frontend/src/components/workflow/DAGNode.tsx` | 自定义 DAG 节点 |
| `webui/frontend/src/components/workflow/DAGEdge.tsx` | 自定义 DAG 边 |
| `webui/frontend/src/components/workflow/DAGToolbar.tsx` | DAG 工具栏 |
| `webui/frontend/src/components/workflow/DAGProgressBar.tsx` | DAG 进度条 |
| `webui/frontend/src/components/workflow/DAGNodeDetail.tsx` | 节点详情侧栏 |
| `webui/frontend/src/components/workflow/WorkflowList.tsx` | Preset 列表 |
| `webui/frontend/src/components/workflow/WorkflowStart.tsx` | 启动表单 |
| `webui/frontend/src/components/goal/GoalTab.tsx` | Goal Tab 容器 |
| `webui/frontend/src/components/goal/CriteriaList.tsx` | 标准清单 |
| `webui/frontend/src/components/goal/GoalTimeline.tsx` | Evidence 时间线 |
| `webui/frontend/src/components/common/Badge.tsx` | 状态徽章 |
| `webui/frontend/src/components/common/Spinner.tsx` | 加载动画 |
| `webui/frontend/src/components/common/EmptyState.tsx` | 空状态 |
| `webui/frontend/src/components/common/ConfirmDialog.tsx` | 确认弹窗 |
| `webui/frontend/src/components/common/Toast.tsx` | Toast 通知 |
| `webui/frontend/src/components/common/ToastManager.tsx` | Toast 队列管理 |
| `webui/frontend/src/components/common/Skeleton.tsx` | 通用骨架屏 |
| `webui/frontend/src/components/common/ImageLightbox.tsx` | 图片全屏查看 |
| `webui/frontend/src/components/common/ErrorBoundary.tsx` | 全局错误边界 |
| `webui/frontend/src/hooks/useKeyboardShortcuts.ts` | 键盘快捷键管理 |
| `webui/frontend/src/hooks/useSSE.ts` | SSE 连接管理 |
| `webui/frontend/src/hooks/useMention.ts` | @mention 搜索 |
| `api/routers/auth.py` | 认证 API |
| `api/routers/chat.py` | 聊天 API |
| `api/routers/session.py` | 会话 API |
| `api/middleware/auth.py` | JWT 中间件 |
| `docs/design/web-ui-design.md` | 本文档 |

### 8.2 修改文件

| 文件 | 改动 |
|---|---|
| `api/app.py` | 注册 auth/chat/session router + 静态文件 mount |
| `api/routers/workflow.py` | 增加 DAG/agent 端点 + SSE 事件 |
| `pyproject.toml` | 新增 python-jose, bcrypt 依赖 |

### 8.3 保留文件（后续可移除）

| 文件 | 说明 |
|---|---|
| `webui/routes.py` | 旧 Jinja2 路由 |
| `webui/templates/` | 旧 HTML 模板 |

---

## 9. 待讨论项

| # | 问题 | 推荐方案 | 状态 |
|---|---|---|---|
| 1 | 图片存储 | ≤5MB base64 inline + >5MB 文件系统（/tmp/uploads/）+ 7 天过期 | ✅ 已确认 |
| 2 | 消息分页 | 游标分页（based on created_at + id） | ✅ 已确认 |
| 3 | SSE 重连策略 | 指数退避 1s→30s + 最大 10 次 + Last-Event-ID 重放 | ✅ 已确认 |
| 4 | WebSocket | 不需要（SSE 单向够用，Agent 状态通过 agent_loop 透传） | ✅ 已确认 |
| 5 | 国际化 | 先硬编码中文，后续用 react-i18next | ✅ 已确认 |
| 6 | 构建部署 | Vite build → `webui/static/`，FastAPI mount 静态文件 | ✅ 已确认 |
| 7 | 虚拟滚动库 | react-virtuoso（MessageList + AgentList + GoalTimeline） | ✅ 已确认 |
| 8 | 图表库 | recharts（bar/line/pie/scatter） | ✅ 已确认 |
| 9 | 多标签页同步 | v1 不处理，后续用 BroadcastChannel | ✅ 已确认 |
| 10 | JWT + SSE 过期 | 宽限期 5 分钟 + 心跳检测 401 → 刷新 token → 重连 | ✅ 已确认 |
