import { useState } from 'react'
import { BarChart3, ChevronDown, ChevronRight } from 'lucide-react'
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import type { ChartPart } from '../../stores/chat'

interface ChartBlockProps {
  chart: ChartPart
}

const COLORS = ['var(--viz-1)', 'var(--viz-2)', 'var(--viz-3)', 'var(--viz-4)', 'var(--viz-5)', 'var(--viz-6)', 'var(--viz-7)', 'var(--viz-8)']

export function ChartRenderer({ chart }: { chart: ChartPart }) {
  const { chart_type, data } = chart

  if (!data || data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-xs text-muted">
        无数据
      </div>
    )
  }

  // Auto-detect x/y keys from first data item
  const firstItem = data[0] as Record<string, unknown>
  const keys = Object.keys(firstItem)
  const xKey = keys.find((k) => typeof firstItem[k] === 'string') || keys[0]
  const yKey = keys.find((k) => typeof firstItem[k] === 'number') || keys[1] || keys[0]

  switch (chart_type) {
    case 'bar':
      return (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--viz-grid)" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11, fill: 'var(--viz-axis)' }} />
            <YAxis tick={{ fontSize: 11, fill: 'var(--viz-axis)' }} />
            <Tooltip
              contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            />
            <Bar dataKey={yKey} fill="var(--viz-1)" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      )
    case 'line':
      return (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--viz-grid)" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11, fill: 'var(--viz-axis)' }} />
            <YAxis tick={{ fontSize: 11, fill: 'var(--viz-axis)' }} />
            <Tooltip
              contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            />
            <Line type="monotone" dataKey={yKey} stroke="var(--viz-1)" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      )
    case 'pie':
      return (
        <ResponsiveContainer width="100%" height={220}>
          <PieChart>
            <Pie
              data={data}
              dataKey={yKey}
              nameKey={xKey}
              cx="50%"
              cy="50%"
              outerRadius={80}
              label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            />
          </PieChart>
        </ResponsiveContainer>
      )
    case 'scatter':
      return (
        <ResponsiveContainer width="100%" height={220}>
          <ScatterChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--viz-grid)" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11, fill: 'var(--viz-axis)' }} />
            <YAxis tick={{ fontSize: 11, fill: 'var(--viz-axis)' }} />
            <Tooltip
              contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            />
            <Scatter dataKey={yKey} fill="var(--viz-3)" />
          </ScatterChart>
        </ResponsiveContainer>
      )
    default:
      return (
        <div className="flex h-48 items-center justify-center text-xs text-muted">
          不支持的图表类型: {chart_type}
        </div>
      )
  }
}

export function ChartBlock({ chart }: ChartBlockProps) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div className="my-2 rounded-lg border border-subtle overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 bg-surface px-3 py-2 text-left text-xs hover:bg-surface-raised/80 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-muted" />
        ) : (
          <ChevronRight className="h-3 w-3 text-muted" />
        )}
        <BarChart3 className="h-3.5 w-3.5 text-primary-400" />
        <span className="text-secondary">
          {chart.title || `${chart.chart_type} 图表`}
        </span>
        <span className="text-[10px] text-muted ml-auto">
          {chart.data.length} 数据点
        </span>
      </button>

      {/* Chart */}
      {expanded && (
        <div className="p-3">
          <ChartRenderer chart={chart} />
        </div>
      )}
    </div>
  )
}
