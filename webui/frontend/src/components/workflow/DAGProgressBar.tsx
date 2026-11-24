interface DAGProgressBarProps {
  progress: number
  completed: number
  total: number
  elapsed?: number
  error?: number
}

export function DAGProgressBar({
  progress,
  completed,
  total,
  elapsed,
  error,
}: DAGProgressBarProps) {
  return (
    <div className="wf-panel flex items-center gap-3 border-t px-4 py-2">
      {/* Progress bar */}
      <div className="flex-1 h-1.5 rounded-full bg-slate-800 overflow-hidden">
        <div
          className="h-full rounded-full bg-primary-500 transition-all duration-300"
          style={{ width: `${Math.min(100, progress)}%` }}
        />
      </div>

      {/* Stats */}
      <div className="wf-text-sub flex items-center gap-2 text-[11px]">
        <span className="font-mono">
          {completed}/{total}
        </span>
        <span className="font-mono">{Math.round(progress)}%</span>
        {error !== undefined && error > 0 && (
          <span className="font-mono text-danger-fg" title="失败的节点">
            ⚠ {error}
          </span>
        )}
        {elapsed !== undefined && (
          <span className="font-mono">
            {elapsed < 60
              ? `${elapsed}s`
              : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`}
          </span>
        )}
      </div>
    </div>
  )
}
