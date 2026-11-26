import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, ArrowRight, RefreshCw, ListChecks, Archive } from 'lucide-react'
import { api, type StudySummary } from '../../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from './constants'
import { StudyActionMenu } from './StudyActionMenu'
import { formatDateTime, clampRound } from './utils'

const ACTIVE_STATUSES = ['running', 'queued', 'monitoring', 'paused', 'interrupted', 'budget_limited']

type FilterKey = 'all' | 'active' | 'complete'

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'active', label: '进行中' },
  { key: 'complete', label: '已完成' },
]

function HistoryCard({
  study,
  selected,
  onClick,
  onDoubleClick,
  onAction,
}: {
  study: StudySummary
  selected: boolean
  onClick: () => void
  onDoubleClick?: () => void
  onAction: (action: 'pause' | 'continue' | 'cancel' | 'archive' | 'unarchive') => void
}) {
  const status = study.execution_status ?? 'unknown'
  const isActive = ACTIVE_STATUSES.includes(status)
  const lastMetrics = study.last_metrics
  const fmtMetric = (key: string) => {
    const v = lastMetrics?.[key]
    return v != null ? Number(v).toFixed(2) : null
  }
  const c = fmtMetric('calmar')
  const s = fmtMetric('sharpe')
  const d = fmtMetric('max_dd')
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      aria-pressed={selected}
      className={`group relative w-full cursor-pointer rounded-xl border p-3 text-left shadow-soft transition-all duration-200 active:scale-[0.99] ${
        selected
          ? 'border-primary-500/60 bg-primary-500/5 ring-1 ring-primary-500/30'
          : 'border-subtle bg-surface-raised hover:translate-x-0.5 hover:border-strong hover:bg-surface-raised hover:shadow-elevated'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className={`min-w-0 truncate text-xs font-medium ${selected ? 'text-primary-300' : 'text-foreground'}`}>
          {study.objective || '未命名研究'}
        </span>
        <div className="flex items-center gap-1">
          <StudyActionMenu study={study} onAction={onAction} />
          <ArrowRight
            className={`h-3 w-3 flex-shrink-0 ${selected ? 'text-primary-400' : 'text-muted'}`}
          />
        </div>
      </div>
      {study.strategy_name && (
        <span className="mt-1 inline-block font-mono text-[10px] text-primary-400/90">
          {study.strategy_name}
        </span>
      )}
      {(c != null || s != null || d != null) && (
        <div className="mt-1.5 flex items-center gap-2 font-mono text-[9px] tabular-nums text-muted">
          {s != null && <span className="text-info-fg">S {s}</span>}
          {c != null && <span className="text-primary-400/80">C {c}</span>}
          {d != null && <span className="text-danger-fg">DD {d}</span>}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[9px] font-medium ${
            STUDY_STATUS_COLORS[status] ?? 'border-strong bg-surface-raised text-secondary'
          }`}
        >
          {isActive && (
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
          )}
          {STUDY_STATUS_LABELS[status] ?? status}
        </span>
        {study.last_verdict && (
          <span
            className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${
              study.last_verdict === 'keep'
                ? 'border-success-fg/40 bg-success-surface text-success-fg'
                : 'border-strong bg-surface-raised/70 text-muted'
            }`}
          >
            {study.last_verdict}
            {study.last_verdict === 'keep' && ' ✓'}
          </span>
        )}
        <span className="text-[10px] text-muted">Round {clampRound(study.current_round)}</span>
        <span className="inline-flex items-center gap-0.5 text-[10px] text-muted">
          <Clock className="h-2.5 w-2.5" />
          {formatDateTime(study.updated_at ?? study.created_at)}
        </span>
      </div>
    </div>
  )
}

interface Props {
  studies: StudySummary[]
  selectedId: string | null
  loading?: boolean
  onSelect: (study: StudySummary) => void
  onRefresh?: () => void
  includeArchived?: boolean
  onToggleArchived?: (v: boolean) => void
  onAction?: (studyId: string, action: 'pause' | 'continue' | 'cancel' | 'archive' | 'unarchive') => void
}

export function StudyTaskList({
  studies,
  selectedId,
  loading,
  onSelect,
  onRefresh,
  includeArchived = false,
  onToggleArchived,
  onAction,
}: Props) {
  const navigate = useNavigate()
  const [filter, setFilter] = useState<FilterKey>('all')

  const handleAction = async (
    study: StudySummary,
    action: 'pause' | 'continue' | 'cancel' | 'archive' | 'unarchive',
  ) => {
    if (action === 'cancel' && !window.confirm(`确定中止「${study.objective.slice(0, 30)}」？中止后可从 Round 1 重新开始。`)) {
      return
    }
    if (action === 'archive' && !window.confirm('确定归档此研究？归档后默认列表不再显示。')) {
      return
    }
    if (action === 'unarchive' && !window.confirm('取消归档后状态将变为「已中断」，可手动恢复。继续？')) {
      return
    }
    try {
      await api.study.dispatchAction(study.study_id, action)
      onAction?.(study.study_id, action)
      onRefresh?.()
    } catch (err) {
      console.error('study action failed:', err)
    }
  }

  const sorted = useMemo(() => {
    const list = [...studies].sort((a, b) => {
      const aActive = ACTIVE_STATUSES.includes(a.execution_status)
      const bActive = ACTIVE_STATUSES.includes(b.execution_status)
      if (aActive !== bActive) return aActive ? -1 : 1
      return String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? ''))
    })
    if (filter === 'active') return list.filter((s) => ACTIVE_STATUSES.includes(s.execution_status))
    if (filter === 'complete') return list.filter((s) => !ACTIVE_STATUSES.includes(s.execution_status))
    return list
  }, [studies, filter])

  return (
    <div className="flex h-full min-h-0 flex-col gap-2.5">
      {/* Header + filters */}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
          <ListChecks className="h-3.5 w-3.5 text-primary-400" />
          任务列表
        </h2>
        <span className="font-mono text-[10px] text-muted">{studies.length}</span>
        <div className="ml-auto flex items-center gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`cursor-pointer rounded-full border px-2 py-0.5 text-[9px] font-medium transition-colors ${
                filter === f.key
                  ? 'border-primary-500/50 bg-primary-500/15 text-primary-300'
                  : 'border-strong bg-surface text-muted hover:border hover:text-secondary'
              }`}
            >
              {f.label}
            </button>
          ))}
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            title="刷新"
            className="ml-1 cursor-pointer rounded-lg p-1.5 text-muted transition-colors hover:bg-surface-raised hover:text-secondary active:scale-95 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        {onToggleArchived && (
          <label className="flex cursor-pointer items-center gap-1.5 text-[10px] text-muted">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => onToggleArchived(e.target.checked)}
              className="h-3 w-3 cursor-pointer rounded border bg-surface-raised text-muted focus:ring-slate-400"
            />
            <Archive className="h-3 w-3" />
            显示已归档
          </label>
        )}
      </div>

      {/* List */}
      {loading && sorted.length === 0 ? (
        <div className="space-y-2">
          <div className="animate-pulse rounded-xl border border-subtle bg-surface p-3">
            <div className="h-3 w-2/3 rounded bg-surface-raised/70" />
            <div className="mt-2 h-2.5 w-1/2 rounded bg-surface" />
          </div>
          <div className="animate-pulse rounded-xl border border-subtle bg-surface p-3">
            <div className="h-3 w-2/3 rounded bg-surface-raised/70" />
            <div className="mt-2 h-2.5 w-1/2 rounded bg-surface" />
          </div>
        </div>
      ) : sorted.length === 0 ? (
        <div className="rounded-xl border border-dashed border-subtle px-4 py-10 text-center text-xs text-muted">
          {filter === 'all' ? '暂无研究任务 —— 在左侧创建一个吧' : '该状态下暂无任务'}
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {sorted.map((s) => (
            <HistoryCard
              key={s.study_id}
              study={s}
              selected={s.study_id === selectedId}
              onClick={() => onSelect(s)}
              onDoubleClick={() => navigate(`/study/${s.study_id}`)}
              onAction={(action) => handleAction(s, action)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
