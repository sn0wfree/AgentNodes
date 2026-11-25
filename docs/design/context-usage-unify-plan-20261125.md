# 上下文用量统一数据源 — ZCode 调研与实施方案

> 2026-11-25 · 问题 1 的方案 + 实施记录（已实施并真机验证）

## 一、ZCode 实现调研（基于已提取的 renderer/host bundle）

### 1.1 完整数据流

```
后端 LLM request 装配
  → 生成 contextUsageBreakdown: [{id, tokens, chars, items?}]  (11 段)
  → usage_update 事件: {used, size, cost?, cache?, breakdown?}
       used = Rxe(usage) = inputTokens + outputTokens   ← 来自 LLM API 真实 usage
       size = contextWindow
       breakdown = bxe(payload) = parse(contextUsageBreakdown)
  → 前端 Csr() 合并当前/新 usage（防闪烁）
  → sen() 按 source 聚合 chars → 段内百分比 → cen() 映射颜色 → 圆环/弹层
```

关键代码（压缩后，保留函数实名）：
- `Ixe` / `contextUsageFromPayload`：从 payload 提取 `{used, size, breakdown}`
- `Rxe`：used = `inputTokens + outputTokens`（真实 API usage）
- `bxe` / `optionalContextUsageBreakdownFromPayload`：解析 breakdown 数组
- `fxe`：`usage_update` 事件一次性推送 used/size/breakdown
- `Csr`：合并策略 —— 新 usage 缺 breakdown 但 used/size 未变 → 继承旧 breakdown；
  新 used 无效（≤0）且非 /compact → 保留旧 usage
- `sen`：按 source 聚合 chars，段内占比 = chars / 总 chars
- `len`：守卫 —— used≤0 或 size≤0 → 不显示（null）

### 1.2 11 段协议（AgentNodes 是 7 段）

```
system_prompt, meta_user_context, skills, tool_prompt,
system_tool_schemas, mcp_tool_schemas,
app_tool_schemas, deferred_tool_schemas, reminders, compact_summary, messages
```

每段结构：`{id, tokens: N, chars: N, items?: [{name, tokens}]}（items 最多 50）`
测量阶段 `phase: "request" | "draft-preview"`，带 `revision / measuredAt / model`。

### 1.3 与我们现状的关键差异

| 维度 | ZCode | AgentNodes 现状 |
|---|---|---|
| 数据源 | 单一 `usage` 对象：`used/size/breakdown` 一体 | 三源分离：tokensUsed（实测）+ 前端字符估算 + contextBreakdown（独立事件） |
| used 来源 | **LLM API 真实 usage**（input+output） | tokensUsed 是 session 累计（session_total_tokens），前端估算只算消息文本 |
| 空闲展示 | `len()` 守卫：used≤0 → 不显示 | 显示 `~7` 估算值（误导） |
| 合并策略 | `Csr()` 显式合并防闪烁/防无效覆盖 | 无合并，各源独立更新 |
| breakdown 粒度 | 11 段，带 chars + tokens + items 明细 | 7 段，只有估算 token |
| emit 时机 | 每次 LLM request 装配（phase=request） | 同左（llm_request 装配点） |

## 二、AgentNodes 现状代码定位

| 位置 | 现状 |
|---|---|
| `hooks/useContextUsage.ts` | measured(tokensUsed) > estimated(前端 chars/3.5) |
| `components/chat/ComposerStatusBar.tsx:54-59` | usage = useContextUsage(); breakdown = store.contextBreakdown 独立取 |
| `stores/chat.ts:187,423` | contextBreakdown: Map<sessionId, segments>，由 SSE handler 写入 |
| `agent_telemetry.py:323-341` | context.breakdown 在 llm_request 前 emit，带 segments + total |
| `contextUsage.ts:90-112` | buildContextUsage 决定 usedTokens / percent |
| `context_breakdown.py` | 后端 7 段归属规则，chars/3.0 估算 |

## 三、实施方案（对齐 ZCode 单一 usage 对象）

### 目标

- 状态栏数字与 hover 明细**同源**，不再出现 `~7` vs `2,374` 矛盾
- used 用**真实 LLM usage**（ZCode 做法），breakdown 只负责构成展示
- 空闲会话**不显示误导估算值**
- 合并策略防闪烁（SSE 事件缺 breakdown 时继承旧的）

### 步骤

#### H1 · 后端：无需改动（已核实）

`session_total_tokens.context_used` **已经**是 ZCode 的 used 语义
（`loop_events.py:103-108`：真实 API `prompt_tokens`，每次覆盖、非累计，
注释明确 "the size of the prompt actually sent to the model on the most recent LLM call"）。
该事件已通过 `apps/chat/events.py:65` 的 passthrough 推到前端，
`ComposerStatusBar` 已把它写入 `tokensUsed`（但语义当成了累计消耗）。

→ **不需要新增事件，也不需要拆字段**：前端 handler `controlHandlers.ts:12-23`
已正确地把 `context_used` 当"当前上下文占用"写入 `tokensUsed`（fallback 才是
total_tokens），注释明确区分 bounded occupancy vs cumulative spend。

**真机为何仍显示 `~7`**：`context_used` 来自 LLM usage 的 `prompt_tokens/input_tokens`，
若 provider（MiniMax）的 usage 响应不含这两个字段，`context_used=0` → 前端回落前端估算。
这与之前 C-1 排障的"tokensUsed 为空"是同一个根因，属于**数据源缺失**，不是前端口径问题。
→ 方案必须同时覆盖"provider 无 usage 字段"的现实：此时以 `context.breakdown.total`
作为 used（后端 chars 估算，可靠且总能产出），而不是前端消息文本估算。

#### H2 · 不合并 store（已核实不需要）

store 已有两个独立字段：`tokensUsed`（context_used 实测）与 `contextBreakdown`（7 段估算），
语义清晰，无需新增统一 usage 字段。ZCode 的 Csr() 合并策略针对的是其 SSE 事件流特性，
AgentNodes 的 SSE handler 已正确处理，跳过。

#### H3 · 双源交叉校验（实际实施方案）

用户最终要求：**两源都实现，实测存在时相互校验，没有时用 breakdown.total**。

在 `utils/contextUsage.ts` 新增纯函数 `reconcileUsage(measured, breakdownTotal, threshold=0.15)`：

| 场景 | 判定 | 采用值 |
|---|---|---|
| 两源都有，相对差异 ≤ 15% | `consistent` | 实测（更准） |
| 两源都有，相对差异 > 15% | `divergent` | breakdown.total（与 hover 明细同源，避免矛盾） |
| 只有实测 | `measured-only` | 实测 |
| 只有 breakdown | `breakdown-only` | breakdown.total |
| 都无 | `no-data` | 0 |

校验结论存在 `ContextUsageInfo.reconciliation: {kind, relativeGap, usedMeasured}`，
不影响既有 `measured`/`estimated` 二分（UI 兼容）。

#### H4 · 展示层（最小改动）

- `useContextUsage` 调 `buildContextUsage({measuredTokens, breakdownTotal, ...})`
- 展示文案由「字符估算」改为「后端估算」（breakdown 是后端 chars 估算，不是前端字符估算）
- 空闲 0%（用户裁决）；`~` 前缀保留表示"非 API 实测"

#### H5 · 测试

- `utils/contextUsage.ts` 的 `reconcileUsage`：一致/分歧/单源/无源
- `useContextUsage.test.ts`：9 用例覆盖 hook 集成（含 15% 阈值两侧、session 隔离）
- 真机：MiniMax 环境（无实测）显示 `~2.4K / 1.0M (0.2%)`，来源「后端估算」，与明细同源

### 风险与取舍

1. 15% 阈值是经验值（chars/3.0 估算 vs API 计费 token 的正常偏差范围），
   可在 `RECONCILE_GAP_THRESHOLD` 常量调整。
2. 分歧时选 breakdown 而非实测：牺牲精度换"状态栏与明细一致"，避免用户困惑。
   校验结论记录在 `reconciliation` 中，便于后续诊断。

## 四、结论与待裁决

调研已回答的问题：
- **不需要改后端**：`context.breakdown` 已有 `total`（7 段之和），`session_total_tokens.context_used`
  已是正确语义的 used；只是 MiniMax 不产出 prompt_tokens 导致实测路径为空。
- **不需要拆 store 字段**：`tokensUsed` 已存 context_used（单次占用），语义正确。
- **空闲估算 `~7` 应去掉**：ZCode 用 `len()` 守卫不显示，我们没有理由显示误导值。

用户裁决（2026-11-25）：
1. 两个来源的区别已解释；**两源都实现，实测存在时互相校验，无实测时用 breakdown.total**
2. 空闲显示 **0%**（不是 `—`）
3. BreakdownList 绝对 token 数**保留**（与状态主数字天然一致）

## 五、实施结果（已验证）

改动（前端 5 文件 + 2 测试）：
- `utils/contextUsage.ts`：新增 `reconcileUsage()` 纯函数（15% 阈值）+
  `UsageReconciliationInfo` 类型；`buildContextUsage` 接收 `breakdownTotal` 参数做双源校验。
- `hooks/useContextUsage.ts`：移除 `estimateTokensFromMessages` 兜底；
  传入 `breakdownTotal = Object.values(contextBreakdown).sum`，保留 `tokensUsed` 作为实测。
- `ComposerStatusBar.tsx` / `TokenCard.tsx`：文案「字符估算」→「后端估算」。
- `test/useContextUsage.test.ts`（8 用例）：一致（≤15%）用实测、分歧（>15%）用 breakdown、
  仅实测、仅 breakdown、无源 0、不再用消息文本估算、按 sessionId 隔离。

验证：
- 受影响测试 40 passed；全量前端 **1442 passed**；tsc 0 error；`npm run build` 成功。
- 真机（合并模式，MiniMax 无实测）：状态栏显示 `~2.4K / 1.0M (0.2%)`，
  来源「后端估算」；hover 明细 历史消息 2 + 系统提示 2,374 = 2,376，与状态栏同源。

未做（按裁决不需要）：后端改动、store 拆字段、BreakdownList 改展示。
