import { useMemo } from 'react'
import type { TokenTone } from '../../utils/tokenThresholds'
import {
  getContextUsageConfig,
  type ContextUsageGroup,
} from './chatUiConfig'

/**
 * 上下文用量环（R-014 H2）—— SVG 分段圆环。
 *
 * 后端 `context.breakdown` SSE 事件携带**细粒度段**（§9 复核批：10 段，
 * 见 kernel/context/breakdown.py）；展示行由 chat-ui-config.json 的
 * ``contextUsage.groups`` **配置聚合**（改分组/改名只改配置）。
 * 常态为小环（~20px，单色 tone），hover 时父级弹层展示聚合明细。
 * 分段颜色复用 viz 令牌（`--viz-1..8`，dark/light 双主题已有值）。
 */
export interface RingSegment {
  key: string
  label: string
  value: number
}

/** 细粒度段 → 展示行聚合（配置驱动；source 引用后端段 key）。
 *
 * **兜底**：后端新增细段而前端未配置时，该段不会被任何 group 引用 →
 *  归入「其他」分组，保证展示合计 == 后端总量（占比不失真）。
 */
export function aggregateBreakdown(
  segments: Record<string, number>,
  groups: ContextUsageGroup[],
): Record<string, number> {
  const out: Record<string, number> = {}
  const covered = new Set<string>()
  for (const g of groups) {
    let sum = 0
    for (const src of g.source) {
      sum += segments[src] || 0
      covered.add(src)
    }
    out[g.key] = sum
  }
  // 未被任何 group 引用、但有值的段 → 「其他」
  let other = 0
  for (const [k, v] of Object.entries(segments)) {
    if (!covered.has(k) && v) other += v
  }
  if (other > 0) {
    const fallback = groups.find((g) => g.key === OTHER_GROUP_KEY)
    if (fallback) {
      out[fallback.key] = (out[fallback.key] || 0) + other
    }
  }
  return out
}

/** 「其他」分组 key（未配置的细段落这里）。 */
export const OTHER_GROUP_KEY = 'other'

/** 展示行元数据（配置驱动），供 buildArcs / BreakdownList 使用。 */
export function breakdownGroups(): ContextUsageGroup[] {
  return getContextUsageConfig().groups
}

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

/** 组装分段弧：每段按 value/total 占比映射为圆周长的一部分（聚合展示行）。 */
export function buildArcs(segments: Record<string, number>, circumference: number): Arc[] {
  const meta = breakdownGroups()
  const total = meta.reduce((sum, m) => sum + (segments[m.key] || 0), 0)
  if (total <= 0) return []
  let offset = 0
  return meta.map((m) => {
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

/** 分段明细列表（hover 弹层内使用）：细粒度段按配置聚合成展示行；
 *  无数据显示占位提示。 */
export function BreakdownList({ segments }: { segments: Record<string, number> }) {
  const groups = breakdownGroups()
  const aggregated = aggregateBreakdown(segments, groups)
  const total = groups.reduce((sum, m) => sum + (aggregated[m.key] || 0), 0)
  if (total <= 0) {
    return <div className="text-[10px] text-muted">分段数据待接入（当前显示估算总量）</div>
  }
  return (
    <ul className="space-y-1">
      {groups.map((m) => {
        const value = aggregated[m.key] || 0
        // 「其他」兜底行仅在真有未配置段时显示（常态不占位、不噪音）
        if (m.key === OTHER_GROUP_KEY && value === 0) return null
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
