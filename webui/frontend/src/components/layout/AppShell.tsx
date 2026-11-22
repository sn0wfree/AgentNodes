import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts'
import { useSSE } from '../../hooks/useSSE'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { useLayoutStore } from '../../stores/layout'
import { IconNav } from './IconNav'
import { TopBar } from './TopBar'
import { MainSplit } from './MainSplit'
import { RightPanel } from './RightPanel'
import { SplitDivider } from './SplitDivider'
import { SessionSidebar } from '../chat/SessionSidebar'
import { ToastManager } from '../common/Toast'
import { CommandPalette } from '../common/CommandPalette'
import { SearchModal } from '../common/SearchModal'
import { PermissionRequestDialog } from '../chat/PermissionRequestDialog'
import { useToastStore } from '../../stores/toast'
import { api } from '../../api/client'

/**
 * Module-scoped init guard: React StrictMode (dev) double-mounts and
 * route remounts can start a second init while the first is still in
 * flight; without this guard both runs see an empty session list and
 * each POSTs a new session (P0-5 finding: 0 → 2 sessions on first
 * load). The flag survives remounts by living at module scope.
 */
let shellInitInFlight = false

export function AppShell() {
  useKeyboardShortcuts()
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const loadSessions = useSessionStore((s) => s.loadSessions)
  const [initError, setInitError] = useState('')

  useLayoutEffect(() => {
    const media = window.matchMedia('(max-width: 767px)')
    const update = () => useLayoutStore.getState().setNarrowViewport(media.matches)
    update()
    media.addEventListener('change', update)
    return () => {
      media.removeEventListener('change', update)
      useLayoutStore.getState().setNarrowViewport(false)
    }
  }, [])

  // Init: restore from persisted state or create fresh
  useEffect(() => {
    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    const init = async () => {
      // Concurrent-init guard: a second mount (StrictMode / route
      // remount / retry overlap) must not run a parallel init.
      if (shellInitInFlight) return
      shellInitInFlight = true
      try {
        // 0. Warm the agent-schema cache (fire-and-forget; JsonActionCard
        //    reads it synchronously via getSchemaFor when rendering).
        void import('../../api/agentSchemas').then(({ loadAgentSchemas }) =>
          loadAgentSchemas().catch(() => {}),
        )
        // 1. Load all sessions metadata (await so filter below works)
        // NOTE: no `cancelled` bail here — the store mutations below are
        // global and idempotent (guarded by shellInitInFlight + the
        // re-read before createNewSession). Bailing on unmount would
        // strand StrictMode's second mount, which was refused by the
        // in-flight guard and would otherwise leave zero sessions.
        await loadSessions()

        // 2. Read latest persisted state AFTER loadSessions
        const state = useSessionStore.getState()
        const validOpenIds = (state.openSessionIds ?? []).filter((id) =>
          state.sessions.some((s) => s.id === id) && !id.startsWith('study')
        )

        if (validOpenIds.length > 0) {
          // Re-open tabs in PARALLEL (was sequential await per tab —
          // N round-trips on boot; each openSession loads messages).
          const results = await Promise.allSettled(
            validOpenIds.map((id) => state.openSession(id))
          )
          results.forEach((r, i) => {
            if (r.status === 'rejected') {
              // 404 — session deleted server-side, skip
              console.debug('openSession rejected:', validOpenIds[i], r.reason)
            }
          })
          // Switch to current (or first valid)
          const target =
            state.currentSessionId && validOpenIds.includes(state.currentSessionId)
              ? state.currentSessionId
              : validOpenIds[0]
          if (target && target !== state.currentSessionId) {
            await state.switchSession(target)
          } else if (target) {
            // Same session — just reload messages and backfill
            // Agent / DAG / Goal panels (loadSessionState). The original
            // code only reloaded messages, leaving the right panel
            // empty until the next run started (B13 follow-up; see
            // session.ts loadSessionState docstring).
            await Promise.all([
              useChatStore.getState().loadMessages(target),
              state.loadSessionState(target),
            ])
          }
        } else {
          // No valid sessions — create one. Re-read the store first: a
          // racing init (retry timer / second mount) may have already
          // created the session while we were awaiting loadSessions.
          const latest = useSessionStore.getState()
          if (!latest.sessions.length && !latest.currentSessionId) {
            await latest.createNewSession('新会话')
          }
        }
        if (!cancelled) setInitError('')
      } catch (err) {
        console.error('AppShell init failed:', err)
        if (!cancelled) {
          setInitError(err instanceof Error && err.message ? err.message : '未知错误')
          retryTimer = setTimeout(init, 1000)
        }
      } finally {
        shellInitInFlight = false
      }
    }
    void init()
    return () => {
      cancelled = true
      clearTimeout(retryTimer)
    }
    // Run only on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const location = useLocation()
  // Route-level SSE isolation: /dag renders its own orchestrator session,
  // so AppShell must NOT also subscribe to the main chat session — that
  // would create two EventSources writing into the same global chatStore
  // and double the status indicator. The orchestrator panel mounts its
  // own useSSE(sessionId='dag:xxx') inside /dag.
  const isDagRoute = location.pathname.startsWith('/dag')
  useSSE(isDagRoute ? null : currentSessionId)

  const sidebarOpen = useLayoutStore((s) => s.sidebarOpen)
  const rightPanelVisible = useLayoutStore((s) => s.rightPanelVisible)
  const rightRatio = useLayoutStore((s) => s.rightRatio)
  const narrowViewport = useLayoutStore((s) => s.narrowViewport)

  return (
    <div className="relative flex h-screen overflow-hidden bg-app">
      <div className="relative z-10 flex h-full w-full overflow-hidden">
        <IconNav />
        {sidebarOpen && (
          <div className="flex-shrink-0 max-md:absolute max-md:bottom-0 max-md:left-16 max-md:top-0 max-md:z-20">
            <SessionSidebar />
          </div>
        )}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <TopBar />
          <div className="relative flex flex-1 overflow-hidden">
            {/* Left chat column — flex-1 absorbs all leftover width so the
                visible columns always tile edge-to-edge with no gap. */}
            <div className="flex flex-1 min-w-0">
              <MainSplit />
            </div>
            {rightPanelVisible && (
              <>
                {/* Divider: dragging TOWARD right (delta < 0) grows the
                    panel; chat (flex-1) absorbs the difference. */}
                {!narrowViewport && (
                  <SplitDivider
                    onDrag={(delta) => {
                      useLayoutStore.setState((s) => ({
                        rightRatio: s.rightRatio - delta,
                      }))
                    }}
                  />
                )}
                <div
                  className="flex h-full flex-shrink-0 overflow-hidden max-md:absolute max-md:bottom-0 max-md:right-0 max-md:top-0 max-md:z-20"
                  style={{ width: narrowViewport ? 'min(320px, 100%)' : `${rightRatio * 100}%` }}
                >
                  <RightPanel />
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      {initError && (
        <div role="alert" className="absolute bottom-4 left-4 z-30 rounded-lg border border-red-500/30 bg-surface px-4 py-3 text-sm text-slate-100 shadow-elevated">
          会话初始化失败：{initError}。正在重试…
        </div>
      )}
      <ToastManager />
      <CommandPalette />
      <SearchModal />
      <PermissionRequestDialogBridge />
    </div>
  )
}

/**
 * Bridge component: wires the chat store's `pendingPermission` slot
 * to the dialog's `onRespond` callback (which posts to the backend
 * gateway endpoint). Kept inline so the dialog is only rendered when
 * there is a real pending request, and so the `api` import is not
 * pulled into the dialog module (single responsibility).
 */
function PermissionRequestDialogBridge() {
  const pending = useChatStore((s) => s.pendingPermission)
  const addToast = useToastStore((s) => s.addToast)
  const [submitting, setSubmitting] = useState(false)
  const [responseError, setResponseError] = useState<{ id: string; message: string } | null>(null)
  const inFlight = useRef(false)

  if (!pending) return null

  const handleRespond = async (
    action: 'allow' | 'deny',
    permanent: boolean,
    reason?: string,
  ) => {
    if (inFlight.current) return
    inFlight.current = true
    setSubmitting(true)
    setResponseError(null)
    try {
      const res = await api.permission.respond({
        tool_call_id: pending.tool_call_id,
        action,
        permanent,
        reason,
      })
      if (res.status === 'ok' || res.status === 'expired') {
        useChatStore.setState((state) =>
          state.pendingPermission?.tool_call_id === pending.tool_call_id
            ? { pendingPermission: null }
            : {},
        )
        if (res.status === 'expired') {
          addToast('warning', '权限请求已过期（操作可能已超时）')
        } else {
          addToast(
            'success',
            permanent
              ? `已${action === 'allow' ? '始终允许' : '始终拒绝'} ${pending.tool_name}`
              : `已${action === 'allow' ? '允许' : '拒绝'}本次`,
          )
        }
      } else {
        const detail = res.status === 'no_gateway' ? '权限网关不可用' : `服务返回 ${res.status}`
        setResponseError({ id: pending.tool_call_id, message: `权限请求失败：${detail}` })
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '未知错误'
      setResponseError({ id: pending.tool_call_id, message: `权限请求失败：${msg}` })
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  return (
    <PermissionRequestDialog
      key={pending.tool_call_id}
      request={pending}
      onRespond={handleRespond}
      submitting={submitting}
      error={responseError?.id === pending.tool_call_id ? responseError.message : undefined}
    />
  )
}