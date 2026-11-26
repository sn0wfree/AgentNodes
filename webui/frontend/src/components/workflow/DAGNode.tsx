import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Clock, CheckCircle, XCircle, AlertCircle, Loader2 } from 'lucide-react'
import { useThemeStore } from '../../stores/theme'

export interface DAGNodeData {
  label: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  agentColor?: string
  agentName?: string
  type?: string
  [key: string]: unknown
}

const STATUS_CONFIG = {
  pending: {
    icon: Clock,
    borderColor: 'border-strong/70',
    iconColor: 'dag-status-pending',
    dotColor: 'dag-status-pending',
    label: '等待中',
  },
  running: {
    icon: Loader2,
    borderColor: 'border-primary-500/80',
    iconColor: 'dag-status-running',
    dotColor: 'dag-status-running',
    label: '运行中',
  },
  completed: {
    icon: CheckCircle,
    borderColor: 'border-emerald-500/70',
    iconColor: 'dag-status-completed',
    dotColor: 'dag-status-completed',
    label: '已完成',
  },
  failed: {
    icon: XCircle,
    borderColor: 'border-red-500/70',
    iconColor: 'dag-status-failed',
    dotColor: 'dag-status-failed',
    label: '失败',
  },
  skipped: {
    icon: AlertCircle,
    borderColor: 'border-strong/50',
    iconColor: 'dag-status-skipped',
    dotColor: 'dag-status-skipped',
    label: '已跳过',
  },
}

export const DAGNode = memo(function DAGNode({ data, selected }: NodeProps) {
  const nodeData = data as unknown as DAGNodeData
  const config = STATUS_CONFIG[nodeData.status || 'pending']
  const Icon = config.icon
  const isRunning = nodeData.status === 'running'
  const theme = useThemeStore((s) => s.theme)

  // Light theme needs darker variants of the type colors to keep
  // ≥4.5:1 contrast on white cards; dark theme keeps the originals.
  const LIGHT_AGENT_COLORS: Record<string, string> = {
    llm_agent: 'var(--wf-node-1)',
    planner: 'var(--wf-node-2)',
    evaluator: 'var(--wf-node-3)',
    approval: 'var(--wf-node-4)',
    python: 'var(--wf-node-5)',
    tool: 'var(--wf-node-6)',
  }
  const titleColor =
    theme === 'light' && nodeData.type && LIGHT_AGENT_COLORS[nodeData.type]
      ? LIGHT_AGENT_COLORS[nodeData.type]
      : (nodeData.agentColor ?? 'var(--dag-card-title)')

  return (
    <div
      className={`relative rounded-xl border-2 ${config.borderColor} px-4 py-3 min-w-[150px] transition-all dag-node-card ${
        selected ? 'dag-node-selected' : ''
      } ${isRunning ? 'animate-stage-pulse' : ''}`}
    >
      {/* Left color bar (agent color) */}
      {nodeData.agentColor && (
        <div
          className="absolute left-0 top-2 bottom-2 w-1 rounded-full"
          style={{ backgroundColor: nodeData.agentColor, boxShadow: '0 0 8px var(--wf-node-1-soft)' }}
        />
      )}

      {/* Header */}
      <div className="flex items-center gap-2 mb-1">
        <span
          className={`flex h-4 w-4 items-center justify-center rounded ${config.dotColor}`}
          style={{ backgroundColor: 'currentColor', opacity: 0.18 }}
        >
          <Icon
            className={`h-3.5 w-3.5 ${config.iconColor} ${isRunning ? 'animate-spin' : ''}`}
          />
        </span>
        <span className={`text-[10px] font-medium tracking-wide uppercase ${config.iconColor}`}>
          {config.label}
        </span>
      </div>

      {/* Label */}
      <div
        className="text-sm font-semibold truncate dag-card-title"
        style={{ color: titleColor }}
      >
        {nodeData.label}
      </div>

      {/* Type + agent name */}
      {nodeData.agentName && (
        <div className="mt-1.5 flex items-center gap-1">
          {nodeData.type && (
            <span
              className="rounded dag-card-type px-1 py-px text-[10px] font-mono"
              style={{ color: titleColor }}
            >
              {nodeData.type}
            </span>
          )}
          <span className="truncate text-[11px] dag-card-sub">{nodeData.agentName}</span>
        </div>
      )}

      {/* Handles */}
      <Handle
        type="target"
        position={Position.Left}
        className="!w-2 !h-2 !bg-slate-600 !border-2 !border-subtle"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!w-2 !h-2 !bg-slate-600 !border-2 !border-subtle"
      />
    </div>
  )
})