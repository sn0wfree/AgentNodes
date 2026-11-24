/**
 * Shared status → label / badge-class mapping.
 *
 * The 中文 labels and pill colors for agent / DAG / goal / tool status
 * were previously duplicated in 5 components (DAGNode, DAGNodeDetail,
 * AgentItem, GoalTab, ToolCallBlock). This is the single source of
 * truth; components that need icons keep their own icon mapping.
 */

export const STATUS_LABELS: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
  aborted: '已中止',
  done: '已完成',
  error: '失败',
  active: '进行中',
  success: '成功',
}

/** Human-readable label for a status key (falls back to the raw key). */
export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

const BADGE_CLASSES: Record<string, string> = {
  pending: 'bg-slate-500/20 text-slate-400',
  running: 'bg-info-surface text-info-fg',
  completed: 'bg-success-surface text-success-fg',
  done: 'bg-success-surface text-success-fg',
  success: 'bg-success-surface text-success-fg',
  failed: 'bg-danger-surface text-danger-fg',
  error: 'bg-danger-surface text-danger-fg',
  skipped: 'bg-slate-600/20 text-slate-500',
  aborted: 'bg-warning-fill text-warning-fg',
  active: 'bg-success-surface text-success-fg',
}

/** Tailwind pill classes for a status key. */
export function statusBadgeClass(status: string): string {
  return BADGE_CLASSES[status] ?? 'bg-slate-500/20 text-slate-400'
}
