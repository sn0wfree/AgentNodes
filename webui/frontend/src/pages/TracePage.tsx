import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ScrollText } from 'lucide-react'
import { PageShell } from '../components/layout/PageShell'
import { TraceViewer } from '../components/chat/TraceViewer'
import { useSessionStore } from '../stores/session'

/** Trace 独立页（导航侧边框入口）——历史会话的事件时间线回放。
 *  Monitor = 实时系统状态；Trace = 历史单次运行回放（边界明确）。
 *  会话下拉只列本账户历史（后端 /chat/session 已按 user_id 过滤）；
 *  不提供任意 sessionId 输入（避免 ownership 旁路）。 */
export function TracePage() {
  const navigate = useNavigate()
  const sessions = useSessionStore((s) => s.sessions)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const [selectedId, setSelectedId] = useState(currentSessionId || sessions[0]?.id || '')

  return (
    <PageShell title="Trace" subtitle="历史运行回放 · 事件时间线" icon={<ScrollText className="h-4 w-4" />}>
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <label htmlFor="trace-session" className="text-xs text-muted">会话</label>
          <select
            id="trace-session"
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="max-w-md rounded-lg border border-strong bg-surface-raised px-2 py-1 text-xs text-foreground outline-none focus:border-primary-500"
          >
            {sessions.length === 0 && <option value="">（暂无会话）</option>}
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>{s.title}</option>
            ))}
          </select>
          <button onClick={() => navigate('/chat')} className="ml-auto text-xs text-muted hover:text-secondary">
            ← 返回会话
          </button>
        </div>
        <div className="h-[calc(100vh-220px)] min-h-[420px] rounded-lg border border-subtle bg-slate-950">
          {selectedId ? (
            <TraceViewer sessionId={selectedId} onClose={() => navigate('/chat')} />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted">
              选择一条会话以查看其 Trace
            </div>
          )}
        </div>
      </div>
    </PageShell>
  )
}
