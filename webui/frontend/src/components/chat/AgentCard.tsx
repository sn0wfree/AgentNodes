import { useState, useEffect } from 'react'
import {
  CheckCircle, XCircle, Loader2, ChevronRight,
  Clock, Bot,
} from 'lucide-react'
import type { AgentPart } from '../../stores/chat'
import { ToolCallBlock } from './ToolCallBlock'
import { StreamingText } from './StreamingText'
import { MarkdownRenderer } from './MarkdownRenderer'
import { formatDuration } from '../../utils/time'

interface AgentCardProps {
  agentPart: AgentPart
  isStreaming?: boolean
}

const STATUS_CONFIG = {
  pending: { icon: Loader2, color: 'text-muted', bg: 'bg-surface', label: '等待中' },
  running: { icon: Loader2, color: 'text-warning-fg', bg: 'bg-warning-fill', label: '执行中' },
  completed: { icon: CheckCircle, color: 'text-success-fg', bg: 'bg-emerald-500/5', label: '完成' },
  failed: { icon: XCircle, color: 'text-danger-fg', bg: 'bg-red-500/5', label: '失败' },
} as const

export function AgentCard({ agentPart, isStreaming }: AgentCardProps) {
  const [expanded, setExpanded] = useState(isStreaming && agentPart.status === 'running')
  const [tickMs, setTickMs] = useState(0)

  // Auto-expand while running, auto-collapse when done
  useEffect(() => {
    if (agentPart.status === 'running') {
      setExpanded(true)
    } else if (agentPart.status === 'completed' || agentPart.status === 'failed') {
      setExpanded(false)
    }
  }, [agentPart.status])

  // Duration timer while running
  useEffect(() => {
    if (agentPart.status !== 'running' || !agentPart.startedAt) return
    const startMs = agentPart.startedAt * 1000
    const id = setInterval(() => setTickMs(Date.now() - startMs), 100)
    return () => clearInterval(id)
  }, [agentPart.status, agentPart.startedAt])

  const config = STATUS_CONFIG[agentPart.status] ?? STATUS_CONFIG.pending
  const StatusIcon = config.icon
  const toolCount = agentPart.toolCalls.length
  const doneCount = agentPart.toolCalls.filter((tc) => tc.status === 'done').length
  const errorCount = agentPart.toolCalls.filter((tc) => tc.status === 'error').length

  const duration =
    agentPart.status === 'running' && agentPart.startedAt
      ? tickMs || Date.now() - agentPart.startedAt * 1000
      : agentPart.finishedAt && agentPart.startedAt
        ? (agentPart.finishedAt - agentPart.startedAt) * 1000
        : null

  return (
    <div
      className={`my-1 border-l-2 rounded-r-md overflow-hidden transition-colors duration-300 ${
        agentPart.status === 'running'
          ? 'border-warning/50 bg-warning-fill'
          : agentPart.status === 'failed'
          ? 'border-red-500/40 bg-red-500/5'
          : agentPart.status === 'completed'
          ? 'border-emerald-500/30 bg-emerald-500/5'
          : 'border-strong bg-surface-raised/30'
      }`}
    >
      {/* Summary line (always visible) */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setExpanded((v) => !v)
          }
        }}
        className="group flex w-full cursor-pointer items-center gap-2 px-2 py-1.5 text-left text-[12px] hover:bg-white/[0.03] transition-colors"
      >
        <ChevronRight
          className={`h-3 w-3 text-muted transition-transform duration-200 ${
            expanded ? 'rotate-90' : ''
          }`}
        />
        <StatusIcon
          className={`h-3.5 w-3.5 ${config.color} ${
            agentPart.status === 'running' ? 'animate-spin' : ''
          }`}
        />
        <Bot className="h-3.5 w-3.5 text-primary-400" />
        <span className="font-medium text-foreground">
          {agentPart.name}
        </span>
        <span className="text-muted text-[11px]">{config.label}</span>
        {toolCount > 0 && (
          <span className="text-muted text-[11px]">
            · {doneCount}/{toolCount} 工具
            {errorCount > 0 && <span className="text-danger-fg"> ({errorCount} 失败)</span>}
          </span>
        )}
        {agentPart.tokensUsed !== undefined && agentPart.tokensUsed > 0 && (
          <span className="text-muted text-[11px]">
            · {agentPart.tokensUsed.toLocaleString()} tokens
          </span>
        )}
        <span className="ml-auto flex items-center gap-2 text-[10px] text-muted">
          {duration !== null && (
            <span className="flex items-center gap-0.5 font-mono">
              <Clock className="h-2.5 w-2.5" />
              {formatDuration(duration)}
            </span>
          )}
        </span>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-strong/40 px-2 py-1.5 space-y-1">
          {/* Tool calls */}
          {agentPart.toolCalls.map((tc) => (
            <ToolCallBlock key={tc.id} toolCall={tc} />
          ))}

          {/* Streaming text */}
          {agentPart.streamingText && (
            <div className="px-1 py-1">
              {isStreaming && agentPart.status === 'running' ? (
                <StreamingText text={agentPart.streamingText} isDone={false} partId={`agent-${agentPart.id}`} />
              ) : (
                <MarkdownRenderer content={agentPart.streamingText} />
              )}
            </div>
          )}

          {/* Error */}
          {agentPart.error && (
            <div className="px-2 py-1 text-xs text-danger-fg bg-red-500/5 rounded">
              {agentPart.error}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
