import { useEffect, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { ArrowLeft, FolderOpen, LineChart as LineChartIcon } from 'lucide-react'
import {
  ResponsiveContainer, LineChart, Line, AreaChart, Area,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { api, type RunStatusResponse, type RunEquityResponse } from '../../api/client'
import { useSystemStore } from '../../stores/system'
import { EmptyState } from '../common/EmptyState'

interface EquityDatum {
  idx: number
  equity: number
  drawdown: number
}

export function computeDrawdowns(points: Array<{ equity: number }>): EquityDatum[] {
  let peak = -Infinity
  return points.map((p, i) => {
    peak = Math.max(peak, p.equity)
    const dd = peak > 0 ? (p.equity - peak) / peak : 0
    return { idx: i, equity: p.equity, drawdown: dd }
  })
}

export function fmt(n: number | string | null | undefined, digits = 4): string {
  if (n == null) return '—'
  const v = typeof n === 'string' ? Number(n) : n
  if (!Number.isFinite(v)) return String(n)
  return v.toFixed(digits)
}

export function RunDetailPage() {
  const { strategyName = '', runName = '' } = useParams<{ strategyName: string; runName: string }>()
  const navigate = useNavigate()
  const workspacePath = useSystemStore((s) => s.workspacePath)

  const [metrics, setMetrics] = useState<RunStatusResponse | null>(null)
  const [equity, setEquity] = useState<RunEquityResponse | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      if (!workspacePath) return
      try {
        const [m, e] = await Promise.all([
          api.run.status(workspacePath, strategyName, runName),
          api.run.equity(workspacePath, strategyName, runName),
        ])
        if (cancelled) return
        setMetrics(m)
        setEquity(e)
        setError('')
      } catch (err) {
        if (cancelled) return
        const status = (err as { status?: number })?.status
        if (status === 404) {
          setNotFound(true)
        } else {
          setError((err as Error).message)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [workspacePath, strategyName, runName])

  if (loading || !workspacePath) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-muted">
        <div className="flex items-center gap-2">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-strong border-t-sky-500" />
          加载中...
        </div>
      </div>
    )
  }

  if (notFound) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-950">
        <EmptyState
          icon={<FolderOpen className="h-10 w-10" />}
          title="回测产物不存在"
          description="该 run 可能已被删除，或链接不正确。"
        />
        <Link to="/" className="text-sm text-sky-400 hover:text-sky-300 hover:underline">
          返回聊天
        </Link>
      </div>
    )
  }

  const m = metrics?.metrics ?? {}
  const points = computeDrawdowns(equity?.equity ?? [])
  const chartColor = '#38bdf8'
  const ddColor = '#f87171'
  const baseline = typeof points[0]?.equity === 'number' ? points[0].equity : undefined

  const METRIC_CARDS: Array<{ label: string; key: string; color: string }> = [
    { label: '总收益', key: 'total_return', color: 'text-emerald-400' },
    { label: 'Sharpe', key: 'sharpe', color: 'text-sky-400' },
    { label: 'Calmar', key: 'calmar', color: 'text-indigo-400' },
    { label: '最大回撤', key: 'max_dd', color: 'text-rose-400' },
  ]

  return (
    <div className="min-h-screen bg-slate-950 text-foreground">
      <header className="flex items-center gap-3 border-b border-subtle bg-surface-raised px-4 py-2.5">
        <button
          onClick={() => navigate(-1)}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-sm text-muted hover:bg-surface-raised hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> 返回
        </button>
        <LineChartIcon className="h-4 w-4 text-muted" />
        <div className="min-w-0">
          <h1 className="truncate text-sm font-medium text-foreground">{runName}</h1>
          <p className="truncate text-[10px] text-muted">{strategyName}</p>
        </div>
        {m.status && (
          <span
            className={`ml-auto inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
              m.status === 'ok' ? 'bg-emerald-900/50 text-emerald-400' : 'bg-rose-900/50 text-rose-400'
            }`}
          >
            {m.status}
          </span>
        )}
      </header>

      {error && (
        <div className="mx-4 mt-2 rounded border border-rose-800 bg-rose-950/50 px-3 py-1.5 text-xs text-rose-300">
          {error}
        </div>
      )}

      <main className="space-y-4 p-4">
        {/* Metric cards */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {METRIC_CARDS.map((c) => (
            <div key={c.key} className="rounded border border-strong bg-slate-900 p-3">
              <div className="text-[10px] uppercase text-muted">{c.label}</div>
              <div className={`mt-1 font-mono text-lg ${c.color}`}>
                {fmt(m[c.key])}
              </div>
            </div>
          ))}
        </div>

        {/* Equity curve */}
        <div className="rounded border border-strong bg-slate-900 p-3">
          <div className="mb-2 text-[10px] uppercase text-muted">净值曲线</div>
          {points.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted">无净值数据</p>
          ) : (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={points} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                  <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="idx"
                    stroke="#475569"
                    fontSize={10}
                    tickFormatter={(v: number) => (points.length > 1 ? Math.round((v / (points.length - 1)) * 100) + '%' : '')}
                  />
                  <YAxis stroke="#475569" fontSize={10} domain={['auto', 'auto']} width={52} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--popover)', border: '1px solid var(--border)',
                      borderRadius: 8, fontSize: 11,
                    }}
                    labelFormatter={(v) => `进度 ${v}%`}
                    formatter={(value) => [Number(value).toFixed(4), '净值']}
                  />
                  <Line
                    type="monotone" dataKey="equity" stroke={chartColor}
                    strokeWidth={1.5} dot={false}
                  />
                  <ReferenceLine y={baseline} stroke="#334155" strokeDasharray="4 4" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* Drawdown */}
        <div className="rounded border border-strong bg-slate-900 p-3">
          <div className="mb-2 text-[10px] uppercase text-muted">回撤</div>
          {points.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted">无回撤数据</p>
          ) : (
            <div className="h-40">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={points} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                  <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="idx"
                    stroke="#475569"
                    fontSize={10}
                    tickFormatter={(v: number) => (points.length > 1 ? Math.round((v / (points.length - 1)) * 100) + '%' : '')}
                  />
                  <YAxis stroke="#475569" fontSize={10} tickFormatter={(v: number) => (v * 100).toFixed(0) + '%'} width={52} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--popover)', border: '1px solid var(--border)',
                      borderRadius: 8, fontSize: 11,
                    }}
                    labelFormatter={(v) => `进度 ${v}%`}
                    formatter={(value) => [(Number(value) * 100).toFixed(2) + '%', '回撤']}
                  />
                  <Area
                    type="monotone" dataKey="drawdown" stroke={ddColor}
                    fill={ddColor} fillOpacity={0.25} strokeWidth={1.5}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
