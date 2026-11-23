import { useState } from 'react'
import { Target, ChevronDown, ChevronRight, CheckCircle2 } from 'lucide-react'
import type { Message } from '../../stores/chat'
import { formatTime } from '../../utils/time'

const CHANGE_LABELS: Record<string, string> = {
  create: '创建目标',
  evidence: '添加证据',
  complete: '完成目标',
  update: '目标更新',
}

const STATUS_LABELS: Record<string, string> = {
  covered: '已覆盖',
  in_progress: '进行中',
  pending: '待办',
}

/**
 * Chat-stream card for message_type='goal' (backend goal_updated full
 * snapshot). Collapsed by default: shows change type + objective /
 * latest evidence + progress. Expand to see criteria status and the
 * FULL evidence text (audit).
 */
export function GoalMessage({ message }: { message: Message }) {
  const [expanded, setExpanded] = useState(false)
  const meta = message.metadata ?? {}

  const changeType = (meta.change_type as string) || 'update'
  const label = CHANGE_LABELS[changeType] || '目标更新'
  const objective = (meta.objective as string) || ''
  const progress = (meta.progress_percent as number) ?? 0
  const evidenceCount = (meta.evidence_count as number) ?? 0
  const status = (meta.goal_status as string) || 'active'
  const evidenceText = (meta.evidence_text as string) || ''
  const recap = (meta.recap as string) || ''
  const criteria = Array.isArray(meta.criteria) ? meta.criteria : []
  const isComplete = status === 'complete'

  const preview = changeType === 'evidence' && evidenceText
    ? evidenceText.length > 50
      ? `${evidenceText.slice(0, 50)}…`
      : evidenceText
    : objective

  return (
    <div className="px-4 py-3">
      <div className="rounded-lg border border-slate-700/50 bg-slate-800/30">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex w-full items-start gap-2 px-3 py-2.5 text-left transition-colors hover:bg-slate-800/40"
        >
          <div
            className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
              isComplete
                ? 'bg-emerald-500/10 text-emerald-400'
                : 'bg-primary-500/10 text-primary-400'
            }`}
          >
            {isComplete ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : (
              <Target className="h-3.5 w-3.5" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <div className="mb-1 flex items-center gap-2 text-xs">
              <span className={`font-medium ${isComplete ? 'text-emerald-400' : 'text-slate-300'}`}>
                {label}
              </span>
              <span className="text-slate-600">{formatTime(message.created_at)}</span>
              {evidenceCount > 0 && (
                <span className="ml-auto text-[10px] text-slate-500">
                  {evidenceCount} 条证据
                </span>
              )}
            </div>
            <div className="text-sm leading-relaxed text-slate-300">{preview}</div>
            <div className="mt-2 flex items-center gap-2">
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-slate-800">
                <div
                  className={`h-full rounded-full transition-all ${
                    isComplete ? 'bg-emerald-500' : 'bg-primary-500'
                  }`}
                  style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
                />
              </div>
              <span className="font-mono text-[10px] text-slate-500">{progress}%</span>
              <span className="text-slate-600">
                {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              </span>
            </div>
          </div>
        </button>

        {expanded && (
          <div className="border-t border-slate-800 px-3 py-2.5 text-xs">
            {objective && (
              <div className="mb-2">
                <span className="text-slate-500">目标: </span>
                <span className="text-slate-300">{objective}</span>
              </div>
            )}
            {criteria.length > 0 && (
              <div className="mb-2 space-y-1">
                {criteria.map((c) => (
                  <div key={c.criterion_id} className="flex items-center gap-2">
                    <span
                      className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                        c.status === 'covered'
                          ? 'bg-emerald-500'
                          : c.status === 'in_progress'
                            ? 'bg-warning'
                            : 'bg-slate-600'
                      }`}
                    />
                    <span className="text-slate-400">{c.text}</span>
                    <span className="ml-auto text-slate-600">
                      {STATUS_LABELS[c.status] || c.status}
                      {c.evidence_count > 0 ? ` · ${c.evidence_count}` : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {evidenceText && (
              <div className="mb-2 rounded bg-slate-900/50 p-2 text-slate-400">
                <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-600">
                  证据全文
                </span>
                <span className="whitespace-pre-wrap break-words">{evidenceText}</span>
              </div>
            )}
            {recap && (
              <div className="rounded bg-emerald-500/5 p-2 text-emerald-300">
                <span className="mb-1 block text-[10px] uppercase tracking-wider text-emerald-500/70">
                  总结
                </span>
                {recap}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
