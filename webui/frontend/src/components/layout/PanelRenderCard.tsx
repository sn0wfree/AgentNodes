import { useMemo } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Area,
} from 'recharts'
import { BarChart3, FileCode2, LineChart as LineChartIcon, TrendingUp, TrendingDown } from 'lucide-react'
import { ChartRenderer } from '../chat/ChartBlock'
import type {
  BacktestMetrics,
  EquityCurve,
  EquityPoint,
  PanelRenderable,
} from '../../utils/equityCurve'

interface Props {
  /**
   * Latest agent-driven renderable (show_chart / show_report).
   * The agent decides what this card shows.
   */
  item: PanelRenderable | null
  /** Metrics fallback when no renderable exists yet (Tier B P7). */
  metrics?: BacktestMetrics | null
  /**
   * Optional nav curve (line chart points) decoded from chart parts
   * whose title matches equity patterns. When present, renders the
   * full equity curve + stat chips. docs/right-panel-agent-driven.md
   */
  curve?: EquityCurve | null
}

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

function fmtPct(n: number): string {
  return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(2)}%`
}

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
    : 'text-slate-200'
  return (
    <div className="rounded border border-slate-800/50 bg-slate-900/40 px-2 py-1.5">
      <div className="text-[9px] text-slate-500">{label}</div>
      <div className={`flex items-center gap-1 font-mono text-xs ${color}`}>
        {Icon && <Icon className="h-3 w-3" />}
        {value}
      </div>
    </div>
  )
}

function curveStats(points: EquityPoint[]) {
  if (points.length === 0) return null
  const values = points.map((p) => p.value)
  const latest = values[values.length - 1]
  const first = values[0]
  const peak = Math.max(...values)
  const trough = Math.min(...values)
  const totalReturn = first !== 0 ? latest / first - 1 : 0
  const maxDrawdown = peak !== 0 ? trough / peak - 1 : 0
  return { latest, first, peak, trough, totalReturn, maxDrawdown }
}

/**
 * 「表现曲线」right-panel card — the SOLE owner of the performance
 * surface. Inline branches (in priority order):
 *
 * 1. show_chart / show_report renderable (agent-driven)
 *    - chart → ChartRenderer
 *    - html  → sandboxed iframe (srcdoc)
 * 2. Equity curve points (decoded from line chart parts) → recharts
 *    LineChart + stat chips
 * 3. run_backtest metrics only → subtitle + 3-stat row (Tier B P7)
 * 4. Nothing → empty placeholder
 *
 * EquityCurveCard logic (formerly a separate component) is inlined
 * here so the card has exactly ONE header. docs/right-panel-agent-driven.md
 */
export function PanelRenderCard({ item, metrics, curve }: Props) {
  // Full equity curve branch — render real LineChart + stat chips.
  const stats = useMemo(
    () => (curve && curve.points.length > 0 ? curveStats(curve.points) : null),
    [curve],
  )

  // P6 空态诚实（业界调研 ux-side-panel-industry-survey-20261123）：
  // 无 renderable / metrics / curve 时不渲染卡片 —— 右栏不摆空占位卡，
  // run 产出后本卡自动出现。
  const hasContent = Boolean(item) || Boolean(metrics) || (Boolean(curve) && (curve?.points.length ?? 0) > 0)
  if (!hasContent) return null

  const title =
    item?.kind === 'chart'
      ? item.chart?.title || '图表'
      : item?.kind === 'html'
        ? item.html?.title || 'HTML 报告'
        : ''

  // Header icon follows the active branch.
  const HeaderIcon =
    item?.kind === 'chart'
      ? BarChart3
      : item?.kind === 'html'
        ? FileCode2
        : LineChartIcon

  return (
    <div className="rounded-lg border border-slate-800/50 bg-slate-900/30 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <HeaderIcon className="h-3 w-3" />
        <span>表现曲线</span>
      </div>

      {item?.kind === 'chart' ? (
        <div>
          {title && <div className="mb-2 text-xs text-slate-400">{title}</div>}
          <ChartRenderer
            chart={{
              type: 'chart',
              chart_type: item.chart!.chart_type as 'bar' | 'line' | 'pie' | 'scatter',
              data: item.chart!.data,
              title,
            }}
          />
        </div>
      ) : item?.kind === 'html' ? (
        <div>
          {title && <div className="mb-2 text-xs text-slate-400">{title}</div>}
          <iframe
            title={title}
            sandbox=""
            srcDoc={item.html!.content}
            className="h-[260px] w-full rounded border border-slate-800 bg-white"
          />
        </div>
      ) : stats && curve ? (
        <div>
          {curve.title && (
            <div className="mb-2 text-xs text-slate-400">{curve.title}</div>
          )}
          <div className="mb-2 grid grid-cols-3 gap-2">
            <Stat label="最新" value={fmt(stats.latest)} />
            <Stat
              label="区间收益"
              value={fmtPct(stats.totalReturn)}
              tone={stats.totalReturn >= 0 ? 'up' : stats.totalReturn === 0 ? 'flat' : 'down'}
            />
            <Stat label="最大回撤" value={fmtPct(stats.maxDrawdown)} tone="down" />
          </div>
          <div className="h-36">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={curve.points} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={stats.totalReturn >= 0 ? 'var(--success)' : 'var(--danger)'} stopOpacity={0.25} />
                    <stop offset="100%" stopColor={stats.totalReturn >= 0 ? 'var(--success)' : 'var(--danger)'} stopOpacity={0} />
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
                  itemStyle={{ color: stats.totalReturn >= 0 ? 'var(--success)' : 'var(--danger)' }}
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
                  stroke={stats.totalReturn >= 0 ? '#10b981' : '#ef4444'}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : metrics && (
        metrics.total_return !== undefined ||
        metrics.sharpe !== undefined ||
        metrics.max_drawdown !== undefined
      ) ? (
        <div>
          {(() => {
            const subtitle = metrics?.strategy
              ? `${metrics.strategy}${metrics.run ? ` · ${metrics.run}` : ''}`
              : metrics?.run ?? '最近回测'
            return (
              <div className="mb-2 text-[10px] text-slate-500">{subtitle}</div>
            )
          })()}
          <p className="mb-2 text-[10px] text-slate-500">
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
      ) : (
        <p className="py-6 text-center text-[11px] text-slate-500">
          暂无回测净值数据
        </p>
      )}
    </div>
  )
}