# 输入框对标改造 C/D 子方案（R-014 · 待确认 · 零代码）

> 日期：2026-11-24
> 状态：子方案。用户已表达「还需要做 c+d」，但两项均涉及**后端核心契约变更**且存在**定义缺失**，需确认关键决策后再动代码。
> 依据：`composer-layout-industry-benchmark-20261124.md`（对标）、`context-usage-agent-survey-20261124.md`（用量调研）、C/D 后端契约只读调研（本文档 §1/§2）。

---

## C · 7 段分段上下文用量

### C.1 调研结论（实事求是）

| 能力 | 状态 |
|---|---|
| 7 段拆分数据 | ❌ **完全缺失** |
| LLM usage 细粒度字段（cache_read/details） | ⚠️ 部分存在：`parser.py:212-219,320-326` 只保留数值型顶层键，嵌套 details 被丢弃 |
| 单一总量 + 当前占用 `session_total_tokens.context_used` | ⚠️ 事件已定义但**生产 chat SSE bridge 未透传**（`apps/chat/events.py:51-61`、`chat.py:755-763`） |
| 模型窗口/成本字段 `ModelInfo` | ✅ 已有（`foundation/llm/model_catalog.py:45-75`），但成本字段无人消费 |
| 上下文按来源记账 `ContextBundle.accounting` | ⚠️ 按 provider 聚合，不按 7 段（`kernel/context/hub.py:140-149`） |

**最关键的阻碍**：ZCode 的 7 段中，`meta_user_context` / `mcp_tool_schemas` 在 AgentNodes 后端**根本没有对应概念**（`kernel/context/protocol.py:35-37` 的 kind 列表、`kernel/agent/context/core.py:57-125` 的 system prompt 模板均无此分割）。这不是"透传缺失"，是**定义缺失**。

### C.2 实施路径（分两步）

**C-1 · 先修实测值透传（前置，收益最大）**
1. `apps/chat/events.py:56-61` 的 `STREAM_PASSTHROUGH_KINDS` 加入 `llm_usage` / `session_total_tokens`（或 `chat.py:755-763` 加 mapping）。
2. 注意生产 chat 走 Gateway 路径（`apps/chat/send_turn.py`），需要确认这两个事件确实到达 bridge；若 Gateway sink 未转发，还需在 `kernel/runtimes/nodes/agent_loop_node.py:119-129` 的 sink 处补。
3. 前端 `hooks/sse/controlHandlers.ts:12-46` 的 handler 已注册**无需改协议**——透传后 `source` 自动从 `estimated` 切 `measured`，`~` 前缀消失。
4. 验证：发一条真实消息，确认前端显示从 `~1.5K` 变成无 `~` 的实测值。

**C-2 · 再定义 7 段（需产品决策）**
1. **先定义段归属规则**（最关键）：
   - `messages` = 历史对话
   - `system_prompt` = 系统提示词
   - `tool_prompt` / `system_tool_schemas` = 工具定义与 schema
   - `skills` = `ContextItem.kind=skill`
   - `meta_user_context` / `mcp_tool_schemas` = **AgentNodes 当前无对应概念**，需决定是"新增概念"还是"前端隐藏这两段"
2. 在 `kernel/agent/telemetry/agent_telemetry.py:306-321`（已有 `system_prompt_len` / `tools_count` / `estimated_tokens` 长度指纹）扩展为按段估算，新增事件 `context.breakdown`（注册 `kernel/events/event_v2.py:130-141`）。
3. bridge 放行新事件。
4. 前端：SSE 事件类型（`hooks/sse/types.ts`）+ handler + store 字段 + 分段进度条 UI（基于 `components/chat/ContextUsageBar.tsx` 扩展，弹层形态参考 ZCode）。
5. 可选：`parser.py:212-219,320-326` 保留 `details` 子字典，以支持 cache hit 展示。

### C.3 验收

- C-1 后：真实消息下发后前端显示实测值（无 `~`），且 `GET /api/chat/session/{id}/trace` 可见用量事件
- C-2 后：用量弹层显示分段进度条（至少 5 段，或定义完整后的 7 段），各段之和 ≈ 总量
- 全量：前端测试 / tsc / lint、后端全量、gate 13/13、双主题基线重录

---

## D · 排队消息编辑态

### D.1 调研结论

| 能力 | 状态 |
|---|---|
| per-session FIFO 队列 + steer 队 + 暂停/恢复 | ✅ 已存在（`kernel/orchestration/mailbox.py:73-99` 等） |
| 排队状态可观测（status/queue_length/`/attempts`） | ✅ 已存在 |
| 取消正在执行的 attempt（连带暂停整会话） | ✅ 已存在（`gateway.py:363-383`），**粒度是整会话** |
| 按 attempt_id 编辑/替换排队消息 | ❌ 完全缺失（Mailbox 无 mutate-by-id API） |
| 按 attempt_id 只取消某一条排队消息 | ❌ 完全缺失 |
| `steerQueued`/`steerDrained` 协议 | ❌ 完全缺失（grep 零命中） |
| `chat_available_actions` | ⚠️ 只有 cancel/resume/send（`kernel/session/service.py:57-87`），前端未消费 |

### D.2 实施路径

**D-1 · 后端最小能力**
1. `mailbox.py:390-404`（`pending_attempt_ids()` 旁）新增：
   - `replace_pending(attempt_id, new_request) -> bool`：扫描 `_queue` + `_steer`，替换 payload text/prompt，保留队列位置；发 `queue.edited` 事件
   - `remove_pending(attempt_id) -> bool`：同样双队列扫描移除；发 `queue.cancelled` 事件
2. `gateway.py:410-428` 旁新增薄封装：`edit_queued_attempt()` / `cancel_queued_attempt()`（**不要**走 `cancel_session`，那会暂停整个 mailbox）
3. **数据一致性**：编辑时同步更新持久化的 `Attempt.prompt`（`chat.py:238`、`kernel/session/models.py:162` 附近）与已落库的用户消息（`chat.py:287-298`），否则刷新后仍是旧 prompt

**D-2 · API + 事件 + 前端**
1. 新端点 `POST /chat/queue/edit`、`POST /chat/queue/cancel`（`api/routers/chat.py:356-370` 附近），body `{session_id, attempt_id, content}`，复用 `enforce_session_owned`
2. schema 加在 `api/schemas/chat.py:26-51`
3. 事件注册 `kernel/events/event_v2.py` + bridge 放行（`apps/chat/events.py:56-61` 或 `chat.py` mapping）
4. 前端：
   - SSE 事件类型 + handler（`hooks/sse/types.ts`、`hooks/sse/queueHandlers.ts`、`hooks/sse/handlers.ts`）
   - store action `editQueuedMessage` / `cancelQueuedMessage`（`stores/chat.ts:224-227,367-401`），同步更新 `messages` Map 中的用户消息与 assistant 占位
   - API 方法（`api/client.ts:601-609` 旁）
   - UI 编辑条：在 `AssistantMessage.tsx:53-79,463-465` 的 `QueuedIndicator` 附近挂"编辑/取消"，形态参考 ZCode 的 `v4-composer-edit-bar`（容器内顶部提示条 + 取消按钮）；可选择把编辑条放在 A 改造后的 composer 卡片顶部（更贴近 ZCode）

### D.3 验收

- 发送多条消息形成队列 → 排队项显示"编辑"入口 → 点编辑 → composer 顶部出现编辑条 + 取消 → 确认后该排队消息内容更新且位置不变 → 刷新页面内容仍是新的
- 取消单条排队消息 → 只移除该条，其他排队项与正在执行的 attempt 不受影响
- 全量：前端测试 / tsc / lint、后端全量、gate 13/13、双主题基线重录、实机双宽度

---

## 规模与风险评估

| 项 | 涉及层 | 文件数（估） | 风险 |
|---|---|---|---|
| C-1 | L1 apps + 前端 | 2-3 | 低（只加事件直通） |
| C-2 | L2 kernel + L1 + 前端 + 定义缺失 | 8-12 | **高**（7 段定义需产品决策） |
| D-1 | L3 kernel（mailbox/gateway）+ 数据一致性 | 4-6 | **高**（队列数据一致性） |
| D-2 | L1 API + 事件 + 前端全链路 | 8-10 | 中高 |

## 需要用户决策的点

1. **C-2 的 7 段定义**：`meta_user_context` / `mcp_tool_schemas` 无对应概念——
   - 选项 A：前端只显示有数据的 5 段（messages/system_prompt/skills/tool_prompt/system_tool_schemas）
   - 选项 B：后端新增这两个概念的归属规则（工作量显著增加）
2. **C 是否分两步**：先做 C-1（实测值透传，低风险立竿见影），C-2 另案？
3. **D 的编辑条位置**：挂在排队消息卡片上（`QueuedIndicator` 附近）还是 composer 卡片顶部（更贴近 ZCode）？
4. **D 的取消粒度**：按 attempt_id 取消单条（推荐），还是沿用整会话取消？

## 建议顺序

1. **C-1 先行**（低风险、直接让现有 F4-b 的估算变实测）
2. **D-1 + D-2**（完整编辑态，价值高但需仔细处理数据一致性）
3. **C-2**（等 7 段定义决策后）

## 不做

- 不做 E（语音/worktree/CUA）
- 不恢复 Markdown 按钮
- 不 push

## 回滚

每步独立提交；C-1 回滚只需移除直通集合条目；D 回滚涉及新端点与事件，需整批 revert。

## 10. 实施进展（2026-11-24）

### C-1 已改动的代码

- `apps/chat/events.py`：`STREAM_PASSTHROUGH_KINDS` 加入 `llm_usage` / `session_total_tokens`
- `tests/test_chat_events.py`：同步直通集合 parity 断言
- 验证：事件翻译测试 7/7 通过

**实机验证受阻**：重启后端后发消息，`tokensUsed` 仍为空（`store: {tokensUsed:{}}`），后端日志无用量事件记录 → 说明事件在 Gateway 链路更上游就未产生或未到达 bridge，而非仅被过滤。**C-1 尚未达成目标**，需继续排查 Gateway sink 的事件流。

### 用户新要求（本批插入）

- **重试次数改配置文件、最大 10 次，否则显示失败**：已将 `~/.agentnodes/llm.json` 的 `max_retries` 从 2 改为 **10**（顶层 + minimax profile），备份 `llm.json.bak-retries-20261124-132532`。语义为「总尝试次数」（`foundation/llm/config.py:165`），由 `openai_client.py` 的 `_request_with_retry` 执行；10 次全失败后异常上抛，前端 `Composer.tsx:180-198` 已有「发送失败：…」toast。
- **暂停运行服务**：已精确停止后端（PID 32160）与 Vite（PID 30896）。/services 处于停止状态，后续需按 `tmp/probe/start_backend_8766.py` 与 Vite 命令重启。

### 用户澄清：前端「重试失败」是会话初始化重试，不是 LLM max_retries

用户观察到「前端每隔几秒显示重试失败」，经查是 `AppShell.tsx` 的**会话初始化重试**（`setTimeout(init, 1000)` **无限循环**），与 LLM 的 `max_retries` 无关。服务暂停时 `loadSessions()` 必失败 → 每秒弹一次「正在重试…」。

**已修复**（R-014 同类纪律）：
- `AppShell.tsx`：新增模块级 `MAX_INIT_RETRIES = 10`；失败计数 `retryCount`，`retryCount < 10` 时继续 1s 重试并显示「正在重试…（第 N/10 次）」；达到 10 次后**停止自动重试**，UI 改为「已重试 10 次仍未成功，请检查服务是否运行」+「重新加载」按钮（手动重试）
- `appShellPermission.test.tsx`：新增重试上限用例（假定时器推进 10 次，断言调用次数恰好 10、之后不再增加、显示手动重试按钮）
- 验证：前端 1414 测试 / tsc OK / lint 0 error
- 注意：服务仍处暂停状态，未做实机验证（UI 行为已被单测覆盖）

### 用户反馈：不要写死，统一配置（本批完成）

用户指出「修复不合理，为什么不能统一配置，而是写死的」。已按「统一到 /system/info + 前端默认兜底」重构：

- **后端**：`api/routers/system.py` 的 `/system/info` 响应 `llm` 增加 `max_retries`（来自 `LLMConfig.load().max_retries`，即 `llm.json`）——**单一事实源**，LLM 调用重试与会话初始化重试共用此值
- **前端**：
  - `stores/system.ts`：`LLMInfo` 加 `max_retries: number`（初始 0 = 尚未加载）；`fetchSystemInfo()` 从响应该字段
  - `AppShell.tsx`：删除硬编码 `MAX_INIT_RETRIES = 10`，改为模块级 `FALLBACK_MAX_RETRIES = 10`（**仅服务不可达/配置未加载时的兜底**）；每次失败时动态读 `useSystemStore.getState().llm.max_retries`，有配置用配置、无配置用兜底；错误态带 `maxRetries` 字段供 UI 显示真实上限
- **测试**：
  - 后端 `test_system_api.py` 新增用例验证 `/system/info` 下发 `max_retries=10`
  - 前端 `appShellPermission.test.tsx` mock system store 可变，新增「从 store 读配置值（3 次）」用例 + 原「服务不可达走兜底（10 次）」用例；补齐 `AssistantMessage.test.tsx` / `systemStore.test.ts` 的 `LLMInfo` mock 字段
- **验证**：前端 1415 测试 / tsc OK / lint 0 error；后端 8151 passed（架构数字随 system.py 行数漂移已同步 md+HTML：L1 22,115 / L2 11,718 / total 97,552）；gate 13/13 ALL GREEN
- **设计取舍**：会话初始化失败时后端恰好不可达，`/system/info` 拿不到 → 必须保留前端兜底常量；但正常路径（LLM 调用失败、服务正常）完全由 llm.json 一处配置驱动，改配置不再需要改前端代码
