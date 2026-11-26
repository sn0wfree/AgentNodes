/**
 * StudyChat — unified chat widget for the study detail page.
 *
 * Uses the chat session API for loading messages (event-sourced via Projector).
 * Each round creates a session: study:{studyId}:round:{roundNum}
 *
 * Layout:
 *   Header: round indicator
 *   Body: MessageList (with round separators + scroll-to-bottom arrow)
 *         + RoundNavRail (right-edge overlay dots)
 *         + InterruptApprovalCard (when HITL pending)
 *         + StudyChatComposer (directive-only)
 */
import { useState, useEffect, useRef } from 'react'
import { Loader2 } from 'lucide-react'
import {
  api,
  type StudyRoundSummary,
} from '../../../../api/client'
import { useStudyStore } from '../../../../stores/study'
import { useChatStore, type Message } from '../../../../stores/chat'
import { ChatSessionProvider } from '../../../../contexts/ChatSessionContext'
import { MessageList } from '../../../chat/MessageList'
import type { WidgetProps } from '../types'
import { StudyChatComposer } from './StudyChatComposer'
import { InterruptApprovalCard } from './InterruptApprovalCard'
import { clampRound } from '../../utils'

// ── SSE event → Message (for live streaming) ──────────────────

export function buildEventMessage(
  event: { type: string; message: string; timestamp: number },
  studyId: string,
  currentRound: number,
): Message {
  return {
    id: `sse:${event.timestamp}:${event.type}`,
    session_id: `study:${studyId}:round:${currentRound}`,
    role: 'system',
    parts: [{ type: 'text', id: `evt:${event.timestamp}`, text: event.message }],
    created_at: event.timestamp / 1000,
    metadata: {
      kind: 'system',
      round: currentRound,
    },
  }
}

export function buildAgentEventMessage(
  event: { type: string; [k: string]: any },
  studyId: string,
  currentRound: number,
): Message | null {
  const eventType = event.type as string
  const resolvedAgentId = event.agent || event.data?.agent || ''
  const data = (event.data || {}) as Record<string, any>
  const ts: number = event.timestamp || Date.now()
  // Display whitelist: only render events that carry user-visible
  // conclusions or tool activity. Low-level lifecycle events
  // (thinking_start/done, text.started/ended, iter_start/end,
  // loop_start/final, llm_usage, session_total_tokens…) were rendered
  // as bare event-name cards ("thinking_done") or empty placeholders —
  // see docs/rootcause-goal-injection-maxiter.md §8.
  // (agent_text_delta is not here: the dispatcher drops those events
  // upstream before they ever reach the timeline.)
  const DISPLAYABLE = new Set([
    'agent_assistant_message',
    'agent_tool_call',
    'agent_tool_result',
  ])
  if (!DISPLAYABLE.has(eventType)) {
    // F1: return null so non-displayable events are never injected as
    // messages. The old empty-parts placeholder still rendered as a
    // header-only card and accumulated in the store.
    return null
  }

  let text: string = ''

  if (eventType === 'agent_tool_call') {
    const toolName = data.tool || data.name || '未知工具'
    const args = data.arguments || data.args || {}
    const argsStr = typeof args === 'string' ? args : JSON.stringify(args, null, 2)
    text = `🔧 \`${toolName}\`\n\`\`\`json\n${argsStr}\n\`\`\``
  } else if (eventType === 'agent_tool_result') {
    const toolName = data.tool || data.name || '工具'
    const status = data.status || 'ok'
    const result = data.result || data.output || ''
    const resultStr = typeof result === 'string' ? result.slice(0, 500) : JSON.stringify(result, null, 2).slice(0, 500)
    text = `📋 \`${toolName}\` → ${status}\n\`\`\`\n${resultStr}\n\`\`\``
  } else if (eventType === 'agent_text_delta') {
    text = data.text || data.delta || ''
  } else if (eventType === 'agent_assistant_message') {
    const content = data.content || data.text || ''
    text = typeof content === 'string' ? content : JSON.stringify(content)
  } else {
    const message = data.message || data.text || ''
    text = message || `${eventType.replace('agent_', '')}`
  }

  if (!text) return null

  return {
    id: `agent:${ts}:${eventType}:${resolvedAgentId}`,
    session_id: `study:${studyId}:round:${currentRound}`,
    role: 'system',
    agent_id: resolvedAgentId || undefined,
    parts: [{ type: 'text', id: `aevt:${ts}:${eventType}`, text }],
    created_at: ts / 1000,
    metadata: {
      kind: 'agent',
      model: resolvedAgentId,
      round: currentRound,
    },
  }
}

/** separatorKey function: extracts round from metadata for round dividers. */
function roundKey(msg: Message): string | null {
  const round = msg.metadata?.round
  if (round != null) return String(round)
  return null
}

// ── Round discovery fallback ──────────────────────────────────

/**
 * Extract round numbers from chat session ids of the form
 * `study:{studyId}:round:{N}`. Returns ascending unique numbers.
 *
 * Fallback for studies whose `study_rounds` DB rows are missing (e.g.
 * rounds crashed before finalization) — the chat sessions still
 * exist, so the round nav rail + message loading can recover from
 * them instead of rendering an empty page.
 */
export function discoverRoundSessions(
  sessions: { id: string }[],
  studyId: string,
): number[] {
  const prefix = `study:${studyId}:round:`
  const rounds = sessions
    .filter((s) => s.id.startsWith(prefix))
    .map((s) => parseInt(s.id.slice(prefix.length), 10))
    .filter((n) => Number.isInteger(n) && n > 0)
  return [...new Set(rounds)].sort((a, b) => a - b)
}

/** Build minimal round summaries for the nav rail from round numbers. */
export function toRoundSummaries(roundNums: number[]): StudyRoundSummary[] {
  return roundNums.map((n) => ({
    round_num: n,
    run_name: '',
    metrics: null,
    verdict: null,
    created_at: '',
  }))
}

// ── RoundNavRail ──────────────────────────────────────────────

function RoundNavRail({
  rounds,
  selectedRound,
  onSelectRound,
}: {
  rounds: StudyRoundSummary[]
  selectedRound: number
  onSelectRound: (n: number) => void
}) {
  const sorted = [...rounds].sort((a, b) => a.round_num - b.round_num)

  return (
    <div
      className="pointer-events-none absolute inset-y-0 right-1 z-10 flex items-center"
      aria-label="轮次导航"
    >
      <div className="pointer-events-auto flex flex-col items-center gap-0 rounded-full bg-surface px-1 py-2 backdrop-blur-sm">
        {sorted.map((r, i) => {
          const isActive = r.round_num === selectedRound
          return (
            <div key={r.round_num} className="flex flex-col items-center">
              <button
                type="button"
                onClick={() => onSelectRound(r.round_num)}
                title={`Round ${r.round_num} · ${r.verdict ?? '—'}`}
                aria-label={`跳转到 Round ${r.round_num}`}
                className={`group relative rounded-full transition-all duration-200 ${
                  isActive
                    ? 'h-2.5 w-2.5 bg-primary-400 shadow-[0_0_8px_rgba(99,179,237,0.5)]'
                    : 'h-1.5 w-1.5 bg-slate-600 hover:bg-slate-400'
                }`}
              >
                <div className="pointer-events-none absolute right-full top-1/2 mr-2 hidden -translate-y-1/2 whitespace-nowrap rounded-md border border-strong bg-surface-raised/95 px-2 py-1 text-[10px] text-foreground shadow-lg backdrop-blur group-hover:block">
                  <div className="font-medium text-foreground">Round {r.round_num}</div>
                  <div className="text-muted">
                    {r.verdict ?? '—'} · {r.run_name.slice(0, 24)}
                  </div>
                </div>
              </button>
              {i < sorted.length - 1 && (
                <div className="my-0.5 h-3 w-px bg-surface-raised" />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Main Widget ────────────────────────────────────────────────

export function StudyChat({ studyId, summary }: WidgetProps) {
  // DB may persist a real 0 for current_round; `??` would not catch it.
  const currentRound = clampRound(summary?.current_round as number | undefined) ?? 1
  const [selectedRound, setSelectedRound] = useState(currentRound)
  const [loading, setLoading] = useState(false)
  const recentEvents = useStudyStore((s) => s.recentEvents)
  const hitlInterrupt = useStudyStore((s) => s.hitlInterrupt)
  const chatStore = useChatStore()
  // Consume cursor: highest LiveEvent.seq already injected into the
  // chat. Length-diff broke once the store buffer hit its 50-entry cap
  // (length pinned at 50 → newCount ≤ 0 forever).
  const lastSeqRef = useRef(0)
  const prevStudyIdRef = useRef<string | null>(null)
  // F4 auto-follow guard: tracks the round the auto-sync last picked.
  // While the user is browsing an older round (selectedRound !==
  // autoFollowRef) a new round must NOT yank the viewport forward.
  const autoFollowRef = useRef(currentRound)
  // Round discovery fallback: populated when study_rounds has no rows
  // but chat sessions for the rounds exist (crashed before finalize).
  const [discoveredRounds, setDiscoveredRounds] = useState<number[]>([])

  /** Manual round selection — auto-follow resumes only when the user
   * lands on the latest round; browsing history keeps the last
   * auto-set value so the guard in the sync effect stays closed. */
  const selectRoundManual = (n: number) => {
    if (n >= currentRound) {
      autoFollowRef.current = n
    }
    setSelectedRound(n)
  }

  const dbRounds: StudyRoundSummary[] = summary?.recent_rounds ?? []
  const rounds: StudyRoundSummary[] = dbRounds.length > 0
    ? dbRounds
    : toRoundSummaries(discoveredRounds)

  // Fallback discovery: only when the DB rounds list is empty. Scans
  // the chat session list for `study:{studyId}:round:N` sessions and
  // jumps to the latest discovered round (current_round may point at
  // a session that no longer exists).
  useEffect(() => {
    if (dbRounds.length > 0) return
    let cancelled = false
    api
      .get<{ sessions: { id: string }[] }>('/chat/session')
      .then((res) => {
        if (cancelled) return
        const found = discoverRoundSessions(res.sessions ?? [], studyId)
        if (found.length > 0) {
          setDiscoveredRounds(found)
          setSelectedRound((prev) =>
            found.includes(prev) ? prev : found[found.length - 1],
          )
          autoFollowRef.current = found[found.length - 1]
        }
      })
      .catch(() => {
        /* best-effort — page just shows empty state */
      })
    return () => { cancelled = true }
  }, [studyId, dbRounds.length])

  // Sync selectedRound with currentRound when it changes — but only
  // while the user hasn't navigated into history (F4). They rejoin by
  // selecting the latest round or clicking 回到最新.
  useEffect(() => {
    if (currentRound > 0 && selectedRound === autoFollowRef.current) {
      autoFollowRef.current = currentRound
      setSelectedRound(currentRound)
    }
  }, [currentRound, selectedRound])

  // Load messages for selected round from chat session API (event-sourced)
  useEffect(() => {
    let cancelled = false
    setLoading(true)

    const sessionId = `study:${studyId}:round:${selectedRound}`

    chatStore.loadMessages(sessionId)
      .then(() => {
        if (!cancelled) setLoading(false)
      })
      .catch(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [studyId, selectedRound])

  // Clear messages on studyId change. Declared BEFORE the inject effect:
  // within one commit effects run in declaration order, so the cursor
  // must skip the outgoing study's buffered events before injection sees
  // them — otherwise up to 50 stale events replay into the new study's
  // chat (events are tagged with the NEW studyId at build time).
  useEffect(() => {
    if (prevStudyIdRef.current !== studyId) {
      chatStore.setMessages([])
      lastSeqRef.current = recentEvents.reduce(
        (m, e) => Math.max(m, e.seq ?? 0), 0,
      )
      // Reset the F4 auto-follow guard: a study switch always snaps to
      // the new study's latest round, even if the user was browsing
      // history of the previous study (autoFollow === selectedRound
      // re-arms the sync effect for the incoming summary).
      autoFollowRef.current = currentRound
      setSelectedRound(currentRound)
      prevStudyIdRef.current = studyId
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chatStore/recentEvents/currentRound intentionally read once per study switch
  }, [studyId])

  // F2 reload recovery: after a page refresh the study_paused SSE event
  // is gone, so rebuild the HITL approval card from the DB. Only fires
  // when the study looks paused and the SSE path hasn't already opened
  // the card slot.
  const executionStatus = summary?.execution_status as string | undefined
  useEffect(() => {
    if (!studyId) return
    if (hitlInterrupt) return // SSE path already opened the card
    const pausedLike =
      executionStatus === 'paused' || executionStatus === 'awaiting_approval'
    if (!pausedLike) return
    let cancelled = false
    api.study
      .pendingInterrupt(studyId)
      .then((res) => {
        if (cancelled || !res.interrupt) return
        useStudyStore.getState().setHitlInterrupt({
          study_id: studyId,
          interrupt_id: res.interrupt.interrupt_id,
          round: res.interrupt.round_num,
          hypothesis: res.interrupt.hypothesis,
          message:
            res.interrupt.message ||
            `Round ${res.interrupt.round_num ?? '?'}: 请审批 researcher 假设`,
        })
      })
      .catch(() => {
        /* best-effort — the SSE event will open the card when it arrives */
      })
    return () => { cancelled = true }
  }, [studyId, executionStatus, hitlInterrupt])

  // Inject SSE events as system messages + agent-level events (live streaming)
  useEffect(() => {
    if (recentEvents.length === 0) return
    // Store prepends newest-first: [newest, ..., oldest]. The consume
    // cursor is the monotonic LiveEvent.seq — length-diff silently died
    // once the buffer reached its 50-entry cap.
    const fresh = recentEvents.filter((e) => (e.seq ?? 0) > lastSeqRef.current)
    if (fresh.length === 0) return
    lastSeqRef.current = fresh.reduce((m, e) => Math.max(m, e.seq ?? 0), lastSeqRef.current)

    fresh.forEach((event) => {
      const raw = event.raw
      // F3: tag the message with the event's TRUE round (from the
      // payload), not the round the user happens to be viewing —
      // events streamed while browsing an old round used to land in
      // the wrong session bucket and vanish on reload.
      const rawRound = Number(raw?.data?.round)
      const evRound = Number.isFinite(rawRound) && rawRound > 0
        ? rawRound
        : currentRound
      if (raw?.type?.startsWith('agent_')) {
        const msg = buildAgentEventMessage(
          { ...raw.data, type: raw.type, timestamp: event.timestamp },
          studyId,
          evRound,
        )
        // F1: non-displayable events build to null — never inject them.
        if (msg) chatStore.addMessage(msg)
        return
      }

      chatStore.addMessage(buildEventMessage(event, studyId, evRound))
      // F2: HITL approval cards are driven by the study_paused SSE
      // event (studyHandlers), which carries the REAL DB interrupt_id.
      // The old synthetic `pending:{studyId}:{round}` id always 404'd.
    })
  }, [recentEvents, studyId, currentRound])

  const scrollKey = `${studyId}:${selectedRound}`
  // Provider sessionId MUST equal the sessionId passed to chatStore.loadMessages
  // (line below: `study:${studyId}:round:${selectedRound}`), otherwise the
  // MessageList filter `m.session_id === currentSessionId` drops every message
  // and the page renders the empty state.
  const providerSessionId = `study:${studyId}:round:${selectedRound}`

  return (
    <ChatSessionProvider sessionId={providerSessionId}>
      <div className="flex h-full flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-subtle px-3 py-2">
          <div className="flex items-center gap-2 text-xs text-muted">
            <span className="font-mono text-secondary">
              Round {selectedRound}
              {loading && <Loader2 className="ml-1 inline h-3 w-3 animate-spin" />}
            </span>
            {selectedRound !== currentRound && (
              <button
                onClick={() => selectRoundManual(currentRound)}
                className="text-[10px] text-primary-400 hover:text-primary-300"
              >
                (回到最新)
              </button>
            )}
          </div>
        </div>

        {/* Body */}
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1">
            <MessageList separatorKey={roundKey} scrollKey={scrollKey} />
            {rounds.length > 1 && (
              <RoundNavRail
                rounds={rounds}
                selectedRound={selectedRound}
                onSelectRound={selectRoundManual}
              />
            )}
          </div>

          {/* HITL Approval Card — driven by the study_paused SSE event
              (real DB interrupt_id; see studyHandlers.studyPaused). */}
          {hitlInterrupt && hitlInterrupt.study_id === studyId && (
            <div className="flex-shrink-0 border-t border-subtle p-3">
              <InterruptApprovalCard
                studyId={studyId}
                interruptId={hitlInterrupt.interrupt_id}
                hypothesis={hitlInterrupt.hypothesis}
                message={hitlInterrupt.message}
                onApproved={() => useStudyStore.getState().clearHitlInterrupt(studyId)}
                onRejected={() => useStudyStore.getState().clearHitlInterrupt(studyId)}
              />
            </div>
          )}

          {/* Composer */}
          <div className="flex-shrink-0">
            <StudyChatComposer studyId={studyId} />
          </div>
        </div>
      </div>
    </ChatSessionProvider>
  )
}
