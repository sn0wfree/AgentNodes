import { useEffect, useState, useRef, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Clock, FolderOpen, Target, User, ExternalLink } from 'lucide-react'
import { api, type StudySummaryResponse } from '../../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from './constants'
import { MetricsCompare } from './MetricsCompare'
import { StudyActionMenu } from './StudyActionMenu'
import { EmptyState } from '../common/EmptyState'
import { formatDateTime, clampRound } from './utils'

interface Props {
  studyId: string | null
}

function MetaRow({ icon, label, value, title }: { icon: React.ReactNode; label: string; value: string; title?: string }) {
  return (
    <div className="flex items-center gap-2 text-[10px]">
      <span className="flex w-14 flex-shrink-0 items-center gap-1 text-muted">
        {icon}
        {label}
      </span>
      <span className="min-w-0 truncate font-mono text-secondary" title={title ?? value}>
        {value}
      </span>
    </div>
  )
}

export function StudyTaskSummary({ studyId }: Props) {
  const navigate = useNavigate()
  const [summary, setSummary] = useState<StudySummaryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  // 高-1: restarts the polling chain after a terminal status + action
  // (e.g. continue on an errored study) — otherwise the panel froze.
  const [pollGeneration, setPollGeneration] = useState(0)
  const summaryRef = useRef<StudySummaryResponse | null>(null)
  const etagRef = useRef<string | null>(null)

  const run = useCallback(async () => {
    if (!studyId) return
    setLoading(true)
    setError('')
    try {
      const { data, etag } = await api.study.summaryWithEtag(studyId, etagRef.current ?? undefined)
      if (data) {
        setSummary(data)
        summaryRef.current = data
        etagRef.current = etag
      }
      // data === null → 304, no update needed
    } catch (err) {
      setError((err as Error).message)
      setSummary(null)
    } finally {
      setLoading(false)
    }
  }, [studyId])

  useEffect(() => {
    // 中-8: clear the previous study's summary immediately — the skeleton
    // condition `loading && !summary` is otherwise short-circuited and
    // the OLD study's data renders until (or after) the fetch lands.
    setSummary(null)
    summaryRef.current = null
    etagRef.current = null
    setError('')
    if (!studyId) {
      return
    }
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      await run()
      if (!cancelled) {
        const st = summaryRef.current?.execution_status
        const isTerminal = [
          'complete', 'cancelled', 'archived', 'error',
          'budget_limited', 'early_stopped', 'needs_refresh',
        ].includes(st ?? '')
        timer = isTerminal ? null : setTimeout(poll, 10_000)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [studyId, run, pollGeneration])

  if (!studyId) {
    return (
      <div className="flex h-full flex-col items-center justify-center rounded-xl border border-dashed border-subtle bg-background/30 p-6">
        <EmptyState
          icon={<Target className="h-8 w-8" />}
          title="选择任务查看摘要"
          description="点击中间列表中的任务，这里显示简略信息"
        />
      </div>
    )
  }

  const status = summary?.execution_status ?? 'unknown'
  const progressPercent = summary?.goal_snapshot?.progress_percent ?? 0
  const evidenceCount = summary?.goal_snapshot?.evidence_count ?? 0

  return (
    <div className="flex h-full min-h-0 flex-col gap-2.5">
      {/* Header with fixed button - always visible */}
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
          <Target className="h-3.5 w-3.5 text-primary-400" />
          任务摘要
        </h2>
        {summary && (
          <div className="flex items-center gap-1">
            <StudyActionMenu
              study={summary}
              onAction={async (action) => {
                if (action === 'cancel' && !window.confirm('确定中止此研究？中止后可从 Round 1 重新开始。')) return
                if (action === 'archive' && !window.confirm('确定归档此研究？归档后默认列表不再显示。')) return
                if (action === 'unarchive' && !window.confirm('取消归档后状态将变为「已中断」，可手动恢复。继续？')) return
                try {
                  await api.study.dispatchAction(summary.study_id, action)
                  etagRef.current = null // force re-fetch after action
                  void run()
                  setPollGeneration((g) => g + 1) // restart polling (高-1)
                } catch (err) {
                  setError((err as Error).message)
                }
              }}
            />
            <Link
              to={`/study/${summary.study_id}`}
              onClick={(e) => e.stopPropagation()}
              className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-primary-500/40 bg-primary-500/10 px-2 py-1 text-[10px] font-medium text-primary-300 transition-colors hover:bg-primary-500/20 hover:text-primary-200"
            >
              <ExternalLink className="h-3 w-3" />
              查看详情
            </Link>
          </div>
        )}
      </div>

      {loading && !summary ? (
        <div className="animate-pulse space-y-2">
          <div className="h-3 w-3/4 rounded bg-surface-raised/70" />
          <div className="h-2.5 w-1/2 rounded bg-surface" />
          <div className="h-20 rounded-lg bg-surface" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-rose-800 bg-rose-950/50 p-3 text-xs text-rose-300">
          {error}
        </div>
      ) : !summary ? (
        <div className="rounded-xl border border-subtle bg-surface p-3 text-xs text-muted">
          加载中…
        </div>
      ) : (
        <div
          className="flex flex-1 cursor-pointer flex-col gap-2.5 rounded-xl border border-subtle bg-surface-raised p-3.5 shadow-soft transition-all hover:border-primary-500/40 hover:bg-surface-raised hover:shadow-elevated"
          onClick={() => summary && navigate(`/study/${summary.study_id}`)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              summary && navigate(`/study/${summary.study_id}`)
            }
          }}
        >
          {/* Objective + badges */}
          <div>
            <p className="line-clamp-2 text-xs font-medium leading-relaxed text-foreground">
              {summary!.objective || '未命名研究'}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[9px] font-medium ${
                  STUDY_STATUS_COLORS[status] ?? 'border-strong bg-surface-raised text-secondary'
                }`}
              >
                {ACTIVE_PULSE.has(status) && (
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                )}
                {STUDY_STATUS_LABELS[status] ?? status}
              </span>
              {summary!.last_verdict && (
                <span
                  className={`rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${
                    summary!.last_verdict === 'keep'
                      ? 'border-success-fg/40 bg-success-surface text-success-fg'
                      : 'border-strong bg-surface-raised/70 text-muted'
                  }`}
                >
                  {summary!.last_verdict}
                  {summary!.last_verdict === 'keep' && ' ✓'}
                </span>
              )}
            </div>

            {/* Progress */}
            <div className="mt-3">
              <div className="flex items-center justify-between text-[10px] text-muted">
                <span>目标进度</span>
                <span className="font-mono tabular-nums text-muted">
                  {progressPercent}% · {evidenceCount} 证据
                </span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-raised/80">
                <div
                  className="h-full bg-accent-500 transition-all duration-500"
                  style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }}
                />
              </div>
              <p className="mt-1.5 text-[10px] text-muted">
                Round {clampRound(summary!.current_round)}/{summary!.max_rounds ?? 5}
              </p>
            </div>
          </div>

          {/* Metrics compare */}
          <MetricsCompare rounds={summary!.recent_rounds ?? []} />

          {/* Meta */}
          <div className="space-y-1.5">
            <MetaRow
              icon={<User className="h-3 w-3" />}
              label="策略"
              value={summary!.strategy_name || '—'}
            />
            <MetaRow
              icon={<FolderOpen className="h-3 w-3" />}
              label="工作区"
              value={summary!.workspace_path || '—'}
              title={summary!.workspace_path}
            />
            <MetaRow icon={<Clock className="h-3 w-3" />} label="创建" value={formatDateTime(summary!.created_at)} />
            <MetaRow icon={<Clock className="h-3 w-3" />} label="更新" value={formatDateTime(summary!.updated_at)} />
          </div>

          {/* Click hint */}
          <div className="mt-auto flex items-center justify-center gap-1 border-t border-subtle/60 pt-2 text-[10px] text-muted">
            <span>点击查看详情</span>
            <ArrowRight className="h-3 w-3" />
          </div>
        </div>
      )}
    </div>
  )
}

const ACTIVE_PULSE = new Set(['running', 'queued', 'monitoring'])
