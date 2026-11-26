import { Target } from 'lucide-react'
import { GoalTab, type GoalTabGoal } from './GoalTab'

interface GoalCardProps {
  goal: GoalTabGoal | null
}

/**
 * Right-panel card for goal & progress (passive tracking).
 *
 * Pure GoalTab container — does NOT embed the performance curve. The
 * 「表现曲线」 card is rendered separately by `PanelRenderCard`, so the
 * goal card and the performance card each show exactly one header.
 * docs/right-panel-agent-driven.md
 */
export function GoalCard({ goal }: GoalCardProps) {
  return (
    <div className="rounded-lg border border-subtle bg-background/30 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted">
        <Target className="h-3 w-3" />
        <span>目标 &amp; 进度</span>
      </div>
      <GoalTab goal={goal} />
    </div>
  )
}