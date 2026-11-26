import { Target } from 'lucide-react'
import { CriteriaList } from './CriteriaList'
import { GoalTimeline } from './GoalTimeline'
import { EmptyState } from '../common/EmptyState'
import { statusLabel } from '../../utils/status'

// TODO(architecture): this is a DISPLAY model (id/title/description/
// timeline) distinct from the store model in stores/goal.ts
// (goal_id/objective/criteria) — RightPanel converts between them by
// hand. Once the goal panel is wired to real data (see the goal_* SSE
// TODO in useSSE.ts), merge the two models instead of duplicating.
export interface GoalTabGoal {
  id: string
  title: string
  description: string
  status: 'active' | 'completed' | 'failed'
  criteria: Array<{
    id: string
    description: string
    status: 'pending' | 'in_progress' | 'completed'
    evidence_count: number
    agent_id?: string
  }>
  timeline: Array<{
    id: string
    type: 'evidence' | 'iteration' | 'message' | 'system'
    content: string
    timestamp: number
    agent_id?: string
  }>
}

interface GoalTabProps {
  goal: GoalTabGoal | null
}

export function GoalTab({ goal }: GoalTabProps) {
  if (!goal) {
    return (
      <EmptyState
        icon={<Target className="h-10 w-10" />}
        title="暂无活跃目标"
        description="使用 /goal 命令创建研究目标"
      />
    )
  }

  const totalCriteria = goal.criteria.length

  return (
    <div className="space-y-6">
      {/* Goal header */}
      <div>
        <h3 className="text-sm font-semibold text-foreground mb-1">{goal.title}</h3>
        {goal.description && (
          <p className="text-xs text-muted leading-relaxed">{goal.description}</p>
        )}
        <div className="flex items-center gap-2 mt-2">
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium
              ${goal.status === 'active'
                ? 'bg-emerald-900/50 text-success-fg'
                : goal.status === 'completed'
                ? 'bg-blue-900/50 text-blue-300'
                : 'bg-red-900/50 text-danger-fg'
              }
            `}
          >
            {statusLabel(goal.status)}
          </span>
        </div>
      </div>

      {/* Criteria */}
      <div>
        <h4 className="text-xs font-medium text-muted mb-2">研究标准</h4>
        <CriteriaList criteria={goal.criteria} totalCriteria={totalCriteria} />
      </div>

      {/* Timeline */}
      <div>
        <h4 className="text-xs font-medium text-muted mb-2">活动时间线</h4>
        <GoalTimeline events={goal.timeline} />
      </div>
    </div>
  )
}
