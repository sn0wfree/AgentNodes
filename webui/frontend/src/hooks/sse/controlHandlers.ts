import type { SSEHandler } from './types'

/**
 * session_total_tokens: backend authoritative figure for the current
 * attempt. Carries both cumulative spend (total_tokens) and the current
 * context-window occupancy (context_used = size of the prompt most
 * recently sent to the model). ContextUsageBar needs the BOUNDED
 * occupancy, not the cumulative spend — so we store context_used
 * (falling back to total_tokens defensively). Marks the session so
 * later llm_usage deltas are not applied on top (regression B2).
 */
export const sessionTotalTokens: SSEHandler = (data, ctx) => {
  const { sessionId, setTokensUsed, markTotalTokensSeen } = ctx
  const { context_used, total_tokens } = data as {
    context_used?: number
    total_tokens?: number
  }
  if (!sessionId) return
  const used = typeof context_used === 'number' ? context_used : total_tokens
  if (typeof used !== 'number') return
  setTokensUsed(sessionId, used)
  markTotalTokensSeen(sessionId)
}

/**
 * llm_usage: per-call usage delta. The backend accumulates spend into
 * session_total_tokens and re-emits it for every LLM call, so adding
 * here would double-count (regression B2). The current context
 * occupancy is "the prompt size of the most recent call" — an overwrite,
 * not an accumulate. Only act as a fallback when the authoritative
 * session_total_tokens was never seen for this session.
 */
export const llmUsage: SSEHandler = (data, ctx) => {
  const { sessionId, setTokensUsed, state } = ctx
  if (!sessionId || state.hasSeenTotalTokens(sessionId)) return
  const d = data as {
    prompt_tokens?: number
    input_tokens?: number
    total_tokens?: number
  }
  const used =
    d.prompt_tokens ?? d.input_tokens ?? d.total_tokens
  if (typeof used === 'number' && used > 0) {
    setTokensUsed(sessionId, used)
  }
}

/**
 * context.breakdown (R-014 H1): 7 段上下文构成估算。后端每次 LLM call
 * 发一次（agent_telemetry），前端存到 per-session store，供圆环 + hover
 * 明细使用。事件缺失时 UI 走本地估算兜底。
 */
export const contextBreakdown: SSEHandler = (data, ctx) => {
  const { sessionId, setContextBreakdown } = ctx
  if (!sessionId) return
  const segments = data.segments
  if (!segments || typeof segments !== 'object') return
  const clean: Record<string, number> = {}
  for (const [k, v] of Object.entries(segments)) {
    if (typeof v === 'number' && Number.isFinite(v)) clean[k] = v
  }
  if (Object.keys(clean).length === 0) return
  setContextBreakdown(sessionId, clean)
}

/**
 * compact: context compaction occurred. Increments the per-agent
 * compaction counter and surfaces a dismissible banner via
 * `setLastCompaction`.
 */
export const compact: SSEHandler = (data, ctx) => {
  const { updateAgent, setLastCompaction } = ctx
  const compactData = data as {
    agent_id?: string
    layer?: string
    iteration?: number
    summary?: string
  }
  if (compactData.agent_id) {
    updateAgent(compactData.agent_id, (agent: any) => {
      agent.compaction_count = (agent.compaction_count || 0) + 1
      agent.last_compaction = {
        layer: compactData.layer || 'unknown',
        timestamp: Date.now(),
      }
    })
  }
  setLastCompaction({
    layer: compactData.layer || 'unknown',
    timestamp: Date.now(),
  })
}

/** C4.2: compact.count — observability event for compact operations. */
export const compactCount: SSEHandler = (data) => {
  const { messages_before, messages_after, tokens_before, tokens_after } = data as {
    messages_before?: number
    messages_after?: number
    tokens_before?: number
    tokens_after?: number
  }
  if (messages_before != null && messages_after != null) {
    // Log for debugging — the banner already shows via compact.ended
    console.log(
      `[compact] ${messages_before} → ${messages_after} messages, ` +
      `${tokens_before ?? '?'} → ${tokens_after ?? '?'} tokens`,
    )
  }
}

/**
 * Defensive cleanup: clear `isStreaming` on every part of every
 * assistant message. The per-protocol handlers (text.ended /
 * thinking_done / tool_result) normally do this, but on disconnect,
 * cancel, or error the terminal event may never arrive — this keeps
 * the UI from showing stuck `running` spinners or perpetually
 * expanding thinking blocks.
 */
function clearAllStreamingParts(ctx: Parameters<SSEHandler>[1]): void {
  // Iterate the live messages and clear isStreaming on every part via
  // the store's `updateMessage` action (which runs inside immer's
  // `set` so the part object is a mutable draft). Direct assignment
  // throws TypeError because the part is a frozen object from immer's
  // auto-freeze — this function is the safety net for disconnect /
  // cancel / error paths where the terminal event never arrives.
  for (const msg of ctx.state.getMessages()) {
    if (msg.role !== 'assistant') continue
    let touched = false
    for (const part of msg.parts) {
      if ((part as { isStreaming?: boolean }).isStreaming) {
        ;(part as { isStreaming?: boolean }).isStreaming = false
        touched = true
      }
    }
    if (!touched) continue
    const id = msg.id
    ctx.updateMessage(id, (draft) => {
      for (const part of draft.parts) {
        ;(part as { isStreaming?: boolean }).isStreaming = false
      }
    })
  }
}

/** agent_done: AgentLoop finished — clear streaming state. */
export const agentDone: SSEHandler = (data, ctx) => {
  clearAllStreamingParts(ctx)
  ctx.setStreamingMessage(null)
  ctx.setActiveAttempt(null)
  // The orchestrator flags attempts that ended with a question to the
  // user (backend runs a continuation guard; this flag survives only
  // when the guard gave up). The orchestrator panel surfaces a
  // "keep going" action from it.
  if (ctx.sessionId) {
    ctx.setAskedUser(ctx.sessionId, Boolean((data as { asked_user?: boolean }).asked_user))
  }
}

/** error: backend surfaced a fatal error — toast + clear streaming. */
export const errorEvent: SSEHandler = (data, ctx) => {
  const error = data.error as string
  if (error) ctx.addToast('error', error)
  clearAllStreamingParts(ctx)
  ctx.setStreamingMessage(null)
  ctx.setActiveAttempt(null)
}