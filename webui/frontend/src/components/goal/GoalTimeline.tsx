import { Clock, FileText, MessageSquare, Zap } from 'lucide-react'
import { formatTimestamp } from '../../utils/time'

interface TimelineEvent {
  id: string
  type: 'evidence' | 'iteration' | 'message' | 'system'
  content: string
  timestamp: number
  agent_id?: string
  metadata?: Record<string, unknown>
}

interface GoalTimelineProps {
  events: TimelineEvent[]
}

const EVENT_ICON = {
  evidence: <FileText className="h-3 w-3 text-success-fg" />,
  iteration: <Zap className="h-3 w-3 text-warning-fg" />,
  message: <MessageSquare className="h-3 w-3 text-info-fg" />,
  system: <Clock className="h-3 w-3 text-muted" />,
}

const EVENT_DOT = {
  evidence: 'bg-success',
  iteration: 'bg-amber-500',
  message: 'bg-blue-500',
  system: 'bg-slate-500',
}

export function GoalTimeline({ events }: GoalTimelineProps) {
  // Sort by timestamp descending (newest first)
  const sorted = [...events].sort((a, b) => b.timestamp - a.timestamp)

  if (sorted.length === 0) {
    return (
      <div className="text-xs text-muted italic py-4 text-center">
        暂无活动记录
      </div>
    )
  }

  return (
    <div className="relative">
      {/* Vertical line */}
      <div className="absolute left-[11px] top-2 bottom-2 w-px bg-surface" />

      <div className="space-y-3">
        {sorted.map((event) => (
          <div key={event.id} className="relative flex gap-3">
            {/* Dot */}
            <div className={`relative z-10 mt-1 h-2.5 w-2.5 rounded-full ${EVENT_DOT[event.type]} flex-shrink-0 ring-2 ring-slate-900`} />

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 mb-0.5">
                {EVENT_ICON[event.type]}
                <span className="text-[10px] text-muted">
                  {formatTimestamp(event.timestamp)}
                </span>
                {event.agent_id && (
                  <span className="text-[10px] text-muted">
                    · {event.agent_id.slice(0, 6)}
                  </span>
                )}
              </div>
              <p className="text-xs text-secondary leading-relaxed line-clamp-2">
                {event.content}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
