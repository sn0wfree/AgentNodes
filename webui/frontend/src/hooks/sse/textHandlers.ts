import type { TextPart, ThinkingPart } from '../../stores/chat'
import type { SSEHandler } from './types'

/**
 * Text streaming events — opencode-style 3-step text protocol.
 *
 * `text.started` seeds a text part with a server-issued `text_id` and
 * marks it `isStreaming: true`. `text_delta` appends to the matching
 * part (hard-break if id missing) AND writes to the per-part preview
 * buffer `partTextAccumDelta[text_id]` so the very first character is
 * visible the instant it arrives. `text.ended` overrides with the
 * authoritative final text and clears the streaming flag + buffer.
 *
 * `findLast-by-id` keeps text routing correct when text and tool calls
 * interleave across LLM iterations.
 */
export const textStarted: SSEHandler = (data, { updateMessage }) => {
  const textId = data.text_id as string | undefined
  const messageId = data.message_id as string | undefined
  if (!textId || !messageId) return
  updateMessage(messageId, (msg) => {
    const existing = msg.parts.find(
      (p) => p.type === 'text' && (p as TextPart).id === textId,
    )
    if (!existing) {
      msg.parts.push({ type: 'text', id: textId, text: '', isStreaming: true })
    } else {
      // Re-mark on replay (e.g. late join after a reconnect).
      ;(existing as TextPart).isStreaming = true
    }
  })
}

export const textDelta: SSEHandler = (data, ctx) => {
  const { applyTextDelta } = ctx
  const text = (data.text || data.delta) as string
  const textId = data.text_id as string | undefined
  const messageId = data.message_id as string | undefined
  if (!text) return
  if (!textId) {
    // Protocol error: drop the canonical part (no id to route to), but
    // keep the legacy global stream updated — callers may still use it
    // for the streaming highlight (existing hard-break contract).
    console.warn('[useSSE] text_delta without text_id, dropping chunk')
    ctx.appendStreamingText(text)
    return
  }
  if (!messageId) return
  // P0-1: one store write per SSE chunk (canonical part text + preview
  // buffer + legacy global stream). Previously this was three separate
  // set() calls, each notifying every messages/partTextAccum subscriber.
  applyTextDelta(messageId, textId, text)
}

export const textEnded: SSEHandler = (data, ctx) => {
  // Guard: some protocol variants emit text.ended as a pure
  // end-signal without the final text — never wipe the
  // accumulated streaming content with an empty string (B4).
  const { updateMessage, clearPartAccum } = ctx
  const textId = data.text_id as string | undefined
  const finalText = (data.text || '') as string
  const messageId = data.message_id as string | undefined
  if (!textId || !messageId) return
  updateMessage(messageId, (msg) => {
    for (let i = msg.parts.length - 1; i >= 0; i--) {
      const p = msg.parts[i]
      if (p && p.type === 'text' && (p as TextPart).id === textId) {
        if (finalText) (p as TextPart).text = finalText
        ;(p as TextPart).isStreaming = false
        return
      }
    }
  })
  // Terminal state: drop the preview buffer so readPartText falls
  // back to the persistent text (and so the buffer doesn't grow
  // unbounded across LLM iterations).
  clearPartAccum(textId)
}

/**
 * Thinking events — append thinking tokens to the last thinking part.
 *
 * Mirrors the text protocol: `thinking_start` pushes a fresh
 * `isStreaming: true` part; `thinking_delta` appends + writes to the
 * preview buffer; `thinking_done` / `thinking_end` clear streaming
 * and collapse the part (UI auto-folds once the answer stream lands).
 */
export const thinkingStart: SSEHandler = (data, { updateMessage }) => {
  const messageId = data.message_id as string | undefined
  if (!messageId) return
  // Per-iteration think_id (mirrors the text protocol's text_id).
  const thinkId = data.think_id as string | undefined
  updateMessage(messageId, (msg) => {
    // Dedup: SSE replays re-deliver the same thinking_start — a block
    // with this id already exists, so don't push a duplicate.
    if (thinkId && msg.parts.some(
      (p) => p.type === 'thinking' && (p as ThinkingPart).id === thinkId,
    )) {
      return
    }
    msg.parts.push({
      type: 'thinking',
      text: '',
      collapsed: true,
      isStreaming: true,
      id: thinkId ?? `think-${messageId}-${msg.parts.length}`,
    } as ThinkingPart)
  })
}

export const thinkingDelta: SSEHandler = (data, ctx) => {
  const { updateMessage, accumulatePartText } = ctx
  const delta = data.delta as string
  const messageId = data.message_id as string | undefined
  if (!delta || !messageId) return
  // Route to the exact block via think_id; fall back to the last OPEN
  // (streaming) thinking block — never an already-closed one, which is
  // what "last thinking part" matched when blocks interleaved.
  const thinkId = data.think_id as string | undefined
  let partId: string | null = null
  updateMessage(messageId, (msg) => {
    for (let i = msg.parts.length - 1; i >= 0; i--) {
      const p = msg.parts[i]
      if (p && p.type === 'thinking') {
        const tp = p as ThinkingPart
        if (thinkId && tp.id && tp.id !== thinkId) continue
        if (!thinkId && tp.isStreaming === false) continue
        tp.text += delta
        tp.isStreaming = true
        partId = `think-${messageId}-${i}`
        return
      }
    }
  })
  if (partId) accumulatePartText(partId, delta)
}

export const thinkingDone: SSEHandler = (data, ctx) => {
  const { updateMessage, clearPartAccum } = ctx
  const messageId = data.message_id as string | undefined
  if (!messageId) return
  // Clear streaming flag + preview buffer for the matched thinking part.
  const thinkId = data.think_id as string | undefined
  let partId: string | null = null
  updateMessage(messageId, (msg) => {
    for (let i = msg.parts.length - 1; i >= 0; i--) {
      const p = msg.parts[i]
      if (p && p.type === 'thinking') {
        const tp = p as ThinkingPart
        // With think_id, only close the matching block; without, close
        // the last still-streaming one (replays may deliver these out
        // of order relative to interleaved blocks).
        if (thinkId && tp.id && tp.id !== thinkId) continue
        if (!thinkId && tp.isStreaming === false) continue
        tp.isStreaming = false
        tp.collapsed = true
        partId = `think-${messageId}-${i}`
        return
      }
    }
  })
  if (partId) clearPartAccum(partId)
}

export const thinkingEnd: SSEHandler = (data, ctx) => {
  const { updateMessage, clearPartAccum } = ctx
  const messageId = data.message_id as string | undefined
  if (!messageId) return
  const thinkId = data.think_id as string | undefined
  let partId: string | null = null
  updateMessage(messageId, (msg) => {
    for (let i = msg.parts.length - 1; i >= 0; i--) {
      const p = msg.parts[i]
      if (p && p.type === 'thinking') {
        const tp = p as ThinkingPart
        if (thinkId && tp.id && tp.id !== thinkId) continue
        tp.isStreaming = false
        tp.collapsed = true
        partId = `think-${messageId}-${i}`
        return
      }
    }
  })
  if (partId) clearPartAccum(partId)
}
