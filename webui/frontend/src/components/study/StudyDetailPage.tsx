import { useState, useCallback, useRef, useEffect } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  ArrowLeft, Pause, Play, X, FolderOpen,
  Target, Activity, RotateCcw, BarChart3, BookOpen,
  Archive, ArchiveRestore, Edit3,
  ChevronDown, ChevronRight, LineChart,
} from 'lucide-react'
import { api, type StudySummaryResponse, type StudyAvailableActionsResponse } from '../../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from './constants'
import { EmptyState } from '../common/EmptyState'
import { PageShell } from '../layout/PageShell'
import { EditObjectiveDialog } from './EditObjectiveDialog'
import { formatDateTime, clampRound } from './utils'
import { ContinueDialog } from './ContinueDialog'
import { AgentApprovalDialog } from './AgentApprovalDialog'
import { useSSE } from '../../hooks/useSSE'
import { StudyChat } from './dashboard/widgets/StudyChat'
import { MetricsCompare } from './MetricsCompare'
import { MetricsTrendChart } from './MetricsTrendChart'
import { RoundHistory } from './RoundHistory'

/** Statuses after which 10s summary polling stops (all terminal states). */
const TERMINAL_POLL_STATUSES = [
  'complete',
  'cancelled',
  'archived',
  'error',
  'budget_limited',
  'early_stopped',
  'needs_refresh',
]

function KpiCard({
  icon,
  iconCls,
  value,
  label,
  valueCls = 'text-slate-100',
}: {
  icon: React.ReactNode
  iconCls: string
  value: string
  label: string
  valueCls?: string
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3.5 shadow-soft transition-colors hover:border-slate-700">
      <div className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg ${iconCls}`}>
        {icon}
      </div>
      <div className="min-w-0">
        <div className={`font-mono text-xl font-bold tabular-nums ${valueCls}`}>{value}</div>
        <div className="text-[10px] text-slate-500">{label}</div>
      </div>
    </div>
  )
}

export function StudyDetailPage() {
  const { studyId = '' } = useParams<{ studyId: string }>()
  const navigate = useNavigate()
  const [summary, setSummary] = useState<StudySummaryResponse | null>(null)
  const [actions, setActions] = useState<StudyAvailableActionsResponse | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editObjectiveOpen, setEditObjectiveOpen] = useState(false)
  const [continueDialogOpen, setContinueDialogOpen] = useState(false)
  const [roundHistoryOpen, setRoundHistoryOpen] = useState(false)
  // 高-1: bumped after continue/action so a polling chain that stopped
  // on a terminal status restarts — otherwise the page froze on stale
  // data until a manual reload.
  const [pollGeneration, setPollGeneration] = useState(0)

  // Refs for ETag-conditional polling and terminal-state detection
  const summaryRef = useRef<StudySummaryResponse | null>(null)
  const etagRef = useRef<string | null>(null)

  const loadSummary = useCallback(async () => {
    try {
      const { data, etag } = await api.study.summaryWithEtag(studyId, etagRef.current ?? undefined)
      if (data) {
        setSummary(data)
        summaryRef.current = data
        etagRef.current = etag
        setNotFound(false)
        setError('')
      }
      // data === null → 304 Not Modified, no update needed
    } catch (err) {
      const status = (err as { status?: number })?.status
      if (status === 404) {
        setNotFound(true)
      } else {
        setError((err as Error).message)
      }
    }
  }, [studyId])

  const loadActions = useCallback(async () => {
    try {
      const r = await api.study.availableActions(studyId)
      setActions(r)
    } catch {
      setActions(null)
    }
  }, [studyId])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      await loadSummary()
      if (!cancelled) {
        setLoading(false)
        // Stop polling when study reaches a terminal status. error /
        // budget_limited / early_stopped / needs_refresh are terminal
        // too — polling them forever just burns requests.
        const st = summaryRef.current?.execution_status
        const isTerminal = TERMINAL_POLL_STATUSES.includes(st ?? '')
        timer = isTerminal ? null : setTimeout(poll, 10_000)
      }
    }

    void poll()
    void loadActions()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [
    studyId,
    loadActions,
    loadSummary,
    pollGeneration,
  ])

  // ── SSE connection for study events ────────────────────────────
  useSSE(studyId)

  const onAction = async (
    action: 'pause' | 'continue' | 'cancel' | 'archive' | 'unarchive',
    reason?: string,
  ) => {
    setBusy(true)
    try {
      await api.study.dispatchAction(studyId, action, reason ? { reason } : undefined)
      // Force the next summary fetch past the ETag/TTL cache — the
      // status just changed server-side.
      etagRef.current = null
      await loadActions()
      await loadSummary()
      setPollGeneration((g) => g + 1) // restart polling if it had stopped
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const onContinue = async (mode: 'resume' | 'restart', fromRound?: number) => {
    setBusy(true)
    try {
      await api.study.dispatchAction(studyId, 'continue', {
        mode: mode === 'restart' ? 'restart' : 'append',
        ...(fromRound ? { from_round: fromRound } : {}),
      })
      etagRef.current = null
      await loadActions()
      await loadSummary()
      setPollGeneration((g) => g + 1)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-app text-slate-400">
        <div className="flex items-center gap-2">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-primary-500" />
          加载中...
        </div>
      </div>
    )
  }

  if (notFound || !summary) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-app">
        <EmptyState
          icon={<FolderOpen className="h-10 w-10" />}
          title="研究任务不存在"
          description="该 study 可能已被删除，或链接不正确。"
        />
        <Link to="/study" className="text-sm text-primary-400 hover:text-primary-300 hover:underline">
          返回研究列表
        </Link>
      </div>
    )
  }

  const status = summary.execution_status ?? 'unknown'
  const strategyName = summary.strategy_name ?? ''
  const progressPercent = summary.goal_snapshot?.progress_percent ?? 0
  const evidenceCount = summary.goal_snapshot?.evidence_count ?? 0
  const lastVerdict = summary.last_verdict ?? '—'
  const metricTargets = summary.metric_targets ?? []

  const bestCalmar = (summary.recent_rounds ?? [])
    .map((r) => r.metrics?.calmar)
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
    .reduce((a, b) => Math.max(a, b), Number.NEGATIVE_INFINITY)
  const bestCalmarDisplay = Number.isFinite(bestCalmar) ? bestCalmar.toFixed(2) : '—'
  const driftCount = summary.monitor_state?.drift_count ?? 0
  const isDrifting = status === 'needs_refresh' || driftCount > 0

  const canPause = (actions?.actions ?? []).some((a) => a.name === 'pause')
  const canContinue = (actions?.actions ?? []).some((a) => a.name === 'continue')
  const canCancel = (actions?.actions ?? []).some((a) => a.name === 'cancel')
  const canArchive = (actions?.actions ?? []).some((a) => a.name === 'archive')
  const canUnarchive = (actions?.actions ?? []).some((a) => a.name === 'unarchive')
  const canReplaceObjective = (actions?.actions ?? []).some(
    (a) => a.name === 'replace_objective',
  )

  const controlActions = (
    <div className="flex items-center gap-1.5">
      <button
        onClick={() => navigate(-1)}
        className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-700 bg-slate-800/50 px-2 py-1.5 text-xs text-slate-400 transition-colors hover:border-slate-600 hover:text-slate-200 active:scale-95"
        title="返回"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> 返回
      </button>
      {canPause && (
        <button
          onClick={() => onAction('pause')}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-warning px-2.5 py-1.5 text-xs text-white transition-all hover:bg-warning-fill active:scale-95 disabled:opacity-50"
        >
          <Pause className="h-3.5 w-3.5" /> 暂停
        </button>
      )}
      {canContinue && (
        <button
          onClick={() => setContinueDialogOpen(true)}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-emerald-500 active:scale-95 disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" /> 继续
        </button>
      )}
      {canCancel && (
        <button
          onClick={() => {
            if (window.confirm('确定中止此研究？中止后可从 Round 1 重新开始，当前进度将保留但不再继续。')) {
              void onAction('cancel')
            }
          }}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-rose-700 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-rose-600 active:scale-95 disabled:opacity-50"
        >
          <X className="h-3.5 w-3.5" /> 中止
        </button>
      )}
      {canArchive && (
        <button
          onClick={() => {
            if (window.confirm('确定归档此研究？归档后默认列表不再显示，可在「显示已归档」中查看。')) {
              void onAction('archive')
            }
          }}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-600/40 bg-slate-700/20 px-2.5 py-1.5 text-xs text-slate-300 transition-all hover:bg-amber-700/40 hover:text-amber-50 active:scale-95 disabled:opacity-50"
        >
          <Archive className="h-3.5 w-3.5" /> 归档
        </button>
      )}
      {canUnarchive && (
        <button
          onClick={() => {
            if (window.confirm('取消归档后，状态将变为「已中断」，可手动恢复运行。继续？')) {
              void onAction('unarchive')
            }
          }}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-sky-600/40 bg-sky-700/20 px-2.5 py-1.5 text-xs text-sky-200 transition-all hover:bg-sky-700/40 hover:text-sky-50 active:scale-95 disabled:opacity-50"
        >
          <ArchiveRestore className="h-3.5 w-3.5" /> 取消归档
        </button>
      )}
      {canReplaceObjective && (
        <button
          onClick={() => setEditObjectiveOpen(true)}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-indigo-500/40 bg-indigo-500/15 px-2.5 py-1.5 text-xs text-indigo-200 transition-all hover:bg-indigo-500/30 hover:text-indigo-50 active:scale-95 disabled:opacity-50"
        >
          <Edit3 className="h-3.5 w-3.5" /> 修改目标
        </button>
      )}
    </div>
  )

  return (
    <PageShell
      title={summary.objective || '研究详情'}
      subtitle={`策略 ${strategyName || '—'} · 创建于 ${formatDateTime(summary.created_at)}`}
      icon={<BookOpen className="h-4 w-4" />}
      actions={controlActions}
    >
      {/* Error banner */}
      {error && (
        <div className="mb-4 rounded-xl border border-rose-800 bg-rose-950/50 px-3 py-2 text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* KPI band */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
        <KpiCard
          icon={<RotateCcw className="h-4 w-4" />}
          iconCls="border border-sky-500/30 bg-sky-500/10 text-sky-400"
          value={`${clampRound(summary.current_round)}/${summary.max_rounds ?? 5}`}
          label="当前轮次 / 最大轮数"
          valueCls="text-sky-400"
        />
        <KpiCard
          icon={<Activity className="h-4 w-4" />}
          iconCls="border border-warning/30 bg-warning-fill text-warning-fg"
          value={STUDY_STATUS_LABELS[status] ?? status}
          label={isDrifting ? `漂移 ×${driftCount}（需检查）` : '执行状态'}
          valueCls={STUDY_STATUS_COLORS[status]?.split(' ').find((c: string) => c.startsWith('text-')) ?? 'text-slate-100'}
        />
        <KpiCard
          icon={<BarChart3 className="h-4 w-4" />}
          iconCls="border border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
          value={lastVerdict}
          label="最近 round 结论"
          valueCls="text-emerald-400"
        />
        <KpiCard
          icon={<Target className="h-4 w-4" />}
          iconCls="border border-primary-500/30 bg-primary-500/10 text-primary-400"
          value={`${progressPercent}%`}
          label={`目标进度 · ${evidenceCount} 证据`}
          valueCls="text-primary-400"
        />
        <KpiCard
          icon={<BarChart3 className="h-4 w-4" />}
          iconCls="border border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
          value={bestCalmarDisplay}
          label="最佳 Calmar（历史轮次）"
          valueCls="text-emerald-400"
        />
      </div>

      {/* Metric targets */}
      {metricTargets.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
            验收线:
          </span>
          {metricTargets.map((t, i) => (
            <span
              key={i}
              className="rounded-full border border-primary-500/30 bg-primary-500/10 px-2 py-0.5 font-mono text-[10px] text-primary-400"
            >
              {t.name} {t.op} {t.value}
            </span>
          ))}
        </div>
      )}

      {/* Main content: left StudyChat + right panel */}
      <div className="mt-4 flex min-h-0 flex-1 gap-4" style={{ height: 'calc(100vh - 280px)' }}>
        {/* Left: StudyChat */}
        <div className="min-h-0 min-w-0 flex-1 overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
          <StudyChat studyId={studyId} summary={summary} />
        </div>

        {/* Right panel */}
        <div className="flex w-80 flex-shrink-0 flex-col gap-3">
          {/* MetricsCompare */}
          <div className="min-h-0 flex-shrink-0 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
              <BarChart3 className="h-3 w-3" /> 指标对比
            </div>
            <div className="max-h-52 overflow-y-auto">
              <MetricsCompare rounds={summary.recent_rounds ?? []} />
            </div>
          </div>

          {/* Metrics trend chart */}
          <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-slate-800 bg-slate-900/40 p-3">
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
              <LineChart className="h-3 w-3" /> 指标趋势
            </div>
            <div className="min-h-0 flex-1">
              <MetricsTrendChart
                rounds={summary.recent_rounds ?? []}
                metricTargets={metricTargets}
              />
            </div>
          </div>

          {/* RoundHistory — collapsible */}
          <div className="min-h-0 flex-shrink-0 rounded-xl border border-slate-800 bg-slate-900/40">
            <button
              onClick={() => setRoundHistoryOpen((v) => !v)}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-slate-500 transition-colors hover:bg-slate-800/40"
            >
              {roundHistoryOpen ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              <RotateCcw className="h-3 w-3" /> 轮次历史
              <span className="ml-auto text-slate-600">{summary.recent_rounds?.length ?? 0} 轮</span>
            </button>
            {roundHistoryOpen && (
              <div className="max-h-60 overflow-y-auto border-t border-slate-800 px-3 py-2">
                <RoundHistory
                  rounds={summary.recent_rounds ?? []}
                  currentRound={summary.current_round}
                  studyId={studyId}
                />
              </div>
            )}
          </div>
        </div>
      </div>

      <EditObjectiveDialog
        studyId={studyId}
        currentObjective={summary.objective}
        goalId={summary.goal_snapshot?.goal_id ?? null}
        open={editObjectiveOpen}
        onClose={() => setEditObjectiveOpen(false)}
        onSuccess={() => {
          // Refresh summary so the new objective + history show immediately
          void loadSummary()
          void loadActions()
        }}
      />

      <ContinueDialog
        open={continueDialogOpen}
        summary={summary}
        onClose={() => setContinueDialogOpen(false)}
        onContinue={onContinue}
      />

      <AgentApprovalDialog studyId={studyId} />
    </PageShell>
  )
}
