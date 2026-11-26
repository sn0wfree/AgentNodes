import { Play, Pause, RotateCcw, Workflow } from 'lucide-react'

interface DAGToolbarProps {
  workflowName: string
  status: 'idle' | 'running' | 'paused' | 'completed' | 'failed'
  onStart?: () => void
  onPause?: () => void
  onResume?: () => void
  onReset?: () => void
}

const STATUS_LABEL = {
  idle: '就绪',
  running: '运行中',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
}

const STATUS_COLOR = {
  idle: 'text-muted',
  running: 'text-info-fg',
  paused: 'text-warning-fg',
  completed: 'text-success-fg',
  failed: 'text-danger-fg',
}

export function DAGToolbar({
  workflowName,
  status,
  onStart,
  onPause,
  onResume,
  onReset,
}: DAGToolbarProps) {
  return (
    <div className="wf-panel flex items-center justify-between border-b px-4 py-2.5">
      {/* Left: name + status */}
      <div className="flex items-center gap-3">
        <Workflow className="h-4 w-4 text-primary-400" />
        <span className="wf-text-main text-sm font-medium">{workflowName}</span>
        <span className={`text-xs ${STATUS_COLOR[status]}`}>
          {STATUS_LABEL[status]}
        </span>
      </div>

      {/* Right: controls */}
      <div className="flex items-center gap-1.5">
        {status === 'idle' && (
          <button
            onClick={onStart}
            className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 transition-colors"
          >
            <Play className="h-3.5 w-3.5" />
            启动
          </button>
        )}
        {status === 'running' && (
          <button
            onClick={onPause}
            className="flex items-center gap-1.5 rounded-lg bg-surface-raised px-3 py-1.5 text-xs text-foreground hover:bg-slate-600 transition-colors"
          >
            <Pause className="h-3.5 w-3.5" />
            暂停
          </button>
        )}
        {status === 'paused' && (
          <button
            onClick={onResume}
            className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 transition-colors"
          >
            <Play className="h-3.5 w-3.5" />
            恢复
          </button>
        )}
        {(status === 'completed' || status === 'failed') && (
          <button
            onClick={onReset}
            className="flex items-center gap-1.5 rounded-lg bg-surface-raised px-3 py-1.5 text-xs text-foreground hover:bg-slate-600 transition-colors"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            重置
          </button>
        )}
      </div>
    </div>
  )
}
