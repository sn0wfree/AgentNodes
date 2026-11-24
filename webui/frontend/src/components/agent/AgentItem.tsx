import { useState } from 'react'
import {
  Bot, ChevronDown, ChevronRight, Clock, CheckCircle,
  XCircle, AlertCircle, Zap,
} from 'lucide-react'
import type { Agent, IterationDetail } from '../../stores/agents'
import { formatTime } from '../../utils/time'
import { statusLabel } from '../../utils/status'

interface AgentItemProps {
  agent: Agent
  onSelect?: (agent: Agent) => void
  isSelected?: boolean
}

const STATUS_CONFIG = {
  pending: { icon: Clock, color: 'text-slate-500', bg: 'bg-slate-500/10' },
  running: { icon: Zap, color: 'text-warning-fg', bg: 'bg-warning-fill' },
  completed: { icon: CheckCircle, color: 'text-success-fg', bg: 'bg-success-surface' },
  failed: { icon: XCircle, color: 'text-danger-fg', bg: 'bg-danger-surface' },
  aborted: { icon: AlertCircle, color: 'text-warning-fg', bg: 'bg-warning-fill' },
} as const


function IterationRow({ iteration }: { iteration: IterationDetail }) {
  return (
    <div className="flex items-center gap-2 py-1 text-[11px]">
      <span className="w-5 text-right text-slate-500">#{iteration.iteration}</span>
      <div className="flex-1 truncate text-slate-400">
        {iteration.tool_calls.length > 0 ? (
          <span className="flex items-center gap-1">
            <Zap className="h-3 w-3 text-warning-fg" />
            {iteration.tool_calls.map((tc) => tc.name).join(', ')}
          </span>
        ) : iteration.thought ? (
          <span className="italic">思考中...</span>
        ) : (
          <span className="text-slate-600">-</span>
        )}
      </div>
      <span className="text-slate-600">{formatTime(iteration.timestamp)}</span>
    </div>
  )
}

export function AgentItem({ agent, onSelect, isSelected }: AgentItemProps) {
  const [expanded, setExpanded] = useState(false)
  const config = STATUS_CONFIG[agent.status] ?? STATUS_CONFIG.pending
  const Icon = config.icon
  const isRunning = agent.status === 'running'

  return (
    <div
      className={`rounded-lg border transition-colors ${
        isSelected
          ? 'border-primary-500/50 bg-primary-500/5'
          : 'border-slate-700/50 hover:border-slate-600'
      }`}
    >
      {/* Main row */}
      <button
        onClick={() => {
          setExpanded(!expanded)
          onSelect?.(agent)
        }}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left"
      >
        {/* Color dot + status icon */}
        <div className="relative">
          <div
            className="h-7 w-7 rounded-full flex items-center justify-center"
            style={{ backgroundColor: agent.color || '#3b82f6' }}
          >
            <Bot className="h-3.5 w-3.5 text-white" />
          </div>
          <div className={`absolute -bottom-0.5 -right-0.5 rounded-full p-0.5 ${config.bg}`}>
            <Icon className={`h-2.5 w-2.5 ${config.color}`} />
          </div>
        </div>

        {/* Name + status */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-slate-200 truncate">
              {agent.name || agent.id.slice(0, 8)}
            </span>
            {isRunning && (
              <span className="h-1.5 w-1.5 rounded-full bg-warning animate-pulse" />
            )}
          </div>
          <div className="flex items-center gap-2 text-[10px] text-slate-500">
            <span className={config.color}>{statusLabel(agent.status)}</span>
            {agent.tool_calls_count > 0 && (
              <span>{agent.tool_calls_count} 次调用</span>
            )}
            {agent.iterations_detail.length > 0 && (
              <span>{agent.iterations_detail.length} 轮迭代</span>
            )}
          </div>
        </div>

        {/* Expand arrow */}
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-slate-500" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-slate-500" />
        )}
      </button>

      {/* Expanded: iteration history */}
      {expanded && (
        <div className="border-t border-slate-700/50 px-3 py-2 space-y-0.5">
          {agent.iterations_detail.length === 0 ? (
            <div className="text-[11px] text-slate-600 italic py-1">暂无迭代记录</div>
          ) : (
            agent.iterations_detail.map((iter) => (
              <IterationRow key={iter.iteration} iteration={iter} />
            ))
          )}

          {/* Metrics footer */}
          <div className="flex items-center gap-3 pt-1.5 mt-1 border-t border-slate-700/30 text-[10px] text-slate-600">
            {agent.context_tokens > 0 && (
              <span>上下文: {Math.round(agent.context_tokens / 1000)}K</span>
            )}
            {agent.compaction_count > 0 && (
              <span>
                压缩: {agent.compaction_count}次
                {agent.last_compaction ? ` · ${agent.last_compaction.layer}` : ''}
              </span>
            )}
            {agent.finished_reason && (
              <span>退出: {agent.finished_reason}</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
