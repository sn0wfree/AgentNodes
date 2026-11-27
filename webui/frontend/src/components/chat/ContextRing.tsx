import { useMemo } from 'react'
import type { TokenTone } from '../../utils/tokenThresholds'

/**
 * 上下文用量环（R-014 H2）—— SVG 分段圆环。
 *
 * 7 段数据来自 `context.breakdown` SSE 事件（后端按 7 类归属估算）。
 * 常态为小环（状态行内 ~20px），hover 时父级弹层展示分段明细。
 * 分段颜色复用 viz 令牌（`--viz-1..8`，dark/light 双主题已有值）。
 */
export interface RingSegment {
  key: string
  label: string
  value: number
}

export const RING_SEGMENT_META: { key: string; label: string; colorVar: string }[] = [
  { key: 'messages', label: '历史消息', colorVar: '--viz-1' },
  { key: 'system_prompt', label: '系统提示', colorVar: '--viz-3' },
  { key: 'meta_user_context', label: '用户上下文', colorVar: '--viz-5' },
  { key: 'skills', label: '技能', colorVar: '--viz-2' },
  { key: 'tool_prompt', label: '工具说明', colorVar: '--viz-4' },
  { key: 'system_tool_schemas', label: '工具 Schema', colorVar: '--viz-6' },
  { key: 'mcp_tool_schemas', label: 'MCP Schema', colorVar: '--viz-7' },
]

interface ContextRingProps {
  /** 各段 token 数（key → value）；无数据时显示单色环 */
  segments: Record<string, number>
  /** 百分比 0-100（来自总量/上限） */
  percent: number
  /** 语义色带（决定无数据时的单色与中心文字色） */
  tone: TokenTone
  /** 直径像素，默认 20（状态行小环） */
  size?: number
}

interface Arc {
  key: string
  label: string
  value: number
  ratio: number
  colorVar: string
  dashArray: string
  dashOffset: number
}

/** 组装分段弧：每段按 value/total 占比映射为圆周长的一部分。 */
export function buildArcs(segments: Record<string, number>, circumference: number): Arc[] {
  const total = RING_SEGMENT_META.reduce((sum, m) => sum + (segments[m.key] || 0), 0)
  if (total <= 0) return []
  let offset = 0
  return RING_SEGMENT_META.map((m) => {
    const value = segments[m.key] || 0
    const ratio = value / total
    const len = ratio * circumference
    const arc: Arc = {
      key: m.key,
      label: m.label,
      value,
      ratio,
      colorVar: m.colorVar,
      dashArray: `${len} ${circumference - len}`,
      dashOffset: -offset,
    }
    offset += len
    return arc
  }).filter((a) => a.value > 0)
}

export function ContextRing({ segments, percent, tone, size = 20 }: ContextRingProps) {
  const stroke = 3
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const arcs = useMemo(() => buildArcs(segments, circumference), [segments, circumference])
  // 低占用时真实弧长不足 1px、视觉上不可见；非零占用给一个最小可见弧长
  // （4% 圆周 + round 端点 ≈ 5px），仅影响视觉下限，精确比例仍在 hover 明细。
  const rawFilled = Math.min(100, Math.max(0, percent)) / 100 * circumference
  const filled = rawFilled > 0 ? Math.max(rawFilled, circumference * 0.04) : 0
  const toneColor = tone === 'danger' ? 'var(--danger-fg)'
    : tone === 'warn' ? 'var(--warning-fg)' : 'var(--success-fg)'

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className="shrink-0 -rotate-90"
      data-testid="context-ring"
      aria-hidden="true"
    >
      {/* 底环 */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--muted)"
        strokeWidth={stroke}
      />
      {arcs.length > 0 ? (
        // 7 段实数据：逐段着色
        arcs.map((arc) => (
          <circle
            key={arc.key}
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={`var(${arc.colorVar})`}
            strokeWidth={stroke}
            strokeDasharray={arc.dashArray}
            strokeDashoffset={arc.dashOffset}
            data-testid={`context-ring-seg-${arc.key}`}
          />
        ))
      ) : (
        // 无分段数据：按总量占比单色环（F4-b 估算的兜底路径）
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={toneColor}
          strokeWidth={stroke}
          strokeDasharray={`${filled} ${circumference - filled}`}
          strokeLinecap="round"
          data-testid="context-ring-single"
        />
      )}
    </svg>
  )
}

/** 7 段明细列表（hover 弹层内使用）；无数据显示占位提示。 */
export function BreakdownList({ segments }: { segments: Record<string, number> }) {
  const total = RING_SEGMENT_META.reduce((sum, m) => sum + (segments[m.key] || 0), 0)
  if (total <= 0) {
    return <div className="text-[10px] text-muted">分段数据待接入（当前显示估算总量）</div>
  }
  return (
    <ul className="space-y-1">
      {RING_SEGMENT_META.map((m) => {
        const value = segments[m.key] || 0
        const pct = (value / total) * 100
        return (
          <li key={m.key} className="flex items-center gap-2 text-[10px]" data-testid={`breakdown-row-${m.key}`}>
            <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: `var(${m.colorVar})` }} />
            <span className="flex-1 text-secondary">{m.label}</span>
            <span className="font-mono text-muted">{value.toLocaleString()} ({pct.toFixed(0)}%)</span>
          </li>
        )
      })}
    </ul>
  )
}
