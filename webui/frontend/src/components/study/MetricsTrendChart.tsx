import { useMemo } from 'react'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
} from 'recharts'
import type { StudyRoundSummary } from '../../api/client'

interface Props {
  rounds: StudyRoundSummary[]
  metricTargets?: { name: string; op: string; value: number }[]
}

const METRIC_COLORS: Record<string, string> = {
  calmar: 'var(--viz-3)',
  sharpe: 'var(--viz-5)',
  max_dd: 'var(--viz-4)',
  sortino: 'var(--viz-2)',
}

const METRIC_LABELS: Record<string, string> = {
  calmar: 'Calmar',
  sharpe: 'Sharpe',
  max_dd: 'MaxDD',
  sortino: 'Sortino',
}

const OP_SYMBOLS: Record<string, string> = {
  '>=': '≥',
  '<=': '≤',
  '>': '>',
  '<': '<',
  '==': '=',
}

export function MetricsTrendChart({ rounds, metricTargets }: Props) {
  const chartData = useMemo(() => {
    return rounds.map((r) => ({
      round: r.round_num,
      ...(r.metrics ?? {}),
    }))
  }, [rounds])

  // Get unique metric names from all rounds
  const metricNames = useMemo(() => {
    const names = new Set<string>()
    rounds.forEach((r) => {
      if (r.metrics) {
        Object.keys(r.metrics).forEach((k) => names.add(k))
      }
    })
    return Array.from(names)
  }, [rounds])

  if (rounds.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-xs text-muted">
        暂无轮次数据
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="text-[10px] font-medium uppercase tracking-wider text-muted">
        指标趋势
      </div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis
              dataKey="round"
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              stroke="#334155"
            />
            <YAxis
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              stroke="#334155"
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '8px',
                fontSize: '11px',
              }}
              labelStyle={{ color: '#94a3b8' }}
            />
            <Legend
              wrapperStyle={{ fontSize: '10px' }}
              iconType="plainline"
            />
            {metricNames.map((name) => (
              <Line
                key={name}
                type="monotone"
                dataKey={name}
                stroke={METRIC_COLORS[name] ?? '#64748b'}
                strokeWidth={2}
                dot={{ r: 3, fill: METRIC_COLORS[name] ?? '#64748b' }}
                activeDot={{ r: 5 }}
                name={METRIC_LABELS[name] ?? name}
              />
            ))}
            {/* Target reference lines — labeled with the comparison op
                so the direction (e.g. MaxDD ≤ target) is readable. */}
            {metricTargets?.map((t) => (
              <ReferenceLine
                key={`${t.name}-${t.value}`}
                y={t.value}
                stroke={METRIC_COLORS[t.name] ?? '#64748b'}
                strokeDasharray="5 5"
                strokeOpacity={0.4}
                label={{
                  value: `${METRIC_LABELS[t.name] ?? t.name} ${OP_SYMBOLS[t.op] ?? t.op} ${t.value}`,
                  position: t.op === '<=' || t.op === '<' ? 'insideBottomRight' : 'insideTopRight',
                  fontSize: 9,
                  fill: METRIC_COLORS[t.name] ?? '#94a3b8',
                }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
