# Trace 独立入口 + 权限边界 & DAG 前端隐藏方案（R-014 · 已由后续方案实施）

> 日期：2026-11-23
> 性质：方案（历史记录）。本文方案已由 `sidebar-navigation-redesign-20261123.md` 实施（提交 536e1f95 + 506a6a13）；保留原文供反方论证与事实追溯。
> 触发：用户请求——① Trace 做成单独功能放入侧边框；② 权限：Trace 只能看本账户历史 session，进行中只能看已落库；③ DAG 前端不显示、不暴露入口，API 保留。

## 1. 已自证事实

- **后端归属校验已存在**：`GET /api/chat/session/{id}/trace`（`chat.py:937-1001`）已调用 `enforce_session_owned` → 403（他人）/404（不存在），user_id 取 `request.state.user_id or "anonymous"`（`authz.py:26-44`）。「只能看本账户 session」在 API 层**已实现**，无需新增鉴权。
- **「已落库」语义天然成立**：`EventStore.emit` 先 `_persist` 写 DB 再推 SSE（`event_store.py:266` vs `:274-278`）；TraceProjection 从 event_log 回放（`trace_projection.py:79-95`），trace.jsonl 仅 A1 前回退。两个数据源都是持久化介质，端点无 `live/in_progress` 字段。唯一例外：DB 降级为 InMemoryStore 时（`event_store.py:72-111`）数据只在进程内，端点无法区分——若严格需求需显式处理 `is_degraded`。
- **Trace 当前入口唯一**：只在 `RightPanel.tsx:101-111` 一行按钮，点击后 `TraceViewer` 接管右栏（Q12）；`App.tsx` 无 `/trace` 路由，无独立页/导航。sessionId 来源仅 `useSessionStore.currentSessionId`——**只看当前会话，无法看历史**。
- **本账户历史 session 数据源现成**：`GET /api/chat/session`（`web_session.py:237-248`）SQL `WHERE user_id = ?`；前端 `useSessionStore.sessions`（`session.ts:147-161`，另客户端过滤 `study*`）。

## 2. 方案 A：Trace 独立入口（侧边框）

### 2.1 落位

新增 `/trace` 路由（`App.tsx`，AuthGuard 包裹）+ `pages/TracePage.tsx`；导航入口放 `IconNav.tsx` 的 `NAV_ITEMS`（新增 Trace 项，图标如 `Activity`）；从 `RightPanel.tsx` 移除 Trace 入口行与接管分支（Q12 旧形态退役）。

### 2.2 历史会话选择

`TracePage` 顶部放会话选择器（复用 `useSessionStore.sessions` 的本账户历史列表，按 updated_at 倒序）；选中 sessionId 后传给 `TraceViewer.tsx:68-71` 的现有 props。**当前会话默认选中**；切换下拉 → 重拉 trace。

### 2.3 权限（无需新增后端鉴权）

- 所有 trace 请求带 Bearer token（`client.ts` 已有）→ 后端 `enforce_session_owned` 自动 403/404
- 前端 `sessions` 列表已按 user 过滤，兜底只会列出本账户会话
- 「进行中只看已落库」：API 天然满足（DB-first）；若需严格拒绝降级数据，后端端点加 `if service.event_bus.is_degraded: return 503`（需评估，非必须）
- 不新增「任意 sessionId 输入」入口（避免 study_ 自动重建的归属推断旁路 `chat.py:824-838`）

### 2.4 组件改动与风险

- `TraceViewer` 契约不变（仅 sessionId + onClose），`onClose` 在新页面改为 navigate 返回
- `limit=500` 硬编码（`TraceViewer.tsx:426`）+ 客户端 50 条分页（`:410,453`）在长历史会话有上限——独立页面场景可后续加游标（本批不做，标注限制）
- 敏感字段默认折叠（`SensitiveRaw`/`DiffSection`）保持，历史会话扩大暴露面但 API 403 守住

## 3. 方案 B：DAG 前端隐藏、API 保留

### 3.1 前端入口全量（5 处，全部撤销）

| 入口 | 位置 | 处置 |
|---|---|---|
| IconNav 编排导航项 | `IconNav.tsx:28` | 删 NAV_ITEMS 项 + 注释更新 |
| MonitorPage QuickEntry | `MonitorPage.tsx:207-213` | 删「编排」卡片，4→3（测试 `monitorPage.test.tsx:167-182` 同步） |
| CommandPalette `view-dag` 命令 | `CommandPalette.tsx:50-59` | 删该命令（测试 `:105-112,188-198` 同步） |
| 快捷键 Cmd+W → /dag | `useKeyboardShortcuts.ts:35,48` | 删该绑定（测试 `:129-134` 同步） |
| `/workflow` `/workflow-definition` 重定向 → /dag | `App.tsx:57-64` | 删或改指向（避免死链） |

### 3.2 保留层级（推荐方案③：入口全撤、路由保留）

`/dag` 路由与 `DAGPage.tsx` **保留**（直接 URL 仍可达，开发/回归后门）；API 与页面组件零改动。理由：`IconNav.tsx:22-24` 已有「路由与页面保持可达」的既有裁决；彻底删路由会引发 `viewPages.test.tsx` 编译失败、两个 Navigate 死链、catalog 组件孤儿，风险大收益小。

### 3.3 API 保留（零改动）

`/api/goal/workflow/*` 共 24 条路由（`workflow.py:81-737`），由 `app.py:214-216` 挂载——**全部原样保留**。当前唯一 React 消费者链是 /dag 页，路由保留则消费者不变；TUI/外部脚本也可能依赖。新增成对测试断言「前端无入口 + 路由/API 仍挂载」（放 `appRouting.test.tsx` + 后端 routes 断言）。

### 3.4 不做

- 不删 catalog 里的 DAG 组件故事（`stories.tsx:532-623`，是视觉样张非功能入口，删了破坏基线）
- 不删 `client.ts:477-669` 的 API 包装（契约保留证据）
- 不引入 feature flag（仓库无现成系统；硬删除 + git 恢复点 `6c27a72e`/`887e3068` 足够）

## 4. 批次建议（待批准后执行）

1. **批 T1**：Trace 独立页 + 导航 + 历史会话选择 + 权限测试（后端归属 403/404 已有，补端到端用例）
2. **批 T2**：RightPanel 退役 Trace 入口 + 基线重录
3. **批 D1**：DAG 5 处入口撤销 + 测试同步 + 「入口无/API 在」成对断言 + 基线重录

## 5. 风险

- Trace 历史会话页扩大敏感内容暴露面 → 403 守住 + 默认折叠保留；不做任意 ID 输入
- DB 降级（InMemory）时 trace 端点无法区分「未落库」→ 标注限制，严格模式另案
- DAG 入口撤销后 `/dag` 仅 URL 可达 → 产品需接受「隐蔽但可达」；若要求彻底不可达，改为方案②（删路由+页面，成本显著更高）

## 6. 验证

- Trace：建 A/B 两账户 → B 的会话 id 请求 trace 得 403；A 的历史会话在下拉可选且标题/事件正确；进行中会话只显示已落库事件
- DAG：前端 5 入口全部不存在（组件测试）；直接访问 `/dag` 仍可渲染（路由保留断言）；`GET /api/goal/workflow/definitions` 等 API 仍 200
- 全量前端/后端测试 + gate + 双主题基线重录
