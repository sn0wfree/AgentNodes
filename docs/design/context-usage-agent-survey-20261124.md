# 各 Agent 上下文用量实现调研（2026-11-24 · 零代码 · 事实记录 + 修复建议）

> 状态：**调研记录 + 修复建议**。本文不改任何实现；后端修复项需另出 R-014 方案并批准。
> 触发：用户「上下文用量需要细化，深入调研一下各个 agent 上下文用量都是怎么实现的」。
> 前端部分已先行落地（`composer-toolbar-p0p1-fix` 的 F4-b）：统一口径 + 细化显示。

---

## 0. 最重要的三条结论

1. **生产 Web chat 路径下，后端从不向前端推送任何 token 用量事件。**
   `llm_usage` / `session_total_tokens` 在事件翻译层被丢弃（`apps/chat/events.py:57-61` + `apps/chat/send_turn.py:372-374`），
   而权威的 `session_total_tokens` 唯一生产者挂在**已无生产调用者**的 legacy 路径上。
   → 前端 `tokensUsed` 恒为 0，这是「ComposerStatusBar / TokenCard 一直显示 0%」的直接原因。

2. **DAG / workflow / swarm / goal 四条路径后端根本没有 token 计数**——不是"没推到前端"，是 executor 把 loop 已算好的 `usage_tokens` 直接丢弃。

3. **前端三处 UI 口径彼此不一致**（已由 F4-b 修复）：阈值有两个事实源、sessionId 有三个来源、估算常数前后端不同（3.5 vs 3.0）。

---

## 1. 各执行路径的用量产出对照

| 路径 | 入口 | 产出 token 数？ | 口径 | 能到前端？ |
|---|---|---|---|---|
| **普通 chat turn（生产）** | `api/routers/chat.py:165` `/send_async` → `apps/chat/send_turn.py:429` → Gateway | ✅ 有（`llm_usage` + loop `_budget_*`） | 每次 LLM call 的**真实 usage** | ❌ 被翻译层丢弃 |
| legacy chat | `kernel/session/service.py:194` `send_message` | ✅ 有（`session_total_tokens`） | 累计 spend + `context_used` 覆盖 | ❌ 生产无调用者（`api/dependencies.py:136`） |
| **DAG / workflow 节点** | `kernel/workflow/node_types.py:279-301` → `kernel/agent/factory/role_factory.py:182-255` | ❌ **后端没算** | 只返回 answer 字符串 | ❌ |
| **swarm / goal（多 agent）** | `kernel/swarm/runtime.py:475-588` → `kernel/agent/plugins/executor.py:413-443` | ❌ loop 算了但 executor metrics 只留 3 项（`executor.py:438-442`） | — | ❌（事件止步于 goal 私有 bus `apps/goal/event_bus.py:63-74`） |
| fanout（分支运行时） | `kernel/runtimes/presets/fanout.py:124-137` | ✅ 有（`tokens/prompt_tokens/completion_tokens/cost_usd`） | 真实计费 | ❌ 只进 budget slice |
| `/compact`（手动） | `api/routers/chat.py:187-188` → `slash_common.py:104-139` → `kernel/session/service.py:1300-1414` | ⚠️ **口径是字符不是 token**（`service.py:1349-1350`） | 字符数 | ❌ 事件名 `compact.count`/`compact.ended` 不在前端 `EVENT_TYPES` |
| loop 内自动压缩 | `kernel/agent/loop/core.py:793-804` | — | — | ❌ 事件被丢弃 |
| study | 后端已于 2026-11-05 批次 3 删除（`api/app.py:218` 注释） | ❌ 无路径 | 前端 `components/study/*` 是残留 | ❌ |
| **CLI/TUI** | `interfaces/cli/ui/rail.py:100-102` | ✅ 有 | 累加 input/output | 不适用（唯一真正在消费 `llm_usage` 的消费者） |

## 2. 关键机制

### 2.1 权威用量生产者（已存在但接不上）

`kernel/session/loop_events.py:35-131` `_LoopEventForwarder`：
- `:94-108`：`input/output` **累加**；`context_used = prompt_tokens or input_tokens` **覆盖**（"最近一次 LLM 调用实际发出的 prompt 大小"，压缩后自然回落）
- `:112-123`：每次 `llm_usage` 都 emit `session_total_tokens`（payload 含 `input_tokens/output_tokens/total_tokens/context_used`）
- 挂载点 `kernel/session/service.py:991` → 只在 `SessionService.send_message` → **生产 HTTP 无调用者**

`kernel/agent/loop/core.py:755-763` `_record_budget_usage()` → `_budget_tokens`（真实计费）；`endings.py:121-123` 区分 `metrics["tokens"]`（字符估算）与 `metrics["usage_tokens"]`（真实计费）。

### 2.2 为什么会丢

```
/send_async → apps/chat/send_turn.py:429 send_turn
  → :229-390 _event_bridge（注册为 Gateway sink）
    → :372-374  out_kind = translate_engine_event(kind); if None: return   ★丢弃点
```
`apps/chat/events.py:51-61` 的直通集合只有 `tool_call/tool_result` + 流式事件；其余（含 `llm_usage`/`session_total_tokens`/`compact`/`subagent_*`/`loop_final`/`budget_exhausted`）→ `translate_engine_event()` 返回 None。

**连带影响**（同一机制）：
- `CompactBanner` 永不弹（收不到 `compact`）
- `subagentHandlers` 收不到 `subagent_*` → AgentCard 的 token 永远不显示
- **TraceViewer 的累计 token 曲线永不渲染**（`TraceViewer.tsx:277-285` 只认 `session_total_tokens`，且 `series.length < 2` 直接 return null）

### 2.3 口径差异汇总

| 量 | 定义 | 位置 |
|---|---|---|
| 真实计费 | provider 原生 usage 的 `input_tokens/output_tokens` | `loop/core.py:755-763` |
| 上下文占用 | 最近一次 prompt 尺寸（**覆盖**语义） | `loop_events.py:99-108` |
| 后端估算 | `chars / 3.0` | `foundation/utils/token_utils.py:12` |
| 前端估算 | `chars / 3.5` | `utils/contextUsage.ts`（F4-b 保留原值，见下） |
| 前端百分比 | `used / modelInfo.context_tokens` | `hooks/useContextUsage.ts` |
| 压缩触发阈值 | `context − max(max_output_tokens, buffer)`（~85-90%） | `kernel/agent/compact/core.py:705-727` |
| 上下文仲裁（截断） | `ctx_tokens * 0.8` | `kernel/gateway/session_controller.py:733-744` |

> 前后端估算常数 3.5 / 3.0 不一致，但前端统计口径（parts 文本 + tool_call 参数 + tool 结果）本就与后端（messages content）不同，**不宜直接统一**；F4-b 保持 3.5 并注释说明——改小会让估算值变大、提前触发 80% 自动压缩。

## 3. 缺口清单（前端看不到 / 显示失真的原因）

| # | 缺口 | 证据 |
|---|---|---|
| G1 | 生产 chat 无任何 token 事件下发 → `tokensUsed` 恒 0 | `apps/chat/events.py:57-61`、`apps/chat/send_turn.py:372-374` |
| G2 | `session_total_tokens` 生产者挂在死代码路径 | `loop_events.py:112-123` ← `session/service.py:991` ← `service.py:194`（`api/dependencies.py:136` 注明无调用者） |
| G3 | ComposerStatusBar / TokenCard 无估算兜底 → 恒 `0%` | ✅ **F4-b 已修** |
| G4 | DAG/workflow 路径后端零 token 统计 | `workflow/node_types.py:279-301`、`agent/factory/role_factory.py:182-255` |
| G5 | swarm/goal 丢弃 loop 的 `usage_tokens` | `agent/plugins/executor.py:438-442`、`swarm/runtime.py:579-588` |
| G6 | goal 的 `llm_usage` 止步于私有 bus | `apps/goal/workflow.py:371-374` → `apps/goal/event_bus.py:63-74`；`api/routers/workflow.py:291-312` 不过滤 usage |
| G7 | 刷新页面用量归零（无持久化、无 REST 回填） | `stores/chat.ts:436-457`（`loadMessages` 不恢复）、`:423-435`；`api/client.ts` 无相关端点 |
| G8 | `modelInfo` 是**全局单例**，session 换模型不更新 `context_tokens` → 百分比失真 | `stores/system.ts:35,75-87`（`refreshModelInfo` 生产未调用）、`stores/model.ts:58-61` |
| G9 | 三处 UI 的 sessionId 来源不同（编排面板下各指各的） | ✅ **F4-b 已修**（`useContextUsage(sessionId)` 显式传入） |
| G10 | 阈值双事实源 | ✅ **F4-b 已修**（统一到 `tokenThresholds.ts`） |
| G11 | 前后端估算常数不同 | `utils/contextUsage.ts`（3.5）vs `foundation/utils/token_utils.py:12`（3.0）——**有意保留**，见 §2.3 |
| G12 | `/compact` 报的是字符数却叫 tokens | `session/service.py:1349-1350`、`api/routers/slash_common.py:120` |
| G13 | `compact` 事件名不匹配（后端发 `compact.ended`，前端只认 `compact`） | `session/service.py:1393-1404`、`compact/loop_compaction.py:207-211` vs `hooks/sse/types.ts:123-124` |
| G14 | `subagent_completed` 无 `tokens_used` → AgentCard token 永不显示 | `builtin_tools/subagent_tool.py:272-275`、`hooks/sse/subagentHandlers.ts:131-149`、`chat/AgentCard.tsx:104-106` |
| G15 | 成本字段已有但未用（能算钱却没算） | `stores/system.ts:22-25`（cost_input/output/cache_*）、`foundation/llm/model_catalog.py:62-65`；前端无消费点 |
| G16 | TraceViewer 累计 token 曲线永不渲染 | `chat/TraceViewer.tsx:277-285` 依赖 `session_total_tokens`（见 G2） |
| G17 | 前端自动压缩基于估算值触发，与后端阈值口径不同 | `ContextUsageBar.tsx:97-118`（80%）vs `kernel/agent/compact/core.py:719-727` |

## 4. 已落地的前端部分（F4-b，零后端改动）

| 项 | 前 | 后 |
|---|---|---|
| 口径来源 | 三处各算各的 | 统一 `utils/contextUsage.ts` + `hooks/useContextUsage.ts` |
| sessionId | 三个来源 | 调用方显式传入 `useContextUsage(sessionId)` |
| 阈值 | 两份事实源 | 统一 `tokenThresholds.ts`（50/80） |
| 显示（输入框状态行） | `0% 128K`（恒 0） | `~1.5K / 1.0M` `0.1%`（`~` = 估算） |
| 显示（右栏 TokenCard） | `0.0%`（恒 0） | `~1.5K / 1.0M (0.1%)` |
| 显示（聊天流顶部） | 有估算兜底 | 同上，格式统一 |
| hover 详情 | 无 | 已用/上限、剩余可用、单次输出上限、数据来源（实测 vs 估算）、陈旧标记 |
| 进度条填充色 | 硬编码 `bg-red-500`/`bg-emerald-500` | 语义令牌 `bg-danger`/`bg-success` |

**为什么现在显示的是估算值**：后端尚未透传（G1）。F4-b 的设计已预留——后端一旦下发实测值，`source` 自动从 `estimated` 切到 `measured`，界面去掉 `~` 前缀，**前端无需再改**。

## 5. 后端修复建议（待讨论，未实施）

按「收益 / 成本」排序：

| 优先级 | 建议 | 改动点 | 收益 |
|---|---|---|---|
| **P0-a** | **让 chat turn 的用量事件到达前端** | 在 Gateway sink（`kernel/gateway/session_controller.py:606-634`）累加 usage 并 emit `session_total_tokens`；或在 `apps/chat/events.py` 的直通集合加 `llm_usage`（但生产不走 `_LoopEventForwarder`，需在 sink 处补累计器） | 前端从"估算"变"实测"，G1/G2/G16 一并解决 |
| **P0-b** | **修 `compact` 事件名不匹配** | 后端改发前端已注册的 `compact` / `compact_count`（`hooks/sse/types.ts:123-124`） | CompactBanner 能弹、G13 |
| **P1-a** | **DAG/workflow 路径补 token 统计** | `agent/factory/role_factory.py` 把 loop 的 `usage_tokens` 透出；`workflow/node_types.py` 收进节点 metrics | G4；DAG 页也能显示用量 |
| **P1-b** | **swarm/goal 补 usage 透传** | `agent/plugins/executor.py:438-442` metrics 加 `usage_tokens`/`usage_cost_usd`；`apps/goal/workflow.py:371-374` bridge 透传；`api/routers/workflow.py:291-312` 过滤加上 usage | G5/G6 |
| **P1-c** | **修 `/compact` 的"tokens 实为字符"** | `session/service.py:1349-1350` 改用 `foundation/utils/token_utils.py:15` 的 `estimate_tokens` | G12 |
| **P2-a** | **`modelInfo` 随会话模型更新** | `stores/model.ts:58-61` 切换模型时调 `refreshModelInfo`；或后端提供按 (provider, model) 查询 | G8 |
| **P2-b** | **刷新后用量回填** | 新增 `GET /api/chat/session/{id}/usage` 返回该会话累计 usage | G7 |
| **P2-c** | **展示成本** | 已有 `modelInfo.cost_input/cost_output` + usage 的 input/output 拆分 → 可算花费 | G15 |
| **P3** | **前端自动压缩阈值与后端对齐** | 前端不再自行触发 `/compact`，交给后端压缩策略；或前端阈值改为与 `compact/core.py:719-727` 同源 | G17 |

## 6. 需要用户/负责人决策的点

1. **P0-a 的实现位置**：在 Gateway sink 累计（改动 `kernel/gateway/`，L3）还是在 `apps/chat` 的 bridge 里透传（改动 L2）？后者更贴近产品层、影响面小，但需把 loop 的 usage 事件从 Gateway 侧取到。
2. **DAG / goal 是否要显示用量**（P1-a/P1-b）——这决定了要不要动 L3 的 executor 契约。
3. **`modelInfo` 是全局还是按会话**（P2-a）——影响 `context_tokens` 口径的全局一致性。
4. **前端是否保留自行触发 `/compact`**（P3）——当前前端按估算值 80% 触发，与后端压缩策略并行，存在重复压缩风险。

## 7. 来源

- 前端：`utils/contextUsage.ts`、`hooks/useContextUsage.ts`、`components/chat/ContextUsageBar.tsx`、`components/chat/ComposerStatusBar.tsx`、`components/context/TokenCard.tsx`、`stores/chat.ts`、`stores/system.ts`、`utils/tokenThresholds.ts`
- 后端：`apps/chat/events.py`、`apps/chat/send_turn.py`、`kernel/session/loop_events.py`、`kernel/session/service.py`、`kernel/agent/loop/core.py`、`kernel/agent/loop/endings.py`、`kernel/gateway/session_controller.py`、`kernel/workflow/node_types.py`、`kernel/agent/factory/role_factory.py`、`kernel/agent/plugins/executor.py`、`kernel/swarm/runtime.py`、`apps/goal/event_bus.py`、`api/routers/workflow.py`、`api/routers/chat.py`、`api/routers/slash_common.py`、`foundation/utils/token_utils.py`、`foundation/llm/model_catalog.py`
