import { useState } from 'react'
import { FileCode2, ChevronDown, ChevronRight } from 'lucide-react'
import type { HtmlPart } from '../../stores/chat'

interface HtmlBlockProps {
  htmlPart: HtmlPart
}

/**
 * Agent-driven HTML report (show_report tool): renders in a sandboxed
 * iframe via srcdoc — never dangerouslySetInnerHTML. The sandbox
 * blocks scripts/forms so untrusted LLM-generated HTML cannot execute.
 */
export function HtmlBlock({ htmlPart }: HtmlBlockProps) {
  const [expanded, setExpanded] = useState(true)
  const title = htmlPart.title || 'HTML 报告'

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-subtle">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 bg-surface px-3 py-2 text-left text-xs transition-colors hover:bg-surface-raised/80"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-muted" />
        ) : (
          <ChevronRight className="h-3 w-3 text-muted" />
        )}
        <FileCode2 className="h-3.5 w-3.5 text-primary-400" />
        <span className="text-secondary">{title}</span>
      </button>
      {expanded && (
        <div className="p-2">
          <iframe
            title={title}
            sandbox=""
            srcDoc={htmlPart.content}
            className="h-[320px] w-full rounded border border-subtle bg-white"
          />
        </div>
      )}
    </div>
  )
}
