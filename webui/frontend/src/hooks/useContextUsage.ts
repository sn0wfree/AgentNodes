import { useMemo } from 'react'
import { useChatStore } from '../stores/chat'
import { useSystemStore } from '../stores/system'
import {
  buildContextUsage,
  type ContextUsageInfo,
} from '../utils/contextUsage'

/**
 * 上下文用量的**统一取数口**（R-014 F4-b）。
 *
 * 此前三处 UI 各自取数、各自算口径：
 * - `ContextUsageBar` 用 `useChatSessionId()` + 有估算兜底
 * - `ComposerStatusBar` 用 props 传入的 sessionId + **无**兜底 → 恒显 0%
 * - `TokenCard` 用 `useSessionStore.currentSessionId` + **无**兜底 → 恒显 0%
 *
 * 三处 sessionId 来源不同，在编排面板（`ChatSessionProvider` 传入 `dag:xxx`）
 * 下会各指各的会话。本 hook 要求**调用方显式传入 sessionId**，把「哪个会话」
 * 变成显式契约，避免再出现三个来源。
 *
 * **双源交叉校验**（2026-11-25）：
 *   - `tokensUsed`（= session_total_tokens.context_used，LLM 实测 prompt_tokens）
 *   - `context.breakdown` 的 7 段之和（后端Chars估算，与 hover 明细同源）
 *   两源都存在时按相对差异校验：≤15% 视为一致 → 用实测；>15% 分歧 → 用 breakdown
 *   （与明细同源，避免状态栏数字与明细矛盾）。单源 → 用该源；无源 → 0（显示 0%）。
 *   校验结论存在 `usage.reconciliation` 中供诊断。
 *
 * 不再用前端消息文本估算：它不含 system prompt / tools schema，样本偏小
 * （曾导致状态栏 `~7` 与明细 `2,374` 自相矛盾）。
 */
export function useContextUsage(sessionId: string | null): ContextUsageInfo {
  const measuredTokens = useChatStore((s) =>
    sessionId ? s.tokensUsed.get(sessionId) ?? 0 : 0,
  )
  // 7 段构成（后端 context.breakdown 事件）——各段之和是"当前上下文占用"的
  // 可靠来源：后端按完整 messages + tools schema 估算，总能产出。
  // 与 hover 弹层的 BreakdownList 同源，避免状态栏与明细数字矛盾。
  const breakdown = useChatStore((s) =>
    sessionId ? s.contextBreakdown.get(sessionId) : undefined,
  )
  const modelInfo = useSystemStore((s) => s.modelInfo)

  return useMemo(() => {
    // 主来源：7 段之和。真实 usage（context_used = LLM 报的 prompt_tokens）
    // 若可用则覆盖——它更准，但 MiniMax 等 provider 不产出该字段。
    const breakdownTotal = breakdown
      ? Object.values(breakdown).reduce((sum, v) => sum + (v || 0), 0)
      : 0
    return buildContextUsage({
      measuredTokens,
      estimatedTokens: 0, // 不再用前端消息文本估算——样本不含 system/tools，恒偏小
      breakdownTotal,
      limitTokens: modelInfo?.context_tokens ?? 0,
      outputLimitTokens: modelInfo?.max_output_tokens ?? 0,
      isStale: modelInfo?.source === 'fallback' || modelInfo?.source === 'bundled',
    })
  }, [measuredTokens, breakdown, modelInfo])
}
