import { create } from 'zustand'

type AgentMode = 'plan' | 'build'

interface ModeState {
  /** Current mode for the active session. */
  mode: AgentMode
  /** Session the store is currently bound to (null = unbound). */
  sessionId: string | null
  /** Set mode for the current session. Persists to localStorage. */
  setMode: (m: AgentMode) => void
  /** Toggle between plan and build. */
  toggleMode: () => void
  /** Load persisted mode for a session and bind the store to it. */
  loadSessionMode: (sessionId: string) => void
  /** Bind the store to a session without changing the current mode. */
  bindSession: (sessionId: string | null) => void
}

function loadPersisted(sessionId: string): AgentMode {
  if (typeof window === 'undefined') return 'build'
  const raw = localStorage.getItem(`sr-mode-${sessionId}`)
  return raw === 'plan' ? 'plan' : 'build'
}

function persist(sessionId: string, mode: AgentMode) {
  if (typeof window !== 'undefined' && sessionId) {
    localStorage.setItem(`sr-mode-${sessionId}`, mode)
  }
}

export const useModeStore = create<ModeState>()((set, get) => ({
  mode: 'build',
  sessionId: null,
  setMode: (m) => {
    set({ mode: m })
    const { sessionId } = get()
    if (sessionId) persist(sessionId, m)
  },
  toggleMode: () => {
    const next = get().mode === 'plan' ? 'build' : 'plan'
    set({ mode: next })
    const { sessionId } = get()
    if (sessionId) persist(sessionId, next)
  },
  loadSessionMode: (sessionId) => {
    const mode = loadPersisted(sessionId)
    set({ mode, sessionId })
  },
  bindSession: (sessionId) => {
    set({ sessionId })
  },
}))
