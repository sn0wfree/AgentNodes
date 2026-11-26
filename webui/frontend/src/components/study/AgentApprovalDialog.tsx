import { useCallback, useMemo } from 'react'
import { AlertTriangle, Check, X } from 'lucide-react'
import { api } from '../../api/client'
import { useStudyStore, type AgentApprovalRequest } from '../../stores/study'

interface AgentApprovalDialogProps {
  /** Optional study-id filter so each study page only shows its own gates. */
  studyId?: string
}

const TIMEOUT_MINUTES = (s: number) => Math.round(s / 60)

export function AgentApprovalDialog({ studyId }: AgentApprovalDialogProps) {
  const approvals = useStudyStore((s) => s.agentApprovals)
  const resolve = useStudyStore((s) => s.resolveAgentApproval)

  // Pick the oldest pending approval for the current study (if any).
  const active: AgentApprovalRequest | null = useMemo(() => {
    const list = Object.values(approvals)
      .filter((a) => !studyId || a.study_id === studyId)
      .sort((a, b) => a.requested_at - b.requested_at)
    return list[0] ?? null
  }, [approvals, studyId])

  const respond = useCallback(
    async (decision: 'approved' | 'reject') => {
      if (!active) return
      try {
        await api.study.approveAgentLoop(active.study_id, decision)
      } catch (err) {
        // Backend may have already timed out — log and dismiss.
        // eslint-disable-next-line no-console
        console.warn('approveAgentLoop failed:', err)
      } finally {
        resolve(active.study_id, active.role, active.iteration)
      }
    },
    [active, resolve],
  )

  if (!active) return null

  const timeoutMin = TIMEOUT_MINUTES(active.timeout_s)

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Agent 循环检测"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4 backdrop-blur-sm"
    >
      <div className="flex w-full max-w-md flex-col gap-4 rounded-2xl border border-warning/40 bg-slate-900 p-5 shadow-elevated">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-warning-fg" />
          <h2 className="text-sm font-semibold text-foreground">Agent 循环检测</h2>
        </div>

        <div className="rounded-lg border border-warning/20 bg-warning-fill px-3 py-2 text-[11px] leading-relaxed text-warning-fg">
          {active.message}
        </div>

        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted">
          <dt className="text-muted">Role</dt>
          <dd className="font-mono text-foreground">{active.role ?? '(unknown)'}</dd>
          <dt className="text-muted">Iteration</dt>
          <dd className="font-mono text-foreground">{active.iteration}</dd>
          <dt className="text-muted">Tool hash</dt>
          <dd className="truncate font-mono text-foreground">{active.tool_hash || '-'}</dd>
          <dt className="text-muted">Window</dt>
          <dd className="font-mono text-foreground">{active.window} 次</dd>
        </dl>

        <p className="text-[10px] text-muted">
          ⏱ {timeoutMin} 分钟内无响应将默认 {active.on_timeout === 'reject' ? '中止该 Agent' : '继续循环'}。
        </p>

        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={() => void respond('reject')}
            className="inline-flex items-center gap-1 rounded-lg border border-rose-600/40 bg-rose-900/20 px-3 py-1.5 text-xs text-rose-200 transition-colors hover:bg-rose-900/40 hover:text-rose-50 active:scale-95"
          >
            <X className="h-3.5 w-3.5" />
            中止该 Agent
          </button>
          <button
            type="button"
            onClick={() => void respond('approved')}
            className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs text-white transition-colors hover:bg-emerald-500 active:scale-95"
          >
            <Check className="h-3.5 w-3.5" />
            批准继续
          </button>
        </div>
      </div>
    </div>
  )
}