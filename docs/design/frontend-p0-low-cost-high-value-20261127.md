# 前端 P0 底层优化专项（低成本高收益）

> **性质**：实施方案（doc first）。依据：`docs/ui-zcode-comparison-20261127.md`（未跟踪的外部调研稿，浏览器真机视角）与本轮 ZCode asar 只读调研 + 我方源码审计。
> **日期**：2026-11-27。
> **范围**：仅四项 P0 底层问题，不含 P1/P2 的布局重排、富文本扩展、动效扩展。

## 1. 背景与证据

ZCode/我方对比已完成，视觉骨架已接近；差距集中在交互深度、内容表现力、性能工程、配置真实接线。本专项只处理「花钱少、收益大」的四项：

| 编号 | 问题 | 证据 |
|---|---|---|
| P0-1 | 渲染链全量重渲染：`rawMessages` 每次渲染新建后 sort → `useMemo` 必然失效；组件订阅整个 messages Map；每个 `text_delta` 触发 3 次 store 写入 | `webui/frontend/src/components/chat/MessageList.tsx:83,93-100`；`webui/frontend/src/hooks/sse/textHandlers.ts:34-68`；`AssistantMessage.tsx:326` |
| P0-2 | SSE 回放仅部分幂等：`error` toast 已按 key 去重，但 `text_delta` / `thinking_delta` 无条件追加；后端每条 SSE 带 `id: {event.id}`，前端只把它当游标 | `webui/frontend/src/hooks/useSSE.ts:133-138,162`；`src/agentnodes/interfaces/api/routers/chat.py:1093-1094` |
| P0-3 | `chat-ui-config.json` 半接线：多个 getter 无调用点；`visibility.*` 与 `studyChat` 全库无消费；部分文案硬编码双事实源 | `webui/frontend/src/components/chat/chatUiConfig.ts:182,193,197,205`；`config:21-24,43-45,67-74,96-99,117-122,134-137,152-165`；`MessageList.tsx:183,231,241,264`；`MessageBubble.tsx:91,115,121,135`；`ThinkingBlock.tsx:51-63` |
| P0-4 | 上下文明细断链：`BreakdownList` 显示“分段数据待接入”，但 `useContextUsage` 已可从 `contextBreakdown` 取 10 细段 | `ContextRing.tsx:175-199`；`useContextUsage.ts:31-57` |

## 2. 批次与验证

每批 git first / doc first、路径限定提交、不 push；提交前 `npx vitest run`、`npx tsc --noEmit`、`scripts\batch_gate.cmd`。

### 批 0 · 文档先行（本文件）
只提交本方案文档。

### 批 1 · 渲染链（P0-1）
文件：`MessageList.tsx`、`AssistantMessage.tsx`、`textHandlers.ts`、相关测试。

- `MessageList`：`rawMessages` 改为 `useMemo` 的稳定数组；依赖 `messages` Map 引用（Zustand 只在 Map 变化时重算），而不是每次渲染新建。
- `AssistantMessage`：把 `useChatStore((s)=>s.messages)` 的全 Map 订阅改为只订阅自己需要的 `getMessage(id)` 或缩小 selector 粒度，避免任一 delta 唤醒所有消息组件。
- `textDelta`：把 persistent update + `accumulatePartText` + `appendStreamingText` 三次 store 写合并为一次 `updateMessage` 内的写入（保留 preview buffer 的首字可见性与 legacy global stream 兼容，需验证不破坏首字即时显示）。
- 新增测试：渲染次数/selector 粒度契约测试（mock store 计数或用源码契约断言）；流式文本 3-step 协议用例保持绿。

### 批 2 · SSE 事件级幂等（P0-2）
文件：`useSSE.ts`、`sse/types.ts`、`sse/textHandlers.ts`、相关测试。

- 在 `useSSE` 的 `handleEvent` 内以 `e.lastEventId` 为键做**会话级去重**：已处理过的 id 直接 return；切会话清空集合（与 `seenErrorKeys` 同生命周期）。
- 保留现有 `lastEventId.current` 游标语义；去重集合只做“已处理事件”判断，不改变重连游标推进。
- 兼容没有 `id` 的事件（heartbeat、老事件）：无 id 不走去重，保持原行为。
- 新增测试：同一 `lastEventId` 的 `text_delta` 回放不追加；不同 id 正常追加；切会话后同一 id 可重新处理。

### 批 3 · 配置真实接线（P0-3）
文件：`chatUiConfig.ts`、`chat-ui-config.json`、`MessageList.tsx`、`MessageBubble.tsx`、`ThinkingBlock.tsx`、`ToolCallBlock.tsx`、相关测试。

- 让 `getMessageListConfig` 的 `loadMore / errorLabel / errorDetail / compactionLabel` 在 `MessageList.tsx` 真实消费；消灭 4 处硬编码。
- 让 `getThinkingConfig` 的 `labels.streamingFormat / doneFormat / charCountSuffix` 与 `sizing.charThreshold` 在 `ThinkingBlock.tsx` 消费；消灭硬编码。
- 让 `getUserBubbleConfig`（新增 getter）的 `labels.*` 在 `MessageBubble.tsx` 消费；`getToolCallConfig` 的 `dangerousTools` 成为唯一事实源（代码内 `DANGEROUS_TOOLS` 删除或改为配置兜底）。
- `visibility.*` 字段逐项接线：能接的接（如 `showQuickStartChips` / `showCompactBanner` / `showContextBar` / `showDaySeparators` / `showVerifiabilityBadge` / `showStreamingStatus` / `showCopy` / `showCharCount` / `allowEdit` / `showThemeToggle`）；`studyChat` 段若当前无页面消费则保留配置但在文档标注“待页面接入”，不强行造行为。
- 新增测试：修改配置后对应文案/可见性在组件渲染中生效（至少覆盖 loadMore、thinking label、userBubble label、危险工具集合）。

### 批 4 · 上下文明细断链（P0-4）
文件：`Composer.tsx`、`ContextRing.tsx`、相关测试。

- `Composer` 的 hover 明细从 `EMPTY_SEGMENTS` 改为传入真实 `contextBreakdown`（按 sessionId 从 store 读取），复用已有 `aggregateBreakdown`；保留常态单色小环（用户裁决）。
- 明细空态从“分段数据待接入”改为“暂无上下文用量数据”，避免误导。
- 新增测试：明细弹层展示不低于 1 行聚合数据（有 breakdown 时）；无数据时显示新文案。

## 3. 不属于本专项

- ZCode 折叠算法、`@/$` 入口、附件拖放、KaTeX/Mermaid/Shiki、工具 progressbar、快捷键/无障碍系统性改造（P1/P2）。
- 浏览器真机稿 `docs/ui-zcode-comparison-20261127.md` 提出的 375px 菜单遮挡、768px 三栏拥挤（其 P0 定义与本专项不同）；这些属于布局重排，按用户后续裁决另立专项。
- 构建分包与体积门禁（P2）。

## 4. 回滚

每批独立提交，失败可单独 revert。SSE 幂等与配置接线若引发行为异常，优先 revert 对应批次而非整链。

## 5. 验证矩阵

| 批 | 前端测试 | tsc | gate | 真机 |
|---|---|---|---|---|
| 1 | `npx vitest run` | `npx tsc --noEmit` | 通过 | 发送一条消息，流式文本逐字显示无延迟 |
| 2 | 同上 | 同上 | 通过 | 断线重连后文本不重复；错误 toast 不重复 |
| 3 | 同上 | 同上 | 通过 | 改配置文案后界面文案随之变化 |
| 4 | 同上 | 同上 | 通过 | hover 小环显示真实分段明细，常态仍是单色环 |
