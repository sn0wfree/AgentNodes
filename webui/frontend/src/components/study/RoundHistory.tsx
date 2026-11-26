import { useState } from 'react'
import { ChevronRight, ChevronDown, AlertTriangle, ExternalLink } from 'lucide-react'
import type { StudyRoundSummary } from '../../api/client'
import { RoundDetailDrawer } from './RoundDetailDrawer'

interface Props {
  rounds: StudyRoundSummary[]
  currentRound: number
  /** When provided, renders a per-round link to the run detail page. */
  onOpenRun?: (runName: string) => void
  /** When provided, clicking a round row opens the detail drawer. */
  studyId?: string
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  } catch {
    return '--:--'
  }
}

const VERDICT_STYLES: Record<string, { badge: string; label: string; dot: string; line: string }> = {
  keep: {
    badge: 'border-success-fg/40 bg-success-surface text-success-fg',
    label: '保留',
    dot: 'border-success-fg bg-success',
    line: 'bg-success/40',
  },
  discard: {
    badge: 'border-strong bg-surface-raised/70 text-muted',
    label: '弃用',
    dot: 'border-strong bg-surface-raised',
    line: 'bg-surface-raised',
  },
  review: {
    badge: 'border-warning/40 bg-warning-fill text-warning-fg',
    label: '待复核',
    dot: 'border-warning bg-warning',
    line: 'bg-warning/40',
  },
}

function MetricValue({ label, value }: { label: string; value?: number | null }) {
  const display = value != null ? value.toFixed(2) : '—'
  return (
    <span className="font-mono text-[10px] tabular-nums">
      <span className="text-muted">{label}:</span>{' '}
      <span className="text-secondary">{display}</span>
    </span>
  )
}

function RoundItem({
  round,
  isCurrent,
  isLast,
  onOpenRun,
  onOpenDetail,
}: {
  round: StudyRoundSummary
  isCurrent: boolean
  isLast: boolean
  onOpenRun?: (runName: string) => void
  onOpenDetail?: (round: StudyRoundSummary) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const hasFailures = round.factor_failures && round.factor_failures.length > 0
  const verdict = round.verdict ?? '—'
  const verdictStyle = VERDICT_STYLES[verdict] ?? {
    badge: 'border-strong bg-surface-raised text-muted',
    label: verdict,
    dot: 'border-strong bg-surface-raised',
    line: 'bg-surface-raised',
  }

  return (
    <div className="flex gap-2.5">
      {/* Timeline axis */}
      <div className="flex flex-col items-center">
        <div
          className={`mt-1 h-2.5 w-2.5 flex-shrink-0 rounded-full border-2 ${verdictStyle.dot} ${
            isCurrent ? 'animate-pulse ring-2 ring-sky-500/40' : ''
          }`}
          title={verdictStyle.label}
        />
        {!isLast && <div className={`w-0.5 flex-1 ${verdictStyle.line}`} />}
      </div>

      {/* Round content */}
      <div className="min-w-0 flex-1 pb-3">
        <div
          className={`flex items-center gap-1 rounded-lg transition-colors hover:bg-surface-raised/30 ${
            isCurrent ? 'bg-surface ring-1 ring-slate-700' : ''
          }`}
        >
          <button
            type="button"
            onClick={() => (onOpenDetail ? onOpenDetail(round) : setExpanded(!expanded))}
            aria-expanded={expanded}
            className="flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 py-1.5 pl-1.5 pr-1 text-left"
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3 flex-shrink-0 text-muted" />
            ) : (
              <ChevronRight className="h-3 w-3 flex-shrink-0 text-muted" />
            )}
            <span
              className={`w-6 flex-shrink-0 font-mono text-[10px] ${
                isCurrent ? 'text-primary-400' : 'text-muted'
              }`}
            >
              R{round.round_num}
            </span>
            <span className="w-10 flex-shrink-0 text-[10px] text-muted">
              {formatTime(round.created_at)}
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-muted">
              {round.run_name}
            </span>
            <span
              className={`flex-shrink-0 rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${verdictStyle.badge}`}
              title={`verdict: ${verdict}（${verdictStyle.label}）`}
            >
              {verdict}
              {round.verdict === 'keep' && ' ✓'}
            </span>
            <MetricValue label="C" value={round.metrics?.calmar} />
            <MetricValue label="S" value={round.metrics?.sharpe} />
            <MetricValue label="D" value={round.metrics?.max_dd} />
            {hasFailures && (
              <AlertTriangle className="h-3 w-3 flex-shrink-0 text-warning-fg" />
            )}
          </button>
          {onOpenRun && (
            <button
              type="button"
              title="查看回测产物"
              onClick={() => onOpenRun(round.run_name)}
              className="mr-1 flex-shrink-0 cursor-pointer rounded-lg p-1.5 text-muted transition-colors hover:bg-surface-raised hover:text-info-fg"
            >
              <ExternalLink className="h-3 w-3" />
            </button>
          )}
        </div>

        {expanded && (
          <div className="ml-3 mt-1 space-y-2 border-l-2 border-strong pl-2.5">
            {/* Factor failures */}
            {hasFailures && (
              <div className="space-y-1.5">
                {round.factor_failures!.map((f, i) => (
                  <div key={i} className="rounded-lg border border-warning/30 bg-warning-fill px-2 py-1.5 text-[10px]">
                    <div className="flex items-center gap-1.5 text-warning-fg">
                      <AlertTriangle className="h-3 w-3" />
                      <span className="font-mono">{f.factor_code}</span>
                    </div>
                    <p className="text-muted ml-4">{f.error}</p>
                    {f.available_columns && (
                      <p className="text-muted ml-4">
                        可用列: [{f.available_columns.join(', ')}]
                      </p>
                    )}
                    {f.suggested_fix && (
                      <p className="text-info-fg ml-4">建议: {f.suggested_fix}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export function RoundHistory({ rounds, currentRound, onOpenRun, studyId }: Props) {
  const [detailRound, setDetailRound] = useState<StudyRoundSummary | null>(null)

  if (rounds.length === 0) {
    return (
      <div className="rounded-xl border border-subtle bg-surface-raised p-3.5 shadow-soft">
        <div className="text-[10px] font-medium uppercase tracking-wider text-muted mb-1">
          Round 历史
        </div>
        <p className="text-xs text-muted">暂无历史记录</p>
      </div>
    )
  }

  return (
    <>
      <div className="rounded-xl border border-subtle bg-surface-raised p-3.5 shadow-soft transition-colors hover:border-strong">
        <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted">
          Round 历史 · 时间线
        </div>
        <div className="space-y-0">
          {rounds.map((round, i) => (
            <RoundItem
              key={round.round_num}
              round={round}
              isCurrent={round.round_num === currentRound}
              isLast={i === rounds.length - 1}
              onOpenRun={onOpenRun}
              onOpenDetail={studyId ? () => setDetailRound(round) : undefined}
            />
          ))}
        </div>
      </div>
      {studyId && detailRound && (
        <RoundDetailDrawer
          studyId={studyId}
          round={detailRound}
          onClose={() => setDetailRound(null)}
        />
      )}
    </>
  )
}
