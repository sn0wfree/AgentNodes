import { useMemo } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Area,
} from 'recharts'
import { LineChart as LineChartIcon, TrendingUp, TrendingDown } from 'lucide-react'
import type {
  EquityCurve,
  BacktestMetrics,
} from '../../utils/equityCurve'

interface Props {
  curve: EquityCurve | null
  /**
   * Optional metrics-only fallback. When ``curve`` is null but the
   * session has a recent ``run_backtest`` tool_call, we can still
   * surface the key stats (total_return / sharpe / max_drawdown) so
   * the right-panel card is never empty for an active strategy
   * researcher. Tier B P7.
   */
  metrics?: BacktestMetrics | null
}

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

function fmtPct(n: number): string {
  return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(2)}%`
}

/** Stat card component used in both the curve and metrics branches. */
function Stat({
  label,
  value,
  tone = 'flat',
}: {
  label: string
  value: string
  tone?: 'up' | 'down' | 'flat'
}) {
  const Icon = tone === 'up' ? TrendingUp : tone === 'down' ? TrendingDown : null
  const color = tone === 'up' ? 'text-emerald-400'
    : tone === 'down' ? 'text-red-400'
    : 'text-foreground'
  return (
    <div className="rounded border border-subtle bg-surface px-2 py-1.5">
      <div className="text-[9px] text-muted">{label}</div>
      <div className={`flex items-center gap-1 font-mono text-xs ${color}`}>
        {Icon && <Icon className="h-3 w-3" />}
        {value}
      </div>
    </div>
  )
}

export function EquityCurveCard({ curve, metrics }: Props) {
  const stats = useMemo(() => {
    if (!curve || curve.points.length === 0) return null
    const values = curve.points.map((p) => p.value)
    const latest = values[values.length - 1]
    const first = values[0]
    const peak = Math.max(...values)
    const trough = Math.min(...values)
    const totalReturn = first !== 0 ? latest / first - 1 : 0
    const maxDrawdown = peak !== 0 ? trough / peak - 1 : 0
    return { latest, first, peak, trough, totalReturn, maxDrawdown }
  }, [curve])

  // ── Empty state: keep the card visible if we have metrics ──
  if (!curve || !stats || curve.points.length === 0) {
    const hasMetrics = metrics && (
      metrics.total_return !== undefined ||
      metrics.sharpe !== undefined ||
      metrics.max_drawdown !== undefined
    )
    if (!hasMetrics) {
      return (
        <div className="rounded-lg border border-subtle bg-slate-900/30 p-3">
          <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted">
            <LineChartIcon className="h-3 w-3" />
            <span>表现曲线</span>
          </div>
          <p className="py-6 text-center text-[11px] text-muted">
            暂无回测净值数据
          </p>
        </div>
      )
    }
    // Metrics-only branch: show a compact row of the key stats
    // derived from the most recent run_backtest tool_call result.
    const subtitle = metrics?.strategy
      ? `${metrics.strategy}${metrics.run ? ` · ${metrics.run}` : ''}`
      : metrics?.run ?? '最近回测'
    return (
      <div className="rounded-lg border border-subtle bg-slate-900/30 p-3">
        <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted">
          <LineChartIcon className="h-3 w-3" />
          <span className="truncate">{subtitle}</span>
        </div>
        <p className="mb-2 text-[10px] text-muted">
          暂无曲线数据，以下为最近一次回测的关键指标
        </p>
        <div className="grid grid-cols-3 gap-2">
          {metrics?.total_return !== undefined && (
            <Stat
              label="总收益"
              value={fmtPct(metrics.total_return)}
              tone={metrics.total_return >= 0 ? 'up' : 'down'}
            />
          )}
          {metrics?.sharpe !== undefined && (
            <Stat
              label="Sharpe"
              value={fmt(metrics.sharpe)}
              tone={metrics.sharpe >= 1 ? 'up' : 'flat'}
            />
          )}
          {metrics?.max_drawdown !== undefined && (
            <Stat
              label="最大回撤"
              value={fmtPct(metrics.max_drawdown)}
              tone="down"
            />
          )}
        </div>
      </div>
    )
  }

  const up = stats.totalReturn >= 0
  const color = up ? 'var(--success)' : 'var(--danger)'

  return (
    <div className="rounded-lg border border-subtle bg-slate-900/30 p-3">
      {/* Header */}
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted">
        <LineChartIcon className="h-3 w-3" />
        <span className="truncate">{curve.title}</span>
      </div>

      {/* Stat chips */}
      <div className="mb-2 grid grid-cols-3 gap-2">
        <Stat label="最新" value={fmt(stats.latest)} />
        <Stat
          label="区间收益"
          value={fmtPct(stats.totalReturn)}
          tone={up ? 'up' : stats.totalReturn === 0 ? 'flat' : 'down'}
        />
        <Stat label="最大回撤" value={fmtPct(stats.maxDrawdown)} tone="down" />
      </div>

      {/* Curve */}
      <div className="h-36">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={curve.points} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.25} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 9, fill: '#64748b' }}
              tickLine={false}
              axisLine={{ stroke: '#1e293b' }}
              minTickGap={24}
            />
            <YAxis
              tick={{ fontSize: 9, fill: '#64748b' }}
              tickLine={false}
              axisLine={false}
              width={44}
              domain={['auto', 'auto']}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#0f172a',
                border: '1px solid #334155',
                borderRadius: 8,
                fontSize: 11,
              }}
              labelStyle={{ color: '#94a3b8' }}
              itemStyle={{ color: color }}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke="none"
              fill="url(#eqFill)"
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke={color}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}