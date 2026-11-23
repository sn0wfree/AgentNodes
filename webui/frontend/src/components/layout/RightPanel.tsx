import { useMemo, useState } from 'react'
import { useGoalStore } from '../../stores/goal'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import {
  extractEquityCurve,
  extractLatestBacktestMetrics,
  extractLatestPanelItem,
} from '../../utils/equityCurve'
import { TokenCard } from '../context/TokenCard'
import { GoalCard } from '../goal/GoalCard'
import { PanelRenderCard } from './PanelRenderCard'
import { TraceViewer } from '../chat/TraceViewer'
import type { GoalTabGoal } from '../goal/GoalTab'

/**
 * Merged single right panel: a scrollable column of cards —
 * token usage, goal & progress (passive tracking), and the
 * agent-driven performance card.
 */
export function RightPanel() {
  // Goal state is SSE-driven (full-snapshot goal_updated events) plus
  // loadSessionState recovery on session switch / page load — no
  // polling (docs/goal-events-panel-link.md).
  const currentGoal = useGoalStore((s) => s.currentGoal)

  // Session / messages
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const messages = useChatStore((s) => s.messages)

  // Trace viewer toggle
  const [showTrace, setShowTrace] = useState(false)

  const sessionMessages = useMemo(() => {
    return Array.from(messages.values())
      .filter((m) => !currentSessionId || m.session_id === currentSessionId)
      .sort((a, b) => a.created_at - b.created_at)
  }, [messages, currentSessionId])

  // Latest agent-driven renderable (show_chart / show_report).
  const panelItem = useMemo(
    () => extractLatestPanelItem(sessionMessages),
    [sessionMessages],
  )

  // Full equity curve decoded from line chart parts — PanelRenderCard
  // renders it directly (recharts) when present, before the metrics
  // fallback (Tier B P7).
  const curve = useMemo(
    () => extractEquityCurve(sessionMessages),
    [sessionMessages],
  )

  // Metrics fallback for the performance card (Tier B P7): before any
  // renderable exists, the most recent run_backtest tool_call result
  // still gives total_return / sharpe / max_drawdown.
  const metrics = useMemo(
    () => extractLatestBacktestMetrics(sessionMessages),
    [sessionMessages],
  )

  // Map GoalStore goal to the display model used by GoalTab
  const goalTabGoal: GoalTabGoal | null = currentGoal ? {
    id: currentGoal.goal_id,
    title: currentGoal.objective,
    description: '',
    status: currentGoal.status === 'complete' ? 'completed' as const
      : currentGoal.status === 'cancelled' ? 'failed' as const
      : 'active' as const,
    criteria: currentGoal.criteria.map((c) => ({
      id: c.criterion_id,
      description: c.text,
      status: c.status === 'covered' ? 'completed' as const
        : c.status === 'pending' ? 'pending' as const
        : 'in_progress' as const,
      evidence_count: c.evidence_count ?? 0,
    })),
    timeline: [],
  } : null

  return (
    <div className="flex h-full w-full flex-col gap-3 overflow-y-auto bg-slate-900 p-3">
      <TokenCard />
      {/* 目标 & 进度 — 被动跟踪本 session goal 执行情况（仅 GoalTab）。
          P6 空态诚实：无活跃 goal 时不渲染卡片（业界调研
          ux-side-panel-industry-survey-20261123 §3-P5/P6）。 */}
      {goalTabGoal && <GoalCard goal={goalTabGoal} />}
      {/* 表现曲线 — 由 chat agent 决定显示什么 (show_chart / show_report)；
          PanelRenderCard 自身在无数据时返回 null */}
      <PanelRenderCard item={panelItem} metrics={metrics} curve={curve} />
      {/* Trace Timeline — agent trace events */}
      {currentSessionId && (
        <div className="rounded-lg border border-gray-800 bg-slate-950">
          <button
            type="button"
            onClick={() => setShowTrace(!showTrace)}
            className="flex w-full items-center justify-between px-3 py-2 text-xs text-gray-400 hover:text-gray-200"
          >
            <span>Trace Timeline</span>
            <span className="text-[10px] text-gray-600">{showTrace ? "▾" : "▸"}</span>
          </button>
          {showTrace && (
            <div className="h-80 border-t border-gray-800">
              <TraceViewer sessionId={currentSessionId} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
