# ContextUsageBar token 估算修复（累计消耗 → 当前占用）

## 问题报告

截图显示 `2.3M / 131.1K (100.0%)`：

- **分母 131.1K 正确**：SiliconFlow 模型 `context_tokens = 131072`（128K 窗口）。
- **分子 2.3M 错误**：这是会话**累计消耗**，不是**当前 context 占用**。

一次长 agent 任务（多轮工具调用，每轮把全部历史重发给 LLM）累计消耗轻松
超过 2.3M，远超 128K 窗口 → 百分比被 clamp 到 100%，进度条恒为红色，
不反映真实 context 使用情况。

## 根因分析

### 语义错配（双层）

**第 1 层 — 消耗量 vs 占用量**

`ContextUsageBar` 用 `tokensUsed` 计算百分比，但该值来自
`session_total_tokens` 事件，携带的是 **attempt 累计 LLM 消耗**
（`service.py:667` 每次 attempt 重置 `usage_state`，逐次累加
`input_tokens + output_tokens`）。这是计费/预算语义，单调递增，
与"context 窗口占用"无关。

类比：拿"今天总共写了多少字"除以"一页纸能写多少字"，恒爆表。

**第 2 层 — compact 影响缺失**

正确的 context 占用必须能反映 compact（上下文压缩）后的回落。
数据源有两个候选：

1. **LLM usage 的 `prompt_tokens`**：流式末尾 usage chunk 携带本次调用
   发送给模型的完整 context 大小。compact 后 messages 变小 →
   下一次 `prompt_tokens` 回落。parser.py:294 已完整保留该字段，
   但 `service.py:700` 只把它当作 input 去**累加消耗**，丢弃了
   "最近一次真实占用"。
2. **后端 `estimate_tokens(messages)`**（`token_utils.py:15`）：loop 每次
   迭代都计算当前 messages 的估算 token 数，精确反映 compact 后回落，
   但 `iter_start`/`iter_end` 事件未把 tokens 字段发往前端。

选用 **prompt_tokens** 作为主数据源：它是模型自身报告的、无需额外估算、
compact 后自动回落。

## 修复设计

### 后端 — `src/strategy_research/api/session/service.py`

`event_callback` 内 `llm_usage` 分支（~695-725）：

1. `usage_state` 增加 `"context_used": 0`。
2. `llm_usage` 处理时（锁内）用**覆盖**而非累加记录最近一次真实占用：

   ```python
   prompt_used = int(data.get("prompt_tokens") or data.get("input_tokens") or 0)
   if prompt_used > 0:
       usage_state["context_used"] = prompt_used
   ```

3. `session_total_tokens` 事件 payload 增加 `"context_used"` 字段，
   保留 `input_tokens`/`output_tokens`/`total_tokens`（冗余无害，
   projector 测试不检查 payload）。

语义：`context_used` = 最近一次 LLM 调用发送的 prompt 大小 = 当前
context 窗口占用。有界（≤ 窗口，超限触发 compact），compact 后回落。

### 前端

- `controlHandlers.ts` `sessionTotalTokens`：读取 `data.context_used`
  （有界值）写入 `tokensUsed`；无该字段时回退 `total_tokens`（防御）。
- `llmUsage` fallback：从累加语义改为**覆盖**为
  `prompt_tokens ?? input_tokens`（与后端新语义一致——占用是"最近一次"，
  不是累加）；保留 `hasSeenTotalTokens` 防双计标记。
- `chat.ts`：更新 `tokensUsed` / `totalTokensSeen` 字段注释。
- `ContextUsageBar.tsx`：逻辑不变（`tokensUsed` 现在有界，百分比自然
  正确）；`estimatedTokens` 字符估算 fallback 保留（页面加载后 SSE 前）。

## 测试

- `useSSE.test.ts` 3 个 token 测试：累加 → 覆盖语义；context_used 字段。
- `ContextUsageBar.test.tsx`：`2.5M / 1.0M` 用例改为有界场景；新增
  compact 回落用例（大 context_used → compact 事件 → 小 context_used，
  断言显示回落 + 颜色变化）。

## 数据流（修复后）

```
LLM 调用完成
  → AgentLoop emit llm_usage (usage 含 prompt_tokens)
  → SessionService: context_used = prompt_tokens（覆盖）
  → emit session_total_tokens { context_used, total_tokens, ... }
  → 前端 sessionTotalTokens: tokensUsed = context_used
  → ContextUsageBar: pct = context_used / context_tokens
  → compact 后: messages 变小 → 下次 prompt_tokens 回落 → 百分比下降
```

## 不做的事

- 不改 `total_tokens` 累计逻辑（消耗统计保留，仍发出，供审计/冗余）。
- 不做旧后端兼容（前后端一起改）。
