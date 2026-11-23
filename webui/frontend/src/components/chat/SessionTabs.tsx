import { useMemo } from 'react'
import {
  Plus, X, Star,
} from 'lucide-react'
import { useSessionStore, type Session } from '../../stores/session'

export function SessionTabs() {
  const openSessionIds = useSessionStore((s) => s.openSessionIds)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const sessions = useSessionStore((s) => s.sessions)
  const switchSession = useSessionStore((s) => s.switchSession)
  const closeSession = useSessionStore((s) => s.closeSession)
  const createNewSession = useSessionStore((s) => s.createNewSession)

  // Build open sessions in order; if metadata missing, skip
  const openSessions = useMemo(
    () =>
      (openSessionIds ?? [])
        .map((id) => (sessions ?? []).find((sess) => sess.id === id))
        .filter((s): s is Session => Boolean(s)),
    [openSessionIds, sessions]
  )

  return (
    <div className="flex h-10 items-center gap-1 border-b border-slate-800 bg-slate-900/40 px-2 overflow-x-auto flex-shrink-0">
      {openSessions.map((sess) => {
        const isActive = sess.id === currentSessionId
        return (
          <div
            key={sess.id}
            className={`group relative flex h-7 items-center gap-1.5 rounded-t-md border-b-2 px-2.5 text-xs transition-colors cursor-pointer flex-shrink-0
              ${isActive
                ? 'border-sky-500 bg-slate-800 text-slate-100'
                : 'border-transparent text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
              }
            `}
            onClick={() => void switchSession(sess.id)}
            title={sess.title}
          >
            {sess.starred && (
              <Star className="h-3 w-3 fill-slate-300 text-slate-300 flex-shrink-0" />
            )}
            <span className="max-w-[160px] truncate" title={sess.title}>{sess.title}</span>
            <button
              onClick={(e) => {
                e.stopPropagation()
                closeSession(sess.id)
              }}
              className="flex h-4 w-4 items-center justify-center rounded text-slate-500 hover:bg-slate-700 hover:text-slate-200 opacity-0 group-hover:opacity-100 transition-opacity"
              title="关闭（保留历史）"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        )
      })}

      <button
        onClick={() => void createNewSession('新会话')}
        className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 hover:bg-slate-800 hover:text-slate-200 flex-shrink-0"
        title="新建会话 (⌘T)"
      >
        <Plus className="h-3.5 w-3.5" />
      </button>

      {/* Spacer pushes search button right */}
      <div className="flex-1" />
    </div>
  )
}
