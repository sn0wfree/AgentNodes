import { useCallback, useEffect, useState } from 'react'
import { MessageSquareText, Minus, Play, Sparkles } from 'lucide-react'
import { useSSE } from '../../hooks/useSSE'
import { useDagStepApply } from '../../hooks/useDagStepApply'
import { ChatSessionProvider } from '../../contexts/ChatSessionContext'
import { api } from '../../api/client'
import { Composer } from '../chat/Composer'
import { MessageList } from '../chat/MessageList'
import { useChatStore } from '../../stores/chat'
import { usePersonaStore } from '../../stores/personas'
import type { DagSpec } from './dagSpec'

interface OrchestratorChatProps {
  dagId: string
  /** Read the current canvas as a DAG spec (attached to each user message). */
  getSnapshot: () => DagSpec
  /** Apply a validated spec to the canvas (replace + auto-layout + history). */
  onApplyDag: (spec: DagSpec) => void
}

/** Format DAG: as a a JSON code block, appended to the user message
 *  so the LLM always sees the current canvas as ground truth. */
function snapshotMarkdown(spec: DagSpec): string {
  return `\n\n\`\`\`json\n${JSON.stringify(spec)}\n\`\`\``
}

/** Thin orchestration shell.
 *
 *  Bootstrap a dag:{name} session, fix the persona to
 *  workflow_orchestrator, then hand rendering to the standard
 *  MessageList + Composer inside a ChatSessionProvider so the chat
 *  components pick up the orchestrator session id from context rather
 *  than from the global currentSessionId. SSE flows through the shared
 *  useSSE (AppShell pauses its own chat useSSE while on /dag — see
 *  components/layout/AppShell.tsx). Canvas side-effects are routed
 *  through useDagStepApply, which subscribes to chatStore.messages
 *  and applies completed submit_dag_step tools. */
export function OrchestratorChat({ dagId, getSnapshot, onApplyDag }: OrchestratorChatProps) {
  const sessionId = `dag:${dagId}`
  const [collapsed, setCollapsed] = useState(false)
  const [initError, setInitError] = useState<string | null>(null)

  // Bootstrap once per dagId: create/get session, pin persona,
  // load persisted messages.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        await api.orchestrate.session(dagId)
        usePersonaStore.getState().setSessionPersona(sessionId, 'workflow_orchestrator')
        await useChatStore.getState().loadMessages(sessionId)
      } catch (err) {
        if (!cancelled) {
          setInitError((err as Error).message || '编排会话初始化失败')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [dagId, sessionId])

  // Subscribe to the orchestrator session. AppShell is paused while
  // /dag is active so this is the single SSE connection.
  useSSE(sessionId)

  // Watch chatStore.messages for completed submit_dag_step tool
  // calls in this session and apply them to the canvas. Idempotent.
  useDagStepApply(onApplyDag)

  // Stale "asked user" flag: a new attempt clears it; the backend
  // sets it again on the next question-ending agent_done.
  const [askedUser, setAskedUser] = useState(false)
  useEffect(() => {
    return useChatStore.subscribe((state) => {
      const flagged = !!state.askedUserSessions.get(sessionId)
      const running = state.activeAttemptId !== null
      if (running) {
        if (flagged) state.setAskedUser(sessionId, false)
        setAskedUser(false)
      } else {
        setAskedUser(flagged)
      }
    })
  }, [sessionId])

  const continuePush = useCallback(async () => {
    useChatStore.getState().setAskedUser(sessionId, false)
    setAskedUser(false)
    await api.post('/chat/send_async', {
      session_id: sessionId,
      content: '请继续，自主完成剩余部分，不需要询问我。',
      agent_id: 'workflow_orchestrator',
    })
  }, [sessionId])

  const composeMessage = useCallback(
    (raw: string) => raw + snapshotMarkdown(getSnapshot()),
    [getSnapshot],
  )

  if (collapsed) {
    return (
      <aside className="wf-panel-solid flex w-10 shrink-0 flex-col items-center border-r py-2">
        <button
          onClick={() => setCollapsed(false)}
          title="展开编排助手"
          className="rounded-lg p-2 text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
        >
          <MessageSquareText className="h-4 w-4" />
        </button>
        <span className="mt-1 [writing-mode:vertical-rl] text-[10px] text-muted">编排助手</span>
      </aside>
    )
  }

  return (
    <aside className="wf-panel-solid flex w-72 shrink-0 flex-col border-r">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Sparkles className="h-4 w-4 text-primary-400" />
        <span className="wf-text-main text-xs font-semibold">编排助手</span>
        <span className="wf-text-sub truncate font-mono text-[10px]">{dagId}</span>
        <button
          onClick={() => setCollapsed(true)}
          title="折叠"
          className="ml-auto rounded p-1 text-muted hover:bg-surface-raised hover:text-secondary"
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
      </div>

      {initError && (
        <div className="border-b border-rose-800 bg-rose-950/40 px-3 py-2 text-[11px] text-rose-300">
          {initError}
        </div>
      )}

      <ChatSessionProvider sessionId={sessionId}>
        <MessageList />
        {askedUser && (
          <div className="flex items-center gap-2 border-b border-primary-800/50 bg-primary-950/30 px-3 py-1.5">
            <span className="text-[11px] text-primary-300">助手想询问你</span>
            <button
              onClick={continuePush}
              title="让助手继续推进，不询问你"
              className="ml-auto flex items-center gap-1 rounded-md bg-primary-600/80 px-2 py-1 text-[11px] font-medium text-white transition-colors hover:bg-primary-500"
            >
              <Play className="h-3 w-3" />
              继续推进
            </button>
          </div>
        )}
        <Composer
          composeMessage={composeMessage}
          readOnly="image"
        />
      </ChatSessionProvider>
    </aside>
  )
}