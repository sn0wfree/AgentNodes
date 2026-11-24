import { BarChart3, ExternalLink } from 'lucide-react'
import type { StudyRoundSummary } from '../../api/client'

interface Props {
  rounds: StudyRoundSummary[]
  /** When provided, renders a per-round link to the run detail page. */
  onOpenRun?: (runName: string) => void
}

const METRIC_KEYS = ['calmar', 'sharpe', 'max_dd'] as const

const VERDICT_DOT: Record<string, string> = {
  keep: 'bg-emerald-500',
  discard: 'bg-slate-600',
  review: 'bg-warning',
}

function fmt(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return v.toFixed(2)
}

/**
 * Per-round metrics comparison table (W&B runs-table pattern):
 * one row per round, best value in each column highlighted.
 */
export function MetricsCompare({ rounds, onOpenRun }: Props) {
  const withMetrics = rounds.filter((r) => r.metrics != null)
  if (withMetrics.length === 0) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft">
        <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <BarChart3 className="h-3 w-3" />
          指标对比
        </div>
        <p className="text-xs text-slate-500">暂无带指标的轮次</p>
      </div>
    )
  }

  const best: Partial<Record<(typeof METRIC_KEYS)[number], number>> = {}
  for (const key of METRIC_KEYS) {
    const values = withMetrics
      .map((r) => r.metrics?.[key])
      .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
    if (values.length > 0) best[key] = Math.max(...values)
  }

  const sorted = [...withMetrics].sort((a, b) => a.round_num - b.round_num)

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft transition-colors hover:border-slate-700">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <BarChart3 className="h-3 w-3 text-primary-400" />
        指标对比
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-left text-slate-600">
              <th className="pb-1 pr-2 font-medium">轮次</th>
              {METRIC_KEYS.map((k) => (
                <th key={k} className="pb-1 pr-2 text-right font-medium">
                  {k}
                </th>
              ))}
              <th className="pb-1 pl-1 font-medium">结论</th>
              {onOpenRun && <th className="pb-1 pl-1" />}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {sorted.map((r) => {
              const verdict = r.verdict ?? null
              return (
                <tr key={r.round_num} className="group transition-colors hover:bg-slate-800/30">
                  <td className="py-1.5 pr-2 font-mono text-slate-400">
                    R{r.round_num}
                  </td>
                  {METRIC_KEYS.map((k) => {
                    const v = r.metrics?.[k]
                    const isBest = typeof v === 'number' && best[k] != null && v === best[k]
                    return (
                      <td
                        key={k}
                        className={`py-1.5 pr-2 text-right font-mono tabular-nums ${
                          isBest
                            ? 'font-semibold text-success-fg'
                            : v == null
                              ? 'text-slate-600'
                              : 'text-slate-300'
                        }`}
                        title={isBest ? '本轮最佳' : undefined}
                      >
                        {isBest && <span className="mr-0.5 text-emerald-500">◆</span>}
                        {fmt(v)}
                      </td>
                    )
                  })}
                  <td className="py-1.5 pl-1">
                    {verdict ? (
                      <span
                        className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${
                          verdict === 'keep'
                            ? 'border-emerald-500/40 bg-emerald-500/10 text-success-fg'
                            : verdict === 'review'
                              ? 'border-warning/40 bg-warning-fill text-warning-fg'
                              : 'border-slate-700 bg-slate-800/70 text-slate-500'
                        }`}
                      >
                        <span className={`h-1 w-1 rounded-full ${VERDICT_DOT[verdict] ?? 'bg-slate-500'}`} />
                        {verdict}
                      </span>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  {onOpenRun && (
                    <td className="py-1.5 pl-1 text-right">
                      <button
                        type="button"
                        title="查看回测产物"
                        onClick={() => onOpenRun(r.run_name)}
                        className="cursor-pointer rounded p-1 text-slate-600 opacity-0 transition-all group-hover:opacity-100 hover:bg-slate-800 hover:text-sky-400"
                      >
                        <ExternalLink className="h-3 w-3" />
                      </button>
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-1.5 text-[9px] text-slate-600">◆ 该列最优值</p>
    </div>
  )
}
