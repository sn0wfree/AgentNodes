# 导航侧边框入口调整方案（R-014 · 已批准实施 · 零代码起步）

> 日期：2026-11-23
> 状态：方案 + 实施记录。用户已明确「开始实施」。
> 触发：① DAG 导航仍存在（`3f62291e` 方案只出未实施）；② 调研各 agent 界面后的侧边导航入口建议；③ 此前 Trace 独立入口诉求（放入侧边框）。

## 1. DAG 入口现状（已自证）

`3f62291e` 方案出了但未实施，5 处 DAG 入口全部仍在：
- `IconNav.tsx:27` 编排导航项（所有用户可见）
- `App.tsx:57,61` `/workflow` `/workflow-definition` 重定向；`:80,83` `/dag` 路由
- `MonitorPage.tsx:208-212` 编排 QuickEntry
- `CommandPalette.tsx:51-57` 「打开编排页」命令
- `useKeyboardShortcuts.ts:35` Cmd+W → /dag

## 2. 业界调研结论（2026-11-23）

### 2.1 各产品侧栏入口（事实 + URL）

| 产品 | 侧栏一级入口 | 来源 |
|---|---|---|
| **ZCode**（本地逆向 v3.18.2） | Workspace / Sessions（pin/archive/fork）/ Projects / Subagents 看板 / Plan / History / Files·Diff / Terminal / Skills / Settings；**无 react-router，tab/workspace 式；工作流在画布内不做一级图标** | `docs/ui-zcode-source-research-20261114.md` |
| **OpenClaw** | Home / New session / Sessions / Chat / Dashboard / Online / Inbox / Agents / Automations / Usage / Settings；子代理与进程是 Chat 停靠面板 | https://docs.openclaw.ai/web/control-ui |
| **OpenCode** | Sessions 多标签 + Recent sessions sidebar + `/agents` `/sessions` `/models`；工作流无独立入口 | https://opencode.ai/desktop/、https://opencode.ai/docs/cli/tui/ |
| **Codex** | 资料受限（IDE 文档 403），确认 IDE 扩展 + CLI + App/Web 三形态，侧栏细节未编造 | https://github.com/openai/codex/blob/main/README.md |
| **Claude Code** | Sessions + history + 分组过滤 + Agent map + Bookmarks + plugins；**工作流降级为 `/workflows` 斜杠命令** | https://code.claude.com/docs/en/vs-code.md |
| **Windsurf/Devin** | Start new session / Recent sessions / Recent projects / Open project / Workflows 能力卡 / Agent Command Center | https://docs.devin.ai/desktop/getting-started |

### 2.2 共性模式

**高频必备（≥4/6）**：Sessions/History、Chat、Projects/Workspace、Agents/Subagents、Files/Diff/Review、Tools/Plugins/MCP、Settings、Command Palette。

**关键结论**：
- **没有一款把 DAG 编排做成一级侧栏图标**——Workflow 要么降级为斜杠命令（Claude Code）、要么在画布内（ZCode）、要么是能力卡（Devin）。撤销 AgentNodes 的 `/dag` 图标符合业界。
- **Agents/Subagents 是 5/5 产品的一等入口**——AgentNodes 当前缺。
- **Trace/可观测通常归 Settings/Dashboard 或停靠面板**——用户要求升为一级侧栏属差异化创新，需明确 Monitor（实时状态）vs Trace（历史回放）边界。

## 3. 目标导航结构

```
[Logo / 首页 /]
──────────────────
Chat        (保留，toggleSidebar 行为不变)
Trace       (新增，用户诉求；路由 /trace，历史会话选择 + 事件时间线)
──────────────────
Account     (保留，已合并用户管理)
Logout
```
- **DAG**：5 处前端入口全撤；`/dag` 路由 + `DAGPage` + 24 条 API 全保留（URL 直入可达，开发后门）；CommandPalette 保留一个隐藏命令「打开编排页」供键盘可达（业界"降为命令"模式）
- **不恢复** Study/因子库/策略库/知识库（假入口，与 DAG 隐藏同源）
- **Agents / Files / Automation**：调研建议列为 P1/P2 候选，需先确认后端契约，本批不做

## 4. 实施批次

### 批 N1：撤销 DAG 前端入口（P0）

1. `IconNav.tsx:27`：删 `NAV_ITEMS` 的 `/dag` 项 + 注释更新
2. `MonitorPage.tsx:207-213`：删编排 QuickEntry（4→3 项），测试 `monitorPage.test.tsx:167-182` 同步
3. `useKeyboardShortcuts.ts:35`：删 Cmd+W → /dag 绑定（测试 `:129-134` 同步）；保留 Cmd+Shift+W 关会话
4. `CommandPalette.tsx:51-57`：保留「打开编排页」命令但改为隐藏关键词触发（保留键盘可达，不显示在默认命令列表——具体做法：加 `hidden: true` 或仅在输入 "dag/workflow" 时匹配；实施时按现有 CommandPalette 过滤逻辑确定最小改法）
5. `App.tsx`：`/dag` 路由 + 两个 Navigate **保留**（URL 直入可达，API 零改动）
6. 测试：新增「IconNav 无编排项 + /dag 路由仍挂载 + API 端点仍注册」成对断言

### 批 N2：Trace 独立入口与页面（P0）

1. `IconNav.tsx`：新增 `{ to: '/trace', label: 'Trace', icon: Activity }`
2. `App.tsx`：新增 `/trace` 路由（AuthGuard 包裹）
3. `pages/TracePage.tsx`（新建）：顶部会话选择器（复用 `useSessionStore.sessions` 本账户历史，默认当前会话）+ 右侧 `TraceViewer`（复用现有组件，sessionId props 已支持）
4. `RightPanel.tsx`：删 Q12 的 Trace 入口行与接管分支（入口并入侧栏）
5. 权限：API 403/404 已有（`enforce_session_owned`），前端只列本账户会话；不提供任意 sessionId 输入
6. 测试：`TracePage` 会话选择/403 边界 + 基线重录

## 5. 验收

- 图标栏只剩 Chat / Trace / Account / Logout
- 直接访问 `/dag` 仍能渲染 DAG 页（路由保留）；`GET /api/goal/workflow/definitions` 仍 200
- 普通用户与 admin 都能用 Trace；跨账户 session 请求得 403
- 前端全量 / tsc / lint、后端全量、gate 13/13、双主题基线重录

## 6. 不做

- 不删 `/dag` 路由 / DAGPage / 24 条 API / catalog DAG 故事
- 不恢复 Study/因子库/策略库/知识库假入口
- 本批不做 Agents / Files / Automation 入口（P1/P2 候选，后端契约待确认）

## 7. 来源

调研 URL 见 §2.1；完整报告留存于本次会话记录。
