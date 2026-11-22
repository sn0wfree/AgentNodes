import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '../api/client'
import type { Message } from './chat'
import { useChatStore } from './chat'
import { useWorkflowStore } from './workflow'
import { useAgentStore } from './agents'
import { useGoalStore } from './goal'

export interface Session {
  id: string
  user_id?: string
  title: string
  created_at: number
  updated_at: number
  starred: boolean
  tags: string[]
  message_count: number
  archived: boolean
}

export interface SearchHit {
  session_id: string
  session_title: string
  message_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  snippet: string
  score: number
  created_at: number
}

interface SessionState {
  sessions: Session[]
  openSessionIds: string[]
  currentSessionId: string | null
  searchResults: SearchHit[]
  searchOpen: boolean
  searchQuery: string
  isSearching: boolean
  /**
   * Optional client-side role filter applied on top of the backend
   * search hits. Tier B P52 — when null, all roles are shown.
   */
  searchRoleFilter: SearchHit['role'] | null

  // Existing
  setSessions: (sessions: Session[]) => void
  setCurrentSession: (id: string | null) => void
  addSession: (session: Session) => void
  removeSession: (id: string) => void

  // New
  loadSessions: () => Promise<void>
  switchSession: (id: string) => Promise<void>
  createNewSession: (title?: string) => Promise<Session>
  openSession: (id: string) => Promise<void>
  closeSession: (id: string) => void
  updateSessionMeta: (id: string, partial: Partial<Session>) => Promise<void>
  deleteSession: (id: string) => Promise<void>
  setSearchOpen: (open: boolean) => void
  runSearch: (query: string) => Promise<void>
  clearSearch: () => void
  /** Set the role filter; null clears it. */
  setSearchRoleFilter: (role: SearchHit['role'] | null) => void
  /** Backfill Agent / DAG / Goal panels from the backend snapshot. */
  loadSessionState: (id: string) => Promise<void>
}

/** Backend response type for GET /chat/session/{id}/state (B13 backfill). */
interface SessionStateResponse {
  agents: Array<{
    id: string
    session_id: string
    status: string
    name: string
    description?: string
    created_at: number
    updated_at: number
    finished_reason?: string
    tool_calls_count?: number
    compaction_count?: number
    last_compaction?: { layer: string; timestamp: number }
    context_tokens?: number
    context_tokens_limit?: number
    iterations_detail?: Array<{ iteration: number; prompt: string; response: string } | Record<string, unknown>>
    color?: string
  }> | null
  goal: {
    goal_id: string
    session_id: string
    status: string
    objective: string
    progress_percent?: number
    criteria?: Array<{
      criterion_id: string
      text: string
      status: string
      evidence_count?: number
    }>
    evidence_count?: number
    recap?: string | null
  } | null
  workflow: {
    name: string
    nodes: Array<{ id: string; label?: string; type?: string; status?: string }>
    edges: Array<{ id: string; source: string; target: string }>
    progress: {
      agents_completed?: number
      agents_total?: number
      current_layer?: number
      total_layers?: number
      paused?: boolean
      status?: string
    } | null
    agent_statuses?: Record<string, string>
  } | null
}

// Bump per runSearch call so stale search responses are dropped.
let searchSeq = 0

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
      searchOpen: false,
      searchQuery: '',
      isSearching: false,
      searchRoleFilter: null,

      setSessions: (sessions) => set({ sessions }),
      setCurrentSession: (id) => set({ currentSessionId: id }),
      addSession: (session) =>
        set((s) => ({
          sessions: [session, ...s.sessions.filter((x) => x.id !== session.id)],
        })),
      removeSession: (id) =>
        set((s) => ({
          sessions: s.sessions.filter((sess) => sess.id !== id),
          currentSessionId: s.currentSessionId === id ? null : s.currentSessionId,
          openSessionIds: s.openSessionIds.filter((sid) => sid !== id),
        })),

      loadSessions: async () => {
        try {
          const res = await api.get<{ sessions: Session[] }>('/chat/session')
          // Filter out study sessions — both bare study ids (study_*)
          // and round sessions (study:*) belong to the study detail
          // page's StudyChat widget, not the main chat sidebar.
          const filtered = (res.sessions ?? []).filter(
            (s) => !s.id.startsWith('study'),
          )
          set({ sessions: filtered })
        } catch (err) {
          console.error('loadSessions failed:', err)
          throw err
        }
      },

      /**
       * Backfill the per-session Agent / DAG / Goal panels after a page
       * reload (B13). The SSE stream only updates entries the store
       * already has; once the user reloads the page, the Agent list,
       * DAG nodes/edges, and current goal all go empty until the next
       * run starts. This method calls the backend's
       * ``GET /chat/session/{id}/state`` aggregator and seeds each
       * store from the snapshot, so the RightPanel renders real state
       * immediately instead of empty placeholders.
       *
       * Errors are logged + degrading to "no backfill" — the UI falls
       * back to the empty state, which is the prior behavior, so a
       * backend hiccup cannot regress switchSession.
       */
      loadSessionState: async (id: string) => {
        try {
          const data = await api.get<SessionStateResponse>(
            `/chat/session/${id}/state`,
          )
          // Agent store
          if (data.agents?.length) {
            useAgentStore.getState().setAgents(data.agents as any)
          } else {
            useAgentStore.getState().setAgents([])
          }
          // Workflow store
          if (data.workflow) {
            const wf = data.workflow
            useWorkflowStore.getState().setDAG(
              (wf.nodes || []).map((n) => ({
                id: n.id,
                label: n.label ?? n.id,
                type: n.type,
                status: n.status as any,
              })),
              (wf.edges || []).map((e) => ({
                id: e.id,
                source: e.source,
                target: e.target,
              })),
            )
            useWorkflowStore.getState().setPresets(
              wf.name
                ? [
                    {
                      id: wf.name,
                      name: wf.name,
                      description: '',
                      created_at: Date.now() / 1000,
                    },
                  ]
                : [],
            )
            useWorkflowStore.getState().setCurrentPreset(wf.name || null)
            if (wf.progress && typeof wf.progress.agents_completed === 'number' &&
                typeof wf.progress.agents_total === 'number' &&
                wf.progress.agents_total > 0) {
              useWorkflowStore.getState().setExecutionProgress(
                Math.round(
                  (wf.progress.agents_completed / wf.progress.agents_total) * 100,
                ),
              )
            }
          } else {
            useWorkflowStore.getState().setDAG([], [])
            useWorkflowStore.getState().setCurrentPreset(null)
            useWorkflowStore.getState().setExecutionProgress(0)
          }
          // Goal store
          if (data.goal) {
            useGoalStore.getState().setGoal({
              goal_id: data.goal.goal_id,
              session_id: data.goal.session_id,
              status: data.goal.status,
              objective: data.goal.objective,
              progress_percent: data.goal.progress_percent ?? 0,
              criteria: (data.goal.criteria || []).map((c) => ({
                criterion_id: c.criterion_id,
                text: c.text,
                status: c.status,
                required: true,
                evidence_count: c.evidence_count ?? 0,
              })),
              evidence_count: data.goal.evidence_count ?? 0,
              recap: data.goal.recap ?? undefined,
            })
          } else {
            useGoalStore.getState().clearGoal()
          }
        } catch (err) {
          console.error('loadSessionState failed:', err)
          // Fall back to empty panels rather than throwing — switchSession
          // must never regress on a backend 500 here.
          useAgentStore.getState().setAgents([])
          useWorkflowStore.getState().setDAG([], [])
          useWorkflowStore.getState().setCurrentPreset(null)
          useWorkflowStore.getState().setExecutionProgress(0)
          useGoalStore.getState().clearGoal()
        }
      },

      switchSession: async (id: string) => {
        const { openSessionIds, currentSessionId } = get()
        if (currentSessionId === id) return

        // Add to openSessionIds if not present
        if (!openSessionIds.includes(id)) {
          set({ openSessionIds: [...openSessionIds, id] })
        }
        set({ currentSessionId: id })

        // Clear current messages and load target session messages
        const chat = useChatStore.getState()
        chat.setMessages([])
        chat.setStreamingMessage(null)
        chat.setStreamingText('')
        // Per-session panels: workflow DAG / agents / goal are session-
        // scoped and must not bleed across sessions. Load the target
        // session's persisted state in parallel (B13) so the panels
        // render real data instead of empty placeholders after reload.
        useWorkflowStore.getState().setDAG([], [])
        useAgentStore.getState().setAgents([])
        useGoalStore.getState().clearGoal()
        await Promise.all([
          chat.loadMessages(id),
          get().loadSessionState(id),
        ])
      },

      createNewSession: async (title?: string) => {
        // Auto-generate title with timestamp if not provided
        const autoTitle = title || `会话 ${new Date().toLocaleString('zh-CN', {
          month: 'numeric',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        })}`
        const session = await api.post<Session>('/chat/session', { title: autoTitle })
        get().addSession(session)
        set((s) => ({
          openSessionIds: s.openSessionIds.includes(session.id)
            ? s.openSessionIds
            : [...s.openSessionIds, session.id],
          currentSessionId: session.id,
        }))
        // Clear chat for new session
        const chat = useChatStore.getState()
        chat.setMessages([])
        chat.setStreamingMessage(null)
        chat.setStreamingText('')
        useWorkflowStore.getState().setDAG([], [])
        useAgentStore.getState().setAgents([])
        useGoalStore.getState().clearGoal()
        return session
      },

      openSession: async (id: string) => {
        const { openSessionIds, sessions } = get()
        if (openSessionIds.includes(id)) {
          // Already open — just switch
          await get().switchSession(id)
          return
        }
        // Load session metadata if not present
        if (!sessions.find((s) => s.id === id)) {
          try {
            const session = await api.get<Session>(`/chat/session/${id}`)
            get().addSession(session)
          } catch (err) {
            console.error('openSession metadata failed:', err)
            throw err
          }
        }
        set({ openSessionIds: [...openSessionIds, id] })
        await get().switchSession(id)
      },

      closeSession: (id: string) => {
        const { openSessionIds, currentSessionId } = get()
        const newOpen = openSessionIds.filter((sid) => sid !== id)
        set({ openSessionIds: newOpen })

        if (currentSessionId !== id) return

        // Pick neighbor: next tab in current order, or previous
        const idx = openSessionIds.indexOf(id)
        const neighbor = newOpen[idx] || newOpen[idx - 1] || newOpen[0] || null
        if (neighbor) {
          // switchSession sets currentSessionId and loads messages
          void get().switchSession(neighbor)
        } else {
          // No tabs left — clear current
          set({ currentSessionId: null })
          const chat = useChatStore.getState()
          chat.setMessages([])
          chat.setStreamingMessage(null)
          chat.setStreamingText('')
        }
      },

      updateSessionMeta: async (id, partial) => {
        // Optimistic update
        set((s) => ({
          sessions: s.sessions.map((sess) =>
            sess.id === id ? { ...sess, ...partial } : sess
          ),
        }))
        try {
          const updated = await api.patch<Session>(`/chat/session/${id}`, partial)
          set((s) => ({
            sessions: s.sessions.map((sess) =>
              sess.id === id ? updated : sess
            ),
          }))
        } catch (err) {
          console.error('updateSessionMeta failed:', err)
          // Re-fetch to recover
          try {
            await get().loadSessions()
          } catch (refreshError) {
            console.error('session refresh failed:', refreshError)
          }
          throw err
        }
      },

      deleteSession: async (id: string) => {
        try {
          await api.delete(`/chat/session/${id}`)
        } catch (err) {
          console.error('deleteSession failed:', err)
          throw err
        }
        // Use removeSession to clean up locally
        get().removeSession(id)
        // If the deleted session was current, pick a neighbor
        if (get().currentSessionId === null) {
          const next = get().openSessionIds[0] || null
          if (next) {
            await get().switchSession(next)
          } else {
            // No tabs left — create a fresh session
            await get().createNewSession('新会话')
          }
        }
      },

      setSearchOpen: (open) => set({ searchOpen: open }),

      runSearch: async (query: string) => {
        const seq = ++searchSeq
        set({ searchQuery: query })
        if (!query.trim()) {
          set({ searchResults: [], isSearching: false })
          return
        }
        set({ isSearching: true })
        try {
          const res = await api.post<{ hits: SearchHit[] }>('/chat/session/search', {
            query,
            limit: 20,
          })
          if (seq !== searchSeq) return // stale response (newer query in flight)
          set({ searchResults: res.hits })
        } catch (err) {
          console.error('runSearch failed:', err)
          if (seq === searchSeq) set({ searchResults: [] })
        } finally {
          if (seq === searchSeq) set({ isSearching: false })
        }
      },

      clearSearch: () => set({ searchResults: [], searchQuery: '', searchRoleFilter: null }),
      setSearchRoleFilter: (role) => set({ searchRoleFilter: role }),
    }),
    {
      name: 'sr-sessions',
      partialize: (state) => ({
        openSessionIds: state.openSessionIds,
        currentSessionId: state.currentSessionId,
      }),
    }
  )
)

export type { Message }