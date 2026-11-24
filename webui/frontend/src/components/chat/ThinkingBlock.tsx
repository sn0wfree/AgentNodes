import { useState, useEffect } from 'react'
import { Sparkles, ChevronRight, Copy, Check } from 'lucide-react'
import { MarkdownRenderer } from './MarkdownRenderer'
import { formatDuration } from '../../utils/time'
import { useCopyToClipboard } from '../../hooks/useCopyToClipboard'
import { useThinkingPrefStore } from '../../stores/thinkingPref'

interface ThinkingBlockProps {
  text: string
  collapsed?: boolean
  streaming?: boolean
  startTime?: number
  endTime?: number
}

export function ThinkingBlock({
  text,
  collapsed = true,
  streaming = false,
  startTime,
  endTime,
}: ThinkingBlockProps) {
  const globalCollapsed = useThinkingPrefStore((s) => s.collapsed)
  const globalEnabled = useThinkingPrefStore((s) => s.enabled)
  const [isExpanded, setIsExpanded] = useState(!collapsed)
  const [copied, copy] = useCopyToClipboard()
  const [tickMs, setTickMs] = useState(0)

  // Sync expanded state with collapsed prop. While streaming, reasoning is
  // always shown expanded (opencode behavior); when sealed, respect the
  // global collapse preference so the user's fold state persists across
  // messages (Phase C3).
  useEffect(() => {
    setIsExpanded(streaming ? true : !(collapsed && globalCollapsed))
  }, [collapsed, streaming, globalCollapsed])

  useEffect(() => {
    if (!streaming || !startTime) return
    const id = setInterval(() => setTickMs(Date.now() - startTime), 100)
    return () => clearInterval(id)
  }, [streaming, startTime])

  // Global kill-switch AFTER hooks so the hook order stays stable
  // across enabled/disabled renders (React rules of hooks). When
  // disabled, render nothing regardless of streaming state. Persists
  // across reloads so users who want to hide reasoning entirely
  // don't have to keep folding every block.
  if (!globalEnabled) return null
  if (!text) return null

  let label: string
  if (streaming) {
    const elapsed =
      startTime !== undefined ? tickMs || Date.now() - startTime : 0
    label =
      elapsed > 0
        ? `Thinking for ${formatDuration(elapsed)}`
        : 'Thinking…'
  } else if (startTime !== undefined && endTime !== undefined) {
    label = `Thought for ${formatDuration(endTime - startTime)}`
  } else {
    label = 'Thought'
  }

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    copy(text)
  }

  return (
    <div className="my-1 border-l-2 border-violet-500/30 bg-violet-950/5 rounded-r-md overflow-hidden transition-colors duration-300">
      {/* Single-line header */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setIsExpanded(!isExpanded)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setIsExpanded((v) => !v)
          }
        }}
        className="flex w-full cursor-pointer items-center gap-1.5 px-2 py-1 text-left text-[11px] text-thinking-fg hover:bg-thinking-surface transition-colors"
      >
        <ChevronRight
          className={`h-3 w-3 text-thinking-fg-muted transition-transform duration-200 ${
            isExpanded ? 'rotate-90' : ''
          }`}
        />
        <Sparkles className="h-3 w-3 text-thinking-fg-muted" />
        <span className="font-medium">{label}</span>
        {streaming && (
          <span className="thinking-dots ml-0.5">
            <span /><span /><span />
          </span>
        )}
        <span className="ml-auto flex items-center gap-2 text-[10px] text-thinking-fg-muted">
          {text.length > 50 && <span>{text.length} 字</span>}
          <button
            type="button"
            onClick={handleCopy}
            className="cursor-pointer hover:text-thinking-fg"
            title="复制"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          </button>
        </span>
      </div>

      {/* Expanded content */}
      {isExpanded && (
        <div className="border-t border-thinking-surface px-3 py-2 max-h-72 overflow-y-auto">
          <div className="text-[11px] leading-relaxed text-thinking-fg-muted">
            <MarkdownRenderer content={text} />
          </div>
        </div>
      )}
    </div>
  )
}
