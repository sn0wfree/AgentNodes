export const STUDY_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  paused: '已暂停',
  interrupted: '已中断',
  error: '错误',
  complete: '已完成',
  cancelled: '已取消',
  budget_limited: '预算受限',
  monitoring: '监控中',
  needs_refresh: '需刷新证据',
  early_stopped: '提前停止',
  archived: '已归档',
}

export const STUDY_STATUS_COLORS: Record<string, string> = {
  queued: 'bg-slate-700 text-slate-200',
  running: 'bg-sky-700 text-sky-100',
  paused: 'bg-warning text-warning-fg',
  interrupted: 'bg-orange-700 text-orange-100',
  error: 'bg-rose-700 text-rose-100',
  complete: 'bg-emerald-700 text-emerald-100',
  cancelled: 'bg-slate-700 text-slate-300',
  budget_limited: 'bg-orange-700 text-orange-100',
  monitoring: 'bg-indigo-700 text-indigo-100',
  needs_refresh: 'bg-rose-800 text-rose-100',
  early_stopped: 'bg-rose-800 text-rose-100',
  archived: 'bg-slate-800 text-slate-500 line-through',
}
