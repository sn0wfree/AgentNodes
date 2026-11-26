import { Target, CheckCircle, Circle, Clock } from 'lucide-react'

interface Criterion {
  id: string
  description: string
  status: 'pending' | 'in_progress' | 'completed'
  evidence_count: number
  agent_id?: string
}

interface CriteriaListProps {
  criteria: Criterion[]
  totalCriteria: number
}

const STATUS_ICON = {
  pending: <Circle className="h-3.5 w-3.5 text-muted" />,
  in_progress: <Clock className="h-3.5 w-3.5 text-warning-fg animate-pulse" />,
  completed: <CheckCircle className="h-3.5 w-3.5 text-success-fg" />,
}

export function CriteriaList({ criteria, totalCriteria }: CriteriaListProps) {
  const completed = criteria.filter((c) => c.status === 'completed').length
  const progress = totalCriteria > 0 ? (completed / totalCriteria) * 100 : 0

  return (
    <div className="space-y-3">
      {/* Progress bar */}
      <div>
        <div className="flex items-center justify-between text-xs mb-1.5">
          <span className="text-muted">完成进度</span>
          <span className="text-secondary font-mono">
            {completed}/{totalCriteria} ({Math.round(progress)}%)
          </span>
        </div>
        <div className="h-1.5 rounded-full bg-surface-raised overflow-hidden">
          <div
            className="h-full rounded-full bg-primary-500 transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      {/* Criteria items */}
      <div className="space-y-1.5">
        {criteria.map((c) => (
          <div
            key={c.id}
            className="flex items-start gap-2 rounded-lg px-2.5 py-2 text-xs hover:bg-surface-raised/30 transition-colors"
          >
            {STATUS_ICON[c.status]}
            <div className="flex-1 min-w-0">
              <p className="text-secondary leading-relaxed">{c.description}</p>
              <div className="flex items-center gap-2 mt-1 text-[10px] text-muted">
                {c.evidence_count > 0 && (
                  <span className="flex items-center gap-0.5">
                    <Target className="h-2.5 w-2.5" />
                    {c.evidence_count} 条证据
                  </span>
                )}
                {c.agent_id && (
                  <span>Agent: {c.agent_id.slice(0, 8)}</span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
