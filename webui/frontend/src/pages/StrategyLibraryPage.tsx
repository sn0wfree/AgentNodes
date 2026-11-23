import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Layers, Folder, ArrowRight, FileText, AlertTriangle, CheckCircle2, CircleDashed } from 'lucide-react'
import { PageShell } from '../components/layout/PageShell'
import { api, type StrategyListItem, type RunListItem, type StudySummary } from '../api/client'
import { useSystemStore } from '../stores/system'
import { useSessionStore } from '../stores/session'

interface StrategyCardData extends StrategyListItem {
  runs: RunListItem[]
}

const STATUS_META: Record<string, { label: string; cls: string }> = {
  running: { label: '研究中', cls: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400' },
  queued: { label: '排队中', cls: 'border-sky-500/30 bg-sky-500/10 text-sky-400' },
  monitoring: { label: '监控中', cls: 'border-sky-500/30 bg-sky-500/10 text-sky-400' },
  paused: { label: '已暂停', cls: 'border-warning/30 bg-warning-fill text-warning-fg' },
  completed: { label: '已完成', cls: 'border-slate-600 bg-slate-800/40 text-slate-300' },
  failed: { label: '已失败', cls: 'border-red-500/30 bg-red-500/10 text-red-400' },
  cancelled: { label: '已取消', cls: 'border-slate-600 bg-slate-800/40 text-slate-400' },
}

function fmtMetric(metrics: Record<string, number | string> | null | undefined, key: string): string {
  const v = metrics?.[key]
  if (v === undefined || v === null) return '—'
  if (typeof v === 'number') {
    if (key === 'total_return' || key === 'sharpe' || key === 'calmar' || key === 'max_drawdown') {
      return (v * 100).toFixed(1) + '%'
    }
    return String(v)
  }
  return String(v)
}

function MetricBox({ label, value, tone }: { label: string; value: string; tone: 'pos' | 'neg' | 'neutral' }) {
  const color =
    tone === 'pos' ? 'text-emerald-400'
    : tone === 'neg' ? 'text-red-400'
    : 'text-sky-400'
  return (
    <div className="min-w-[92px] flex-1 rounded-md border border-slate-800 bg-slate-950/60 px-3 py-2 text-center">
      <div className={`font-mono text-sm font-semibold ${value === '—' ? 'text-slate-600' : color}`}>{value}</div>
      <div className="mt-0.5 text-[9px] text-slate-500">{label}</div>
    </div>
  )
}

export function StrategyLibraryPage() {
  const navigate = useNavigate()
  const workspacePath = useSystemStore((s) => s.workspacePath)
  const sessionId = useSessionStore((s) => s.currentSessionId)

  const [strategies, setStrategies] = useState<StrategyCardData[]>([])
  const [studies, setStudies] = useState<StudySummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!workspacePath) return
    setLoading(true)
    setError(null)
    try {
      const [strategyRes, runRes, studyRes] = await Promise.allSettled([
        api.strategies.list(workspacePath),
        api.run.list(workspacePath),
        sessionId ? api.study.list({ session_id: sessionId, limit: 20 }) : Promise.resolve(null),
      ])

      const strategyList: StrategyListItem[] =
        strategyRes.status === 'fulfilled' ? strategyRes.value.strategies : []
      const runList: RunListItem[] = runRes.status === 'fulfilled' ? runRes.value.runs : []
      const studyList: StudySummary[] =
        studyRes.status === 'fulfilled' && studyRes.value ? studyRes.value.studies : []

      const byName = new Map<string, RunListItem[]>()
      for (const r of runList) {
        // run_* dirs don't carry strategy names; group by the strategy
        // dir they live under is not exposed by /run/list — attach all
        // runs to the single workspace scan, then per-card lookups use
        // the most recent runs.
        byName.set('*', [...(byName.get('*') ?? []), r])
      }

      setStrategies(
        strategyList.map((s) => ({
          ...s,
          runs: byName.get('*') ?? [],
        })),
      )
      setStudies(studyList)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [workspacePath, sessionId])

  useEffect(() => {
    void load()
  }, [load])

  const latestRunsByStrategy = useMemo(() => {
    const map = new Map<string, RunListItem>()
    for (const s of studies) {
      if (s.strategy_name && !map.has(s.strategy_name)) {
        // best-effort: study carries strategy_name, use its last_metrics
        map.set(s.strategy_name, {
          name: `run_${s.current_round ?? '?'}`,
          metrics: s.last_metrics ?? {},
        })
      }
    }
    return map
  }, [studies])

  return (
    <PageShell
      title="策略库"
      subtitle="/api/strategies/list + /api/run/list + /api/study/list"
      icon={<Layers className="h-4 w-4" />}
    >
      {!workspacePath && (
        <p className="mb-3 text-xs text-warning-fg">
          未加载到 workspace 路径，请先在设置中确认系统信息
        </p>
      )}
      {loading && strategies.length === 0 && (
        <div className="space-y-2.5">
          <div className="h-[96px] rounded-xl border border-slate-800/50" />
          <div className="h-[96px] rounded-xl border border-slate-800/50" />
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-xs text-red-400">
          {error}
        </div>
      )}

      {/* Strategy cards */}
      <div className="space-y-2.5">
            {strategies.map((s) => {
              const studyForStrategy = studies.filter((st) => st.strategy_name === s.name)
              const latest = latestRunsByStrategy.get(s.name) ?? s.runs[0]
              const metrics = latest?.metrics as Record<string, number | string> | undefined
              return (
                <div
                  key={s.name}
                  className="cursor-pointer rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3.5 transition-all hover:-translate-y-px hover:border-slate-700 hover:shadow-lg hover:shadow-black/40"
                  onClick={() => {
                    const run = s.runs[0]
                    if (run) navigate(`/run/${s.name}/${run.name}`)
                  }}
                >
                  <div className="flex items-center gap-2.5">
                    <Folder className="h-4 w-4 text-slate-400" />
                    <span className="text-sm font-semibold text-slate-100">{s.name}</span>
                    {s.has_strategy_py ? (
                      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[9px] text-emerald-400">
                        <CheckCircle2 className="h-2.5 w-2.5" /> strategy.py
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full border border-warning/30 bg-warning-fill px-2 py-0.5 text-[9px] text-warning-fg">
                        <AlertTriangle className="h-2.5 w-2.5" /> 无 strategy.py
                      </span>
                    )}
                    {s.has_config_yaml ? (
                      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[9px] text-emerald-400">
                        <CheckCircle2 className="h-2.5 w-2.5" /> config.yaml
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full border border-slate-700 px-2 py-0.5 text-[9px] text-slate-400">
                        <CircleDashed className="h-2.5 w-2.5" /> 无 config.yaml
                      </span>
                    )}
                    <span className="rounded-full border border-slate-700 px-2 py-0.5 font-mono text-[9px] text-slate-400">
                      run ×{s.runs.length}
                    </span>
                    <div className="flex-1" />
                    <span className="inline-flex items-center gap-1 text-xs text-primary-400 hover:text-primary-300">
                      查看详情 <ArrowRight className="h-3 w-3" />
                    </span>
                  </div>

                  <div className="mt-3 flex gap-2.5">
                    <MetricBox label="总收益" value={fmtMetric(metrics, 'total_return')} tone={Number((metrics?.total_return ?? 0)) >= 0 ? 'pos' : 'neg'} />
                    <MetricBox label="夏普" value={fmtMetric(metrics, 'sharpe')} tone="neutral" />
                    <MetricBox label="卡玛" value={fmtMetric(metrics, 'calmar')} tone={Number((metrics?.calmar ?? 0)) >= 0 ? 'pos' : 'neg'} />
                    <MetricBox label="最大回撤" value={fmtMetric(metrics, 'max_drawdown')} tone="neg" />
                    {latest && <span className="ml-1 self-center font-mono text-[9px] text-slate-600">{latest.name}</span>}
                  </div>

                  {studyForStrategy.length > 0 && (
                    <div className="mt-2.5 flex flex-wrap gap-1.5">
                      {studyForStrategy.slice(0, 3).map((st) => {
                        const meta = STATUS_META[st.execution_status] ?? STATUS_META.queued
                        return (
                          <span
                            key={st.study_id}
                            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] ${meta.cls}`}
                          >
                            <span className={`h-1.5 w-1.5 rounded-full ${st.execution_status === 'running' ? 'animate-pulse bg-current' : 'bg-current'}`} />
                            {meta.label} · 轮次 {st.current_round ?? 0}
                          </span>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
            {!loading && strategies.length === 0 && (
              <div className="py-20 text-center text-sm text-slate-500">暂无策略</div>
            )}
          </div>

          {/* Study history */}
          {studies.length > 0 && (
            <>
              <h2 className="mb-2.5 mt-7 flex items-center gap-2 text-xs font-semibold text-slate-500">
                <FileText className="h-3.5 w-3.5" />
                研究记录
                <span className="font-mono font-normal text-slate-600">/api/study/list</span>
              </h2>
              <div className="overflow-hidden rounded-lg border border-slate-800">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-slate-800 bg-slate-900/80 text-left text-[10px] text-slate-500">
                      <th className="px-3 py-2.5 font-medium">目标</th>
                      <th className="px-3 py-2.5 font-medium">策略</th>
                      <th className="px-3 py-2.5 font-medium">状态</th>
                      <th className="px-3 py-2.5 font-medium">轮次</th>
                      <th className="px-3 py-2.5 font-medium">最新结论</th>
                    </tr>
                  </thead>
                  <tbody>
                    {studies.map((st) => {
                      const meta = STATUS_META[st.execution_status] ?? STATUS_META.queued
                      return (
                        <tr
                          key={st.study_id}
                          className="cursor-pointer border-b border-slate-800/50 transition-colors last:border-b-0 hover:bg-slate-800/30"
                          onClick={() => navigate(`/study/${st.study_id}`)}
                        >
                          <td className="px-3 py-2.5 text-slate-300">{st.objective || '—'}</td>
                          <td className="px-3 py-2.5 font-mono text-primary-300">{st.strategy_name || '—'}</td>
                          <td className="px-3 py-2.5">
                            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[9px] ${meta.cls}`}>
                              <span className={`h-1.5 w-1.5 rounded-full bg-current ${st.execution_status === 'running' ? 'animate-pulse' : ''}`} />
                              {meta.label}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 font-mono text-slate-400">{st.current_round ?? 0}</td>
                          <td className="px-3 py-2.5 text-slate-400">{st.last_verdict ?? '—'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
    </PageShell>
  )
}
