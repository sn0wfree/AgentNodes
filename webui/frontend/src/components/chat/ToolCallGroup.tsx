import { useState } from 'react'
import { ChevronRight, Wrench } from 'lucide-react'
import type { ToolCallPart } from '../../stores/chat'
import { ToolCallBlock } from './ToolCallBlock'

interface ToolCallGroupProps {
  toolCalls: ToolCallPart[]
  startTimes?: Record<string, number>
  onRetry?: (toolCall: ToolCallPart) => void
}

export function ToolCallGroup({ toolCalls, startTimes, onRetry }: ToolCallGroupProps) {
  const [expanded, setExpanded] = useState(false)

  if (toolCalls.length === 0) return null
  if (toolCalls.length === 1) {
    return (
      <ToolCallBlock
        toolCall={toolCalls[0]}
        startTime={startTimes?.[toolCalls[0].id]}
        onRetry={onRetry}
      />
    )
  }

  const running = toolCalls.filter((tc) => tc.status === 'running').length
  const done = toolCalls.filter((tc) => tc.status === 'done').length
  const error = toolCalls.filter((tc) => tc.status === 'error').length

  return (
    <div className="my-1 border-l-2 border-strong bg-surface-raised/20 rounded-r-md overflow-hidden transition-colors duration-300">
      {/* Single-line summary chip */}
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
        className="flex w-full cursor-pointer items-center gap-2 px-2 py-1.5 text-left text-[12px] hover:bg-surface transition-colors"
      >
        <ChevronRight
          className={`h-3 w-3 text-muted transition-transform duration-200 ${
            expanded ? 'rotate-90' : ''
          }`}
        />
        <Wrench className="h-3.5 w-3.5 text-muted" />
        <span className="font-medium text-secondary">
          {toolCalls.length} tool calls
        </span>
        <span className="ml-auto flex items-center gap-2 text-[10px]">
          {running > 0 && (
            <span className="flex items-center gap-1 text-warning-fg">
              <span className="h-1.5 w-1.5 rounded-full bg-warning animate-pulse" />
              {running} running
            </span>
          )}
          {done > 0 && <span className="text-success-fg">{done} done</span>}
          {error > 0 && <span className="text-danger-fg">{error} failed</span>}
        </span>
      </div>

      {/* Expanded: list of individual tool calls */}
      {expanded && (
        <div className="border-t border-strong/40 px-2 py-1.5 space-y-1">
          {toolCalls.map((tc) => (
            <ToolCallBlock
              key={tc.id}
              toolCall={tc}
              startTime={startTimes?.[tc.id]}
              onRetry={onRetry}
            />
          ))}
        </div>
      )}
    </div>
  )
}
