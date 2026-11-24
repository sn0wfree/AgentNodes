import { useMemo } from 'react'
import { useChatStore } from '../stores/chat'
import { useSystemStore } from '../stores/system'
import {
  buildContextUsage,
  estimateTokensFromMessages,
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
 */
export function useContextUsage(sessionId: string | null): ContextUsageInfo {
  const measuredTokens = useChatStore((s) =>
    sessionId ? s.tokensUsed.get(sessionId) ?? 0 : 0,
  )
  const messages = useChatStore((s) => s.messages)
  const modelInfo = useSystemStore((s) => s.modelInfo)

  return useMemo(() => {
    const estimatedTokens = estimateTokensFromMessages(messages)
    return buildContextUsage({
      measuredTokens,
      estimatedTokens,
      limitTokens: modelInfo?.context_tokens ?? 0,
      outputLimitTokens: modelInfo?.max_output_tokens ?? 0,
      isStale: modelInfo?.source === 'fallback' || modelInfo?.source === 'bundled',
    })
  }, [measuredTokens, messages, modelInfo])
}
