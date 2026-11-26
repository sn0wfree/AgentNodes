import { X, Bot, Clock, Wrench, FileText, Zap } from 'lucide-react'
import type { DAGNodeData } from './DAGNode'
import { statusBadgeClass, statusLabel } from '../../utils/status'

interface DAGNodeDetailProps {
  node: DAGNodeData & { id: string }
  onClose: () => void
}

export function DAGNodeDetail({ node, onClose }: DAGNodeDetailProps) {
  const prompt = typeof node.prompt === 'string' ? node.prompt : undefined
  const conditions = (() => {
    const c = node.conditions
    if (typeof c === 'string' || typeof c === 'object' && c !== null) {
      return c as string | object
    }
    return undefined
  })()
  return (
    <div className="wf-panel-solid absolute right-0 top-0 bottom-0 w-[360px] border-l z-20 flex flex-col shadow-2xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-subtle px-4 py-3">
        <div className="flex items-center gap-2">
          {node.agentColor && (
            <div
              className="h-3 w-3 rounded-full"
              style={{ backgroundColor: node.agentColor }}
            />
          )}
          <h3 className="wf-text-main text-sm font-medium">{node.label}</h3>
        </div>
        <button
          onClick={onClose}
          className="rounded-lg p-1 text-muted hover:bg-surface-raised hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Status */}
        <DetailSection title="状态" icon={<Zap className="h-3.5 w-3.5" />}>
          <StatusBadge status={node.status} />
        </DetailSection>

        {/* Agent */}
        {node.agentName && (
          <DetailSection title="Agent" icon={<Bot className="h-3.5 w-3.5" />}>
            <span className="wf-text-sub text-xs">{node.agentName}</span>
          </DetailSection>
        )}

        {/* Type */}
        {node.type && (
          <DetailSection title="类型" icon={<Wrench className="h-3.5 w-3.5" />}>
            <span className="wf-text-sub text-xs font-mono">{node.type}</span>
          </DetailSection>
        )}

        {/* Prompt / Description (from extra data) */}
        {prompt && (
          <DetailSection title="Prompt" icon={<FileText className="h-3.5 w-3.5" />}>
            <pre className="wf-text-sub wf-input text-xs whitespace-pre-wrap rounded p-2 max-h-40 overflow-y-auto">
              {prompt}
            </pre>
          </DetailSection>
        )}

        {/* Conditions */}
        {conditions && (
          <DetailSection title="条件" icon={<Clock className="h-3.5 w-3.5" />}>
            <span className="wf-text-sub text-xs">
              {JSON.stringify(conditions)}
            </span>
          </DetailSection>
        )}
      </div>
    </div>
  )
}

function DetailSection({
  title,
  icon,
  children,
}: {
  title: string
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-muted">{icon}</span>
        <span className="wf-text-sub text-[10px] uppercase tracking-wider">{title}</span>
      </div>
      {children}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const config = statusBadgeClass(status)
  const label = statusLabel(status)

  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${config}`}>
      {label}
    </span>
  )
}
