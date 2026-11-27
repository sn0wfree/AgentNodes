import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import { enableMapSet } from 'immer'
import { api } from '../api/client'
import { shouldInsertSpaceBetween } from '../utils/mastraSmoothStream'
import { useSessionStore } from './session'

// Required for immer to handle Map/Set types in chat state
enableMapSet()

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool' | 'compaction'

export type MessageType = 'user' | 'assistant' | 'tool' | 'compaction' | 'error' | 'goal'

export interface TextPart {
  type: 'text'
  id: string
  text: string
  /** True while the part is still receiving text_delta events. */
  isStreaming?: boolean
}

export interface ToolCallPart {
  type: 'tool_call'
  id: string
  name: string
  arguments: string | unknown
  result?: string | unknown
  status: 'pending' | 'running' | 'done' | 'error'
  progress?: string[]
  /** True while the tool is still being awaited (tool_call → tool_result). */
  isStreaming?: boolean
}

export interface ThinkingPart {
  type: 'thinking'
  text: string
  collapsed?: boolean
  /** True while the reasoning block is still receiving thinking_delta events. */
  isStreaming?: boolean
  /** Per-iteration id from the backend's thinking protocol (think_id).
   * Routes deltas to the exact block and dedupes SSE replays — text
   * parts have the same via text_id. */
  id?: string
}

export interface FileEditPart {
  type: 'file_edit'
  file_path: string
  old_content: string
  new_content: string
}

export interface TablePart {
  type: 'table'
  headers: string[]
  rows: string[][]
  caption?: string
}

export interface ChartPart {
  type: 'chart'
  chart_type: 'bar' | 'line' | 'pie' | 'scatter'
  data: unknown[]
  title?: string
}

export interface ImagePart {
  type: 'image'
  url: string
  alt?: string
}

export interface HtmlPart {
  type: 'html'
  title?: string
  /** Self-contained HTML content (rendered in a sandboxed iframe). */
  content: string
  isStreaming?: boolean
}

export interface AgentPart {
  type: 'agent'
  id: string
  agentId: string
  name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  toolCalls: ToolCallPart[]
  streamingText: string
  startedAt: number
  finishedAt?: number
  tokensUsed?: number
  error?: string
  isStreaming?: boolean
}

export type MessagePart = TextPart | ToolCallPart | ThinkingPart | FileEditPart | TablePart | ChartPart | ImagePart | HtmlPart | AgentPart

export interface Message {
  id: string
  session_id: string
  role: MessageRole
  agent_id?: string
  parts: MessagePart[]
  created_at: number
  message_type?: MessageType
  metadata?: {
    model?: string
    tokens_used?: number
    iteration?: number
    queue_position?: number
    queue_length?: number
    queue_status?: 'processing' | 'queued'
    status?: string
    details?: string
    error?: string
    /** L2 claim validation result (truthfulness). Populated by the
     *  backend when claim validation is enabled; drives the
     *  🟢/🟡/🔴 verifiability badge in AssistantMessage. */
    claim_validation?: {
      ok: boolean
      total_claims: number
      verified: string[]
      unverified: string[]
      confidence: number
      detail: string
    }
    /** Goal state snapshot (message_type='goal'). Populated by the
     *  goal_updated SSE handler / projector; drives the GoalMessage
     *  card in the chat stream (docs/goal-events-panel-link.md). */
    goal_id?: string
    change_type?: 'create' | 'evidence' | 'complete' | string
    objective?: string
    progress_percent?: number
    goal_status?: string
    criteria?: Array<{
      criterion_id: string
      text: string
      status: string
      evidence_count: number
    }>
    evidence_count?: number
    evidence_text?: string
    recap?: string
    /** Study chat: message category for filtering/display. */
    kind?: 'agent' | 'directive' | 'chat' | 'system' | 'round-separator'
    /** Study chat: round number this message belongs to. */
    round?: number
  }
}

interface ChatState {
  messages: Map<string, Message>
  streamingMessageId: string | null
  streamingText: string
  activeAttemptId: string | null
  /**
   * Tier 1 A1: most recent pending permission_request from the
   * backend. The PermissionRequestDialog subscribes to this and
   * posts the verdict via /api/chat/permission/respond.
   * Single-slot — concurrent requests are unusual because the
   * AgentLoop is sequential within one attempt.
   */
  pendingPermission: import('../hooks/sse/permissionHandlers').PermissionRequest | null
  /** Per-session flag: true when the consumer queue was paused after cancel. */
  queuePaused: Map<string, boolean>
  /**
   * Per-session flag: true when the last finished attempt ended with the
   * agent asking the user a question instead of completing the task
   * (backend agent_done.asked_user). The DAG orchestrator surfaces a
   * "continue" action from this.
   */
  askedUserSessions: Map<string, boolean>
  setAskedUser: (sessionId: string, asked: boolean) => void
  /** Per-session current queue length snapshot. */
  queueLengths: Map<string, number>
  /**
   * Per-session current context-window occupancy (bounded token count,
   * from session_total_tokens.context_used). Drives ContextUsageBar.
   * Reflects compaction: drops after the context is compressed.
   */
  tokensUsed: Map<string, number>
  /**
   * R-014 H1：per-session 7 段上下文构成（来自 context.breakdown SSE 事件）。
   * 供圆环 + hover 明细使用；无数据时为空对象，UI 走估算兜底。
   */
  contextBreakdown: Map<string, Record<string, number>>
  setContextBreakdown: (sessionId: string, segments: Record<string, number>) => void
  /**
   * Per-session flag: true once `session_total_tokens` has been seen.
   * `session_total_tokens` is the backend-authoritative value; per-call
   * `llm_usage` deltas must NOT be applied on top of it (double
   * counting). `llm_usage` only acts as a fallback for sessions that
   * never saw the authoritative event.
   */
  totalTokensSeen: Map<string, boolean>
  /** Most recent compaction event (for CompactBanner). */
  lastCompaction: { layer: string; timestamp: number } | null
  setLastCompaction: (c: { layer: string; timestamp: number } | null) => void
  /** C3: latest trace_id from SSE events (for log correlation). */
  traceId: string | null
  setTraceId: (id: string | null) => void
  /**
   * Per-part streaming text preview buffer.
   *
   * Modeled after opencode's `part_text_accum_delta`: every text_delta
   * / thinking_delta / tool.input_delta event lands here keyed by
   * partId, on top of the persistent part.text update. `readPartText`
   * prefers the accum buffer (so the first character of a new part
   * becomes visible the instant it arrives) and falls back to the
   * persistent text once the part is sealed (text.ended /
   * thinking_end / tool_result).
   *
   * Cleared by `clearPartAccum` when the corresponding part transitions
   * to a terminal state, and wholesale by `clearMessages` /
   * `setMessages`.
   */
  partTextAccumDelta: Record<string, string>
  accumulatePartText: (partId: string, delta: string) => void
  clearPartAccum: (partId: string) => void
  clearAllPartAccum: () => void
  setMessages: (messages: Message[]) => void
  addMessage: (message: Message) => void
  removeMessage: (id: string) => void
  updateMessage: (id: string, updater: (msg: Message) => void) => void
  setStreamingMessage: (id: string | null) => void
  setStreamingText: (text: string) => void
  appendStreamingText: (delta: string) => void
  setActiveAttempt: (attemptId: string | null) => void
  cancelAttempt: () => Promise<void>
  resumeQueue: () => Promise<void>
  setQueuePaused: (sessionId: string, paused: boolean) => void
  setQueueLength: (sessionId: string, length: number) => void
  setTokensUsed: (sessionId: string, tokens: number) => void
  markTotalTokensSeen: (sessionId: string) => void
  /** Per-session flag: older messages exist beyond the loaded window. */
  hasMore: Map<string, boolean>
  setHasMore: (sessionId: string, more: boolean) => void
  /** Load the next older page (before the earliest loaded message). */
  loadMoreMessages: (sessionId: string) => Promise<void>
  loadMessages: (sessionId: string) => Promise<void>
  /**
   * Reload recovery: rebuild streaming/queued state after a page
   * reload from GET /api/chat/attempts (see
   * docs/streaming-reload-recovery.md). Called at the end of
   * loadMessages; SSE replay alone cannot restore it because
   * `attempt.started` / `message_received` sit before the
   * Last-Event-ID cursor.
   */
  fetchSessionAttempts: (sessionId: string) => Promise<void>
  clearMessages: () => void
}

// Bump on every loadMessages call so stale responses are dropped
// (rapid session switching must not let an old response overwrite
// the currently viewed session's messages).
let loadMessagesSeq = 0

// ── F6: SSE-synthetic vs DB-message twin dedup ──────────────────────
// Live study/agent events are injected locally with synthetic ids
// (sse:/agent:/skip:); the projector also materializes the same events
// as DB messages. loadMessages replaces the map so twins can't
// accumulate there, but loadMoreMessages APPENDS the older DB window —
// without dedup the boundary page coexists with its synthetic twin.

const SYNTHETIC_ID_PREFIXES = ['sse:', 'agent:', 'skip:']
const TWINS_TIME_TOLERANCE_S = 2

function syntheticSignature(m: Message): string | null {
  const first = m.parts.find((p) => p.type === 'text') as TextPart | undefined
  if (!first || !first.text) return null
  return first.text.slice(0, 40)
}

function dedupeSyntheticTwins(
  state: { messages: Map<string, Message> },
  sessionId: string,
  loaded: Message[],
): void {
  if (loaded.length === 0) return
  const loadedSigs = loaded.map((m) => ({
    at: m.created_at,
    sig: syntheticSignature(m),
  }))
  for (const [id, m] of Array.from(state.messages)) {
    if (m.session_id !== sessionId) continue
    if (!SYNTHETIC_ID_PREFIXES.some((p) => id.startsWith(p))) continue
    const sig = syntheticSignature(m)
    if (sig === null) continue
    const hasTwin = loadedSigs.some(
      (l) =>
        l.sig === sig &&
        Math.abs(l.at - m.created_at) <= TWINS_TIME_TOLERANCE_S,
    )
    if (hasTwin) state.messages.delete(id)
  }
}

export const useChatStore = create<ChatState>()(
  immer((set, get) => ({
    messages: new Map(),
    streamingMessageId: null,
    streamingText: '',
    activeAttemptId: null,
    queuePaused: new Map(),
    askedUserSessions: new Map(),
    queueLengths: new Map(),
    tokensUsed: new Map(),
    contextBreakdown: new Map(),
    totalTokensSeen: new Map(),
    lastCompaction: null,
    partTextAccumDelta: {},
    pendingPermission: null,
    traceId: null,
    setLastCompaction: (c) => set({ lastCompaction: c }),
    setTraceId: (id) => set({ traceId: id }),
    accumulatePartText: (partId, delta) =>
      set((state) => {
        if (!delta) return
        const prev = state.partTextAccumDelta[partId] ?? ''
        // Chunk-boundary space recovery — see
        // ``shouldInsertSpaceBetween`` in utils/mastraSmoothStream.
        // DeepSeek-V4-Flash streams one BPE token per SSE chunk and
        // drops the leading space of each. We re-insert one here so
        // ``partTextAccumDelta`` carries the words the user expects
        // to see. The persisted backend ``part.text`` is *not* touched
        // — it stays raw.
        state.partTextAccumDelta[partId] = shouldInsertSpaceBetween(prev, delta)
          ? prev + ' ' + delta
          : prev + delta
      }),
    clearPartAccum: (partId) =>
      set((state) => {
        if (partId in state.partTextAccumDelta) {
          delete state.partTextAccumDelta[partId]
        }
      }),
    clearAllPartAccum: () =>
      set((state) => {
        state.partTextAccumDelta = {}
      }),
    setMessages: (messages) =>
      set((state) => {
        state.messages.clear()
        messages.forEach((m) => state.messages.set(m.id, m))
        state.partTextAccumDelta = {}
      }),
    addMessage: (message) =>
      set((state) => {
        state.messages.set(message.id, message)
      }),
    removeMessage: (id) =>
      set((state) => {
        state.messages.delete(id)
      }),
    updateMessage: (id, updater) =>
      set((state) => {
        const msg = state.messages.get(id)
        if (msg) updater(msg)
      }),
    setStreamingMessage: (id) => set({ streamingMessageId: id }),
    setStreamingText: (text) => set({ streamingText: text }),
    appendStreamingText: (delta) =>
      set((state) => {
        state.streamingText += delta
      }),
    setActiveAttempt: (attemptId) => set({ activeAttemptId: attemptId }),
    setAskedUser: (sessionId, asked) =>
      set((state) => {
        const next = new Map(state.askedUserSessions)
        next.set(sessionId, asked)
        return { askedUserSessions: next }
      }),
    cancelAttempt: async (sessionId?: string) => {
      const { activeAttemptId } = get()
      const sid =
        sessionId ?? useSessionStore.getState().currentSessionId
      if (!sid) return

      try {
        await api.post('/chat/cancel', {
          session_id: sid,
          attempt_id: activeAttemptId,
        })
      } catch (err) {
        console.error('Cancel failed:', err)
      }

      // Clear streaming state
      set((state) => {
        state.streamingMessageId = null
        state.streamingText = ''
        state.activeAttemptId = null
      })
    },
    resumeQueue: async (sessionId?: string) => {
      const sid =
        sessionId ?? useSessionStore.getState().currentSessionId
      if (!sid) return
      try {
        await api.post('/chat/queue/resume', { session_id: sid })
        set((state) => {
          state.queuePaused.set(sid, false)
        })
      } catch (err) {
        console.error('Resume queue failed:', err)
      }
    },
    setQueuePaused: (sessionId, paused) =>
      set((state) => {
        state.queuePaused.set(sessionId, paused)
      }),
    setQueueLength: (sessionId, length) =>
      set((state) => {
        state.queueLengths.set(sessionId, length)
      }),
    setTokensUsed: (sessionId, tokens) =>
      set((state) => {
        state.tokensUsed.set(sessionId, tokens)
      }),
    setContextBreakdown: (sessionId, segments) =>
      set((state) => {
        state.contextBreakdown.set(sessionId, segments)
      }),
    markTotalTokensSeen: (sessionId) =>
      set((state) => {
        state.totalTokensSeen.set(sessionId, true)
      }),
    hasMore: new Map(),
    setHasMore: (sessionId, more) =>
      set((state) => {
        state.hasMore.set(sessionId, more)
      }),
    clearMessages: () =>
      set((state) => {
        state.messages.clear()
        state.streamingMessageId = null
        state.streamingText = ''
        state.activeAttemptId = null
        state.queuePaused.clear()
        state.queueLengths.clear()
        state.tokensUsed.clear()
        state.totalTokensSeen.clear()
        state.hasMore.clear()
        state.partTextAccumDelta = {}
      }),
    loadMessages: async (sessionId: string) => {
      const seq = ++loadMessagesSeq
      try {
        const data = await api.get<{ messages: Message[]; has_more?: boolean }>(
          `/chat/session/${sessionId}/messages?limit=200`
        )
        if (seq !== loadMessagesSeq) return // stale response (session switched)
        set((state) => {
          state.messages.clear()
          data.messages.forEach((m) => state.messages.set(m.id, m))
          state.hasMore.set(sessionId, !!data.has_more)
          // History load brings persisted parts back; any preview buffer
          // from a previous live stream is now stale.
          state.partTextAccumDelta = {}
        })
        // Reload recovery: re-attach streaming/queued state for any
        // in-flight attempt (see fetchSessionAttempts docstring).
        await useChatStore.getState().fetchSessionAttempts(sessionId)
      } catch (err) {
        console.error('loadMessages error:', err)
      }
    },
    fetchSessionAttempts: async (sessionId: string) => {
      const seq = loadMessagesSeq
      try {
        const data = await api.get<{
          attempts: {
            attempt_id: string
            message_id: string
            status: 'running' | 'queued' | 'failed'
            prompt: string
            created_at: string
            error?: string
          }[]
        }>(`/chat/attempts?session_id=${sessionId}`)
        if (seq !== loadMessagesSeq) return // stale response (session switched)
        const attempts = data.attempts ?? []
        set((state) => {
          // §9 复核修复（过时错误反复重现）：
          // failed attempt 只在「它是本 session 最新一轮」时才显示错误气泡。
          // 判定：该失败对应的消息之后是否还有更晚的消息 —— 若用户已继续对话，
          // 说明这次失败早已过时，不该在每次切回会话时重新弹出。
          // （该失败在 DB 里本来就没有 error 消息，纯前端合成产物。）
          const newestAt = state.messages.size
            ? Math.max(...[...state.messages.values()].map((m) => m.created_at ?? 0))
            : 0
          attempts.forEach((a, i) => {
            const mid = a.message_id
            if (!mid) return
            const createdAt = a.created_at ? Date.parse(a.created_at) / 1000 : Date.now() / 1000
            if (a.status === 'running') {
              // The message may not be materialized yet (reload inside
              // the first iteration, before the iter_start flush) —
              // create a placeholder so the streaming indicator has a
              // message to attach to. Later flush/SSE events take over.
              if (!state.messages.has(mid)) {
                state.messages.set(mid, {
                  id: mid,
                  session_id: sessionId,
                  role: 'assistant',
                  parts: [{ type: 'text', id: `seed-${mid}`, text: '' }],
                  created_at: createdAt,
                })
              }
              state.activeAttemptId = a.attempt_id
              state.streamingMessageId = mid
            } else if (a.status === 'failed' && a.error) {
              // C1: show failed attempt as an error message bubble —
              // 但仅限**最新**失败；更早的失败已被后续对话覆盖（过时）。
              const isLatest = createdAt >= newestAt
              if (isLatest && !state.messages.has(mid)) {
                state.messages.set(mid, {
                  id: mid,
                  session_id: sessionId,
                  role: 'assistant',
                  parts: [{ type: 'text', id: `err-${mid}`, text: a.error }],
                  created_at: createdAt,
                  metadata: { status: 'error', error: a.error },
                })
              }
            } else {
              // Queued: rebuild the in-memory placeholder (it is never
              // persisted; projector only materializes user messages).
              const meta = {
                queue_status: 'queued' as const,
                queue_position: i + 1,
                queue_length: attempts.length,
              }
              const existing = state.messages.get(mid)
              if (existing) {
                existing.metadata = { ...(existing.metadata ?? {}), ...meta }
              } else {
                state.messages.set(mid, {
                  id: mid,
                  session_id: sessionId,
                  role: 'assistant',
                  parts: [{ type: 'text', id: `seed-${mid}`, text: '' }],
                  created_at: createdAt,
                  metadata: meta,
                })
              }
            }
          })
        })
      } catch (err) {
        // Degrade gracefully: no streaming indicator (same as before
        // this feature); live SSE events take over on the next delta.
        console.error('fetchSessionAttempts error:', err)
      }
    },
    loadMoreMessages: async (sessionId: string) => {
      const { messages } = useChatStore.getState()
      // Find the earliest loaded message (oldest created_at) as cursor
      let earliest: Message | null = null
      for (const m of messages.values()) {
        if (m.session_id !== sessionId) continue
        if (!earliest || m.created_at < earliest.created_at) earliest = m
      }
      if (!earliest) return
      try {
        const data = await api.get<{ messages: Message[]; has_more?: boolean }>(
          `/chat/session/${sessionId}/messages?limit=200&before=${earliest.created_at}`
        )
        set((state) => {
          data.messages.forEach((m) => state.messages.set(m.id, m))
          state.hasMore.set(sessionId, !!data.has_more)
          // Drop synthetic live messages that this older DB page now
          // covers (same event injected twice would render twice).
          dedupeSyntheticTwins(state, sessionId, data.messages)
        })
      } catch (err) {
        console.error('loadMoreMessages error:', err)
      }
    },
  }))
)
