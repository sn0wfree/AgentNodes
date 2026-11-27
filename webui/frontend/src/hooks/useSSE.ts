import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../stores/chat'
import { useAgentStore } from '../stores/agents'
import { useWorkflowStore } from '../stores/workflow'
import { useGoalStore } from '../stores/goal'
import { useToastStore } from '../stores/toast'
import { useSSEStore } from '../stores/sse'
import { useSessionStore } from '../stores/session'
import { useAuthStore } from '../stores/auth'
import { EVENT_TYPES, HANDLERS, type SSEContext, type SSEEventType } from './sse'

/**
 * Subscribe to the session's SSE event stream (/api/chat/events).
 *
 * Contract with the backend (api/session/service.py + routers/chat.py):
 * - One EventSource per session; the browser reconnects natively and
 *   resends `Last-Event-ID`, and the backend replays buffered events
 *   from that id (sse_buffer).
 * - Events carry `{ event_type, message_id, session_id, ... }` where
 *   `message_id` is the ATTEMPT's assistant message id (added by
 *   service.event_callback to every event's data).
 * - The streaming state machine is: `message_received` (user message
 *   echo + queued assistant placeholder, `queue_status: queued` if
 *   behind other attempts) → `attempt.started` (queue consumer picks
 *   up the attempt; frontend switches `streamingMessageId`) →
 *   `text.started` / `text_delta` / `text.ended` (+ tool/thinking
 *   events) → `assistant_message` (final content) → `agent_done`
 *   (streaming cleared). `attempt.completed` carries token usage.
 * - Queue control: `queue_paused` (after an explicit cancel; UI shows
 *   QueuePauseBanner until `resume_queue`), `queue_state` snapshots.
 * - Token accounting: `session_total_tokens` is authoritative (sets
 *   the cumulative); `llm_usage` deltas are only a fallback.
 *
 * State that must survive a reload (agents, DAG, goal panels) is
 * intentionally NOT rebuilt here — see the TODO on the goal handlers.
 *
 * Implementation: each event type is dispatched to a handler in
 * hooks/sse/handlers.ts (split from the original 650-line inline
 * switch). The handlers receive a stable `SSEContext` object bundling
 * the store mutators + read-through state helpers — this keeps the
 * deps array trivial (single context identity) while preserving the
 * exact behavior of the inline switch.
 */
export function useSSE(sessionId: string | null) {
  const sourceRef = useRef<EventSource | null>(null)
  const isFirstConnect = useRef(true)
  const prevSessionId = useRef<string | null>(null)
  const lastEventId = useRef('')
  // §9 复核：已提示过的错误键（message_id / attempt_id + 错误摘要）。
  // SSE 断线重连会回放历史事件，历史 error 不该在每次切回时重新弹 toast。
  // 按 session 隔离——切换会话时清空，新会话里的新错误仍会提示。
  const seenErrorKeys = useRef<Set<string>>(new Set())
  // P0-2: event-level idempotency. Backend emits `id: <event.id>` on every
  // SSE frame (chat.py::_format_event_v2); on reconnect the server replays
  // from Last-Event-ID, which can re-deliver already-applied events.
  // Mark-before-dispatch so handlers can't double-apply a re-entrant event.
  // Bounded to avoid unbounded growth in long-running sessions.
  const seenEventIds = useRef<Set<string>>(new Set())
  const MAX_SEEN_EVENT_IDS = 5000
  // Consecutive rejected connections (e.g. 403 — session not owned by
  // this user) cap the reconnect loop; the browser reports CLOSED for
  // non-2xx responses and we would otherwise retry forever, hammering
  // the backend with 403s every second.
  const failedAttempts = useRef(0)
  const MAX_FAILED_ATTEMPTS = 3
  // Pending reconnect timer — cleared on unmount so a delayed connect()
  // cannot create an orphan EventSource after the component is gone.
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Last-alive timestamp for the liveness watchdog (P0-5): refreshed by
  // onopen, any dispatched event, and the named heartbeat. The backend
  // pings every 15s, so >50s of silence while "connected" means the
  // connection is a zombie (e.g. backend died behind a proxy that keeps
  // the TCP half open) and the pill must flip to 已断开 honestly.
  const lastAliveRef = useRef<number>(Date.now())
  // 看门狗最后一次重连发起时刻（挂载时刻起算）—— disconnected 分支用
  // ≥10s 间隔防打爆。
  const lastReconnectRef = useRef<number>(Date.now())

  const addMessage = useChatStore((s) => s.addMessage)
  const updateMessage = useChatStore((s) => s.updateMessage)
  const setStreamingMessage = useChatStore((s) => s.setStreamingMessage)
  const setStreamingText = useChatStore((s) => s.setStreamingText)
  const appendStreamingText = useChatStore((s) => s.appendStreamingText)
  const accumulatePartText = useChatStore((s) => s.accumulatePartText)
  const clearPartAccum = useChatStore((s) => s.clearPartAccum)
  const setQueuePaused = useChatStore((s) => s.setQueuePaused)
  const setQueueLength = useChatStore((s) => s.setQueueLength)
  const setTokensUsed = useChatStore((s) => s.setTokensUsed)
  const setContextBreakdown = useChatStore((s) => s.setContextBreakdown)
  const markTotalTokensSeen = useChatStore((s) => s.markTotalTokensSeen)
  const applyTextDelta = useChatStore((s) => s.applyTextDelta)
  const updateAgent = useAgentStore((s) => s.updateAgent)
  const updateNodeStatus = useWorkflowStore((s) => s.updateNodeStatus)
  const addToast = useToastStore((s) => s.addToast)

  const handleEvent = useCallback(
    (e: MessageEvent) => {
      lastAliveRef.current = Date.now()
      const event = e.type as SSEEventType
      let data: Record<string, unknown>
      try {
        data = JSON.parse(e.data)
      } catch {
        return
      }

      // P0-2: drop replayed events by SSE id. Events without an id
      // (heartbeat / legacy frames) bypass dedupe and keep prior behavior.
      const eventId = e.lastEventId
      if (eventId) {
        if (seenEventIds.current.has(eventId)) return
        seenEventIds.current.add(eventId)
        if (seenEventIds.current.size > MAX_SEEN_EVENT_IDS) {
          const oldest = seenEventIds.current.values().next().value
          if (oldest !== undefined) seenEventIds.current.delete(oldest)
        }
      }

      // Pull read-through state snapshots at dispatch time so handlers
      // don't need direct store imports (keeps them unit-testable and
      // free of cross-store init-order concerns). Each helper reads the
      // current store state lazily — exactly matching the original
      // inline `useChatStore.getState().X.get(Y)` lookups.
      const ctx: SSEContext = {
        sessionId,
        addMessage,
        updateMessage,
        applyTextDelta,
        setStreamingMessage,
        setStreamingText,
        appendStreamingText,
        setQueuePaused,
        setQueueLength,
        setTokensUsed,
        setContextBreakdown,
        markTotalTokensSeen,
        setActiveAttempt: (id) => useChatStore.getState().setActiveAttempt(id),
        setAskedUser: (sid, asked) => useChatStore.getState().setAskedUser(sid, asked),
        setLastCompaction: (c) => useChatStore.getState().setLastCompaction(c),
        accumulatePartText,
        clearPartAccum,
        state: {
          getMessage: (id) => useChatStore.getState().messages.get(id),
          getMessages: () => useChatStore.getState().messages.values(),
          isQueuePaused: (sid) => !!useChatStore.getState().queuePaused.get(sid),
          hasSeenTotalTokens: (sid) => !!useChatStore.getState().totalTokensSeen.get(sid),
          getTokensUsed: (sid) => useChatStore.getState().tokensUsed.get(sid) ?? 0,
        },
        updateAgent,
        updateNodeStatus,
        setExecutionProgress: (p) => useWorkflowStore.getState().setExecutionProgress(p),
        setGoal: (g) => useGoalStore.getState().setGoal(g),
        addToast,
        addErrorToastOnce: (key, msg) => {
          if (seenErrorKeys.current.has(key)) return false
          seenErrorKeys.current.add(key)
          addToast('error', msg)
          return true
        },
        patchSessionMeta: (sid, patch) =>
          useSessionStore.setState((state) => ({
            sessions: state.sessions.map((sess) =>
              sess.id === sid ? { ...sess, ...patch } : sess,
            ),
          })),
        // Tier 1 A1: permission handshake. Both setters write to
        // the chat store which exposes ``pendingPermission`` so the
        // PermissionRequestDialog can subscribe.
        setPendingPermission: (req) => {
          useChatStore.setState({ pendingPermission: req })
        },
        clearPendingPermission: (toolCallId) => {
          useChatStore.setState((state) =>
            state.pendingPermission?.tool_call_id === toolCallId
              ? { pendingPermission: null }
              : {},
          )
        },
      }

      const handler = HANDLERS[event]
      if (handler) handler(data, ctx)
      if (eventId) lastEventId.current = eventId

      // C3: store trace_id from any SSE event for log correlation
      const tid = data?.trace_id as string | undefined
      if (tid && typeof tid === 'string') {
        useChatStore.getState().setTraceId(tid)
      }
    },
    [
      sessionId,
      addMessage,
      updateMessage,
      applyTextDelta,
      setStreamingMessage,
      setStreamingText,
      appendStreamingText,
      accumulatePartText,
      clearPartAccum,
      setQueuePaused,
      setQueueLength,
      setTokensUsed,
      setContextBreakdown,
      markTotalTokensSeen,
      updateAgent,
      updateNodeStatus,
      addToast,
    ],
  )

  const connect = useCallback(() => {
    if (!sessionId) {
      prevSessionId.current = null
      lastEventId.current = ''
      seenEventIds.current.clear()
      return
    }
    if (sourceRef.current) {
      sourceRef.current.close()
    }
    // Reset per-session state when the session changes
    if (prevSessionId.current !== sessionId) {
      isFirstConnect.current = true
      failedAttempts.current = 0
      lastEventId.current = ''
      // §9 复核：清空已提示错误键，让新会话的错误能正常提示；
      // 但同一会话内的历史错误在回放时不会重复弹出。
      seenErrorKeys.current.clear()
      // P0-2: event ids are session-scoped (EventStore per session) — drop
      // the old session's ids so a reused id can't suppress new events.
      seenEventIds.current.clear()
      prevSessionId.current = sessionId
    }

    useSSEStore.getState().setStatus('connecting')

    // Token via the auth store — the old code hand-parsed the
    // zustand-persist localStorage record ('sr-auth'), coupling the SSE
    // layer to the persistence internals.
    const parsedToken = useAuthStore.getState().token ?? ''

    const params = new URLSearchParams({ session_id: sessionId })
    if (parsedToken) params.set('token', parsedToken)
    if (lastEventId.current) params.set('Last-Event-ID', lastEventId.current)
    const es = new EventSource(`/api/chat/events?${params}`)

    es.onopen = () => {
      failedAttempts.current = 0
      lastAliveRef.current = Date.now()
      useSSEStore.getState().setStatus('connected')
      if (isFirstConnect.current) {
        isFirstConnect.current = false
      } else if (!lastEventId.current) {
        // Reconnect: clean stale streaming state that may be stuck
        const chat = useChatStore.getState()
        if (chat.streamingMessageId) {
          chat.setStreamingMessage(null)
          chat.setActiveAttempt(null)
        }
        console.debug('[SSE] reconnected — cleared stale streaming state')
      }
    }

    es.onerror = (e) => {
      if (e instanceof MessageEvent) return
      // Let the browser's native EventSource reconnect automatically —
      // it sends the Last-Event-ID header so missed events are replayed.
      const target = e.currentTarget as EventSource | null
      if (target) {
        console.debug('[SSE] onerror readyState=%s', target.readyState)
        if (target.readyState === EventSource.CLOSED) {
          // Permanent disconnect — browser won't auto-reconnect.
          // Create a new EventSource after a short delay, but give up
          // after MAX_FAILED_ATTEMPTS consecutive rejections (server
          // 403/404 — e.g. a session owned by a different user).
          failedAttempts.current += 1
          if (failedAttempts.current >= MAX_FAILED_ATTEMPTS) {
            console.warn(
              `[SSE] connection rejected ${failedAttempts.current} times for session ${sessionId}, giving up`,
            )
            useSSEStore.getState().setStatus('disconnected')
            sourceRef.current?.close()
            // 恢复交还给看门狗的 disconnected 分支（P0-5：代理半开时
            // onerror 可能永远不来，且放弃后无人重连会把状态钉死在已断开）。
            return
          }
          console.warn('[SSE] EventSource CLOSED, reconnecting in 1s...')
          if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
          reconnectTimer.current = setTimeout(() => {
            reconnectTimer.current = null
            connect()
          }, 1000)
          return
        }
      } else {
        console.debug('[SSE] onerror')
      }
      useSSEStore.getState().setStatus('disconnected')
    }

    EVENT_TYPES.forEach((type) => es.addEventListener(type, handleEvent))

    // Heartbeat handling: the backend sends periodic SSE comment lines
    // (no event type, just ": heartbeat\n\n"). We also listen for the
    // explicit "heartbeat" event in case the backend uses named events
    // in the future. Receiving either signal keeps the connection
    // marked as alive — defending against the browser prematurely
    // reporting onerror on idle streams.
    es.addEventListener('heartbeat', () => {
      lastAliveRef.current = Date.now()
      useSSEStore.getState().setStatus('connected')
    })

    sourceRef.current = es
  }, [sessionId, handleEvent])

  useEffect(() => {
    connect()
    // Liveness watchdog（P0-5 双分支）：
    //   ① zombie-connected：心跳每 15s 一跳，静默 >50s（3 跳 + 余量）＝
    //      连接是僵尸（TCP 经代理半开，onerror 不会来）→ 杀掉僵尸、
    //      立即重连，状态诚实翻转为已断开直到 onopen。
    //   ② disconnected：>10s 未尝试即主动慢速重连 —— 后端复活后最多
    //      ~15s 自动恢复，不再依赖「用户切会话/刷新」触发。
    const watchdog = setInterval(() => {
      const sse = useSSEStore.getState()
      const now = Date.now()
      if (sse.status === 'connected' && now - lastAliveRef.current > 50_000) {
        sse.setStatus('disconnected')
        lastReconnectRef.current = now
        sourceRef.current?.close()
        connect()
      } else if (sse.status === 'disconnected' && now - lastReconnectRef.current > 10_000) {
        lastReconnectRef.current = now
        connect()
      }
    }, 5_000)
    return () => {
      clearInterval(watchdog)
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
      sourceRef.current?.close()
    }
  }, [connect])
}