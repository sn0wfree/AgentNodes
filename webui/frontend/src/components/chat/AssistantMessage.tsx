import { useCallback, useMemo } from 'react'
import type { Message, MessagePart, ToolCallPart } from '../../stores/chat'
import type { ChatLayout } from '../../stores/layout'
import { useSystemStore } from '../../stores/system'
import { useChatSessionId } from '../../contexts/ChatSessionContext'
import { useToastStore } from '../../stores/toast'
import { api } from '../../api/client'
import {
  splitTextIncremental,
  shouldSplitInline,
} from '../../utils/thinkingParsers/incremental'
import { smoothBuffer } from '../../utils/mastraSmoothStream'
import { MarkdownRenderer } from './MarkdownRenderer'
import { ToolCallBlock } from './ToolCallBlock'
import { ToolCallGroup } from './ToolCallGroup'
import { ThinkingBlock } from './ThinkingBlock'
import { JsonActionCard, parseStructuredContent } from './JsonActionCard'
import { FileEditBlock } from './FileEditBlock'
import { TableBlock } from './TableBlock'
import { ChartBlock } from './ChartBlock'
import { ImageBlock } from './ImageBlock'
import { HtmlBlock } from './HtmlBlock'
import { AgentCard } from './AgentCard'
import { StreamingText } from './StreamingText'
import { MessageActions } from './MessageActions'
import { formatTime } from '../../utils/time'
import { useChatStore } from '../../stores/chat'
import { getAgentStyle } from '../study/agentStyles'
import { getAssistantConfig } from './chatUiConfig'

interface AssistantMessageProps {
  message: Message
  isStreaming?: boolean
  /**
   * Kept for back-compat with MessageList's current call site — no
   * longer used for rendering. Each part carries its own
   * `isStreaming` flag (set by the SSE handlers), and the live tail
   * of any text part is read from the per-part preview buffer
   * (`partTextAccumDelta[part.id]`).
   */
  streamingText?: string
  isQueued?: boolean
  layout: ChatLayout
  /**
   * When true, hide all interactive affordances (regenerate, edit,
   * copy-as-source). Used by the study page's agent detail modal —
   * the user can inspect but cannot rewrite a historical agent run.
   */
  readOnly?: boolean
}


/**
 * Indicator shown while an assistant message is queued behind an
 * in-flight attempt on the same session. Displays a pulsing dot and
 * "等待中... {position}/{length}" so the user knows their message was
 * accepted and is queued.
 */
function QueuedIndicator({
  queuePosition,
  queueLength,
}: {
  queuePosition?: number
  queueLength?: number
}) {
  const pos = queuePosition ?? 1
  const len = queueLength ?? pos
  return (
    <div
      className="inline-flex items-center gap-2 rounded-md border border-subtle bg-surface px-3 py-2 text-xs text-muted animate-pulse"
      aria-label={`消息排队中，第 ${pos} 位，共 ${len} 条`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
      <span>
        等待中... {pos}/{len}
      </span>
    </div>
  )
}

/** Shape of the L2 claim-validation result attached to message metadata. */
interface ClaimValidation {
  ok: boolean
  total_claims: number
  verified: string[]
  unverified: string[]
  confidence: number
  detail: string
}

/**
 * Verifiability badge — surfaces whether the assistant's metric claims
 * trace back to real tool results (truthfulness L3).
 *
 * - no metadata / ok / no claims → rendered nothing
 * - confidence >= 0.5 → 🟡 yellow (some unverified)
 * - confidence < 0.5  → 🔴 red (mostly unverified)
 */
function VerifiabilityBadge({ cv }: { cv?: ClaimValidation }) {
  if (!cv || cv.ok || cv.total_claims === 0) return null
  const red = cv.confidence < 0.5
  return (
    <span
      className={`inline-flex h-3.5 w-3.5 items-center justify-center rounded-full text-[8px] font-bold leading-none ${
        red
          ? 'bg-danger-surface text-danger-fg ring-1 ring-red-500/40'
          : 'bg-warning-fill text-warning-fg ring-1 ring-warning/40'
      }`}
      title={cv.detail}
      aria-label={cv.detail}
    >
      {red ? '!' : '?'}
    </span>
  )
}

/**
 * Read a part's current text, preferring the short-lived per-part
 * streaming preview buffer (so the first byte of a new part is
 * visible immediately) and falling back to the persistent text once
 * the part has been sealed by text.ended / thinking_end / tool_result.
 *
 * Applies Mastra-style word-boundary buffering (`smoothBuffer`) so a
 * half-arrived English word (e.g. ``'expl'``) isn't rendered with
 * its leading space lost — the partial tail is held back until the
 * next chunk completes the word.
 *
 * When ``isStreaming`` is false (the part has been sealed by
 * text.ended / thinking_end / tool_result) we skip the smoothBuffer
 * hold-back: at done, there's no next chunk coming, so the partial
 * word must render verbatim.
 *
 * Modeled on opencode's `readPartText(accum, part)` + Mastra's
 * `smoothStream` (Apache 2.0).
 */
function useReadPartText(): (
  partId: string,
  fallback: string,
  isStreaming?: boolean,
) => string {
  const partTextAccumDelta = useChatStore((s) => s.partTextAccumDelta)
  return (partId, fallback, isStreaming = true) => {
    const raw = partTextAccumDelta[partId] ?? fallback ?? ''
    return isStreaming ? smoothBuffer(raw).stable : raw
  }
}

/**
 * Build the render-time list of parts from a message.
 *
 * For inline-thinking providers (e.g. minimax) the text part is split
 * *incrementally* on every change — as soon as `<think>` lands a
 * thinking part is mounted; as soon as `</think>` lands a text part
 * is mounted. For providers that use a separate reasoning_content
 * field (DeepSeek / Qwen / Kimi / OpenAI) the backend already emits
 * a dedicated thinking part and no client-side split is needed.
 *
 * The `isStreaming` flag is preserved on the rendered parts so the
 * renderer can pick the animated vs static path per part.
 */
function useRenderedParts(
  parts: MessagePart[],
  provider: string | null,
  isStreaming: boolean | undefined,
  readPartText: (partId: string, fallback: string) => string,
): MessagePart[] {
  return useMemo(() => {
    const out: MessagePart[] = []
    if (!shouldSplitInline(provider)) {
      // Standard path — backend already split for known providers.
      // Fallback: if ANY text part still contains inline <think> tags
      // (provider context empty, or historical session from a study run),
      // fall through to the splitting logic so the tags are extracted
      // and rendered as ThinkingBlocks rather than displayed literally.
      const hasInlineTags = parts.some(
        (p) => p.type === 'text' && p.text && (
          p.text.includes('<think>') || p.text.includes('')
        ),
      )
      if (!hasInlineTags) return parts
    }
    for (const part of parts) {
      if (part.type !== 'text') {
        out.push(part)
        continue
      }
      // Inline-thinking provider: split on every render.
      // When not streaming, read raw text without smoothBuffer to
      // avoid truncating non-Latin text (e.g. Chinese) that doesn't
      // end on a word boundary.
      const text = isStreaming
        ? readPartText(part.id, part.text)
        : (part.text ?? '')
      const split = splitTextIncremental(text)
      const thinkingText = split.thinkingBefore + (split.thinkingOpen ?? '')
      if (thinkingText) {
        // Force-expand while the tag is unclosed; collapse once closed
        // (the opencode pattern — the part shows the user "what the
        // model is currently thinking" until the reasoning block ends).
        out.push({
          type: 'thinking',
          text: thinkingText,
          collapsed: split.thinkingOpen === null && !isStreaming,
          isStreaming: !!part.isStreaming,
        })
      }
      if (split.contentAfter) {
        out.push({
          type: 'text',
          id: part.id,
          text: split.contentAfter,
          isStreaming: !!part.isStreaming,
        })
      }
    }
    return out
  }, [parts, provider, isStreaming, readPartText])
}

/**
 * Per-part renderer. The streaming flag decides whether the
 * animated component (StreamingText / ThinkingBlock streaming /
 * ToolCallBlock running) or the static component is mounted.
 *
 * Note: `StreamingText` is *only* used here for a single text part —
 * the prior design replaced the entire message body with a single
 * StreamingText instance, which is what caused the "工具栏突然出现"
 * bug (thinking / tool_call parts were only mounted after agent_done).
 */
function PartRenderer({
  part,
  isStreaming,
  onRetry,
  readPartText,
  agentId,
}: {
  part: MessagePart
  isStreaming: boolean
  onRetry?: (tc: ToolCallPart) => void
  readPartText: (partId: string, fallback: string, isStreaming?: boolean) => string
  agentId?: string
}) {
  switch (part.type) {
    case 'text': {
      // isStreaming=true: smoothBuffer holds back the partial last word
      //                    so the leading space of the next chunk is
      //                    never visible before the word completes.
      // isStreaming=false (sealed): render verbatim so the final
      //                    partial word still shows.
      const liveText = readPartText(part.id, part.text, isStreaming)
      if (isStreaming) {
        return <StreamingText text={liveText} isDone={false} partId={part.id} />
      }
      // Parse into ordered text + JSON segments.  Handles:
      //   - pure JSON (action card / generic card)
      //   - pure markdown
      //   - markdown with embedded JSON blocks
      const parsed = parseStructuredContent(liveText)
      if (parsed.hasStructured) {
        return (
          <>
            {parsed.segments.map((seg, i) => {
              if (seg.kind === 'json' && seg.json) {
                return (
                  <JsonActionCard
                    key={`json-${i}`}
                    agentId={agentId}
                    action={seg.action}
                    fullJson={seg.json}
                  />
                )
              }
              return (
                <MarkdownRenderer key={`text-${i}`} content={seg.text ?? ''} />
              )
            })}
          </>
        )
      }
      return <MarkdownRenderer content={liveText} />
    }
    case 'tool_call': {
      // While the tool is in flight we still render the block but the
      // ToolCallBlock's status-driven UI (running spinner / done check)
      // handles the visual. Streaming flag is informational; the block
      // is visible from the very first `tool_call` event thanks to
      // the opencode-style "every part decides its own visibility" rule.
      return <ToolCallBlock toolCall={part} onRetry={onRetry} />
    }
    case 'thinking': {
      return (
        <ThinkingBlock
          text={part.text}
          collapsed={part.collapsed}
          streaming={isStreaming}
        />
      )
    }
    case 'file_edit':
      return <FileEditBlock fileEdit={part} />
    case 'table':
      return <TableBlock table={part} />
    case 'chart':
      return <ChartBlock chart={part} />
    case 'image':
      return <ImageBlock src={part.url} alt={part.alt} />
    case 'html':
      return <HtmlBlock htmlPart={part} />
    case 'agent':
      return <AgentCard agentPart={part} isStreaming={isStreaming} />
    default:
      return null
  }
}

export function AssistantMessage({
  message,
  isStreaming,
  isQueued,
  layout,
  readOnly,
}: AssistantMessageProps) {
  const provider = useSystemStore((s) => s.llm.provider)
  const readPartText = useReadPartText()
  const currentSessionId = useChatSessionId()
  const addToast = useToastStore((s) => s.addToast)

  /**
   * Retry a failed tool call by re-sending the user message that
   * triggered this assistant turn. Backend lacks a per-tool retry
   * endpoint; the safest fallback is to re-issue the original prompt
   * with the agent's existing context (matches MessageActions'
   * regenerate). The user keeps the failed tool_call block visible in
   * history and a fresh attempt produces a new assistant message.
   */
  const handleToolRetry = useCallback(
    async (tc: ToolCallPart) => {
      const sessionId = currentSessionId
      if (!sessionId) return
      // P0-1: read the messages Map lazily on click instead of subscribing to
      // it — a full-Map subscription re-renders every mounted message on any
      // streaming delta just to keep this retry handler warm.
      const sorted = Array.from(useChatStore.getState().messages.values()).sort(
        (a, b) => a.created_at - b.created_at,
      )
      let prevUser: Message | null = null
      for (const m of sorted) {
        if (m.id === message.id) break
        if (m.role === 'user') prevUser = m
      }
      const content = prevUser?.parts
        ?.filter((p) => p.type === 'text')
        .map((p) => (p as { text?: string }).text ?? '')
        .join('\n')
        .trim()
      if (!content) {
        addToast('error', '找不到触发该工具调用的用户消息，无法重试')
        return
      }
      try {
        await api.post('/chat/send_async', { session_id: sessionId, content })
        addToast(
          'success',
          `已重新发送，将重试「${tc.name}」`,
        )
      } catch (err: any) {
        addToast('error', `重试失败：${err?.message ?? '未知错误'}`)
      }
    },
    [currentSessionId, message.id, addToast],
  )

  // Group consecutive tool_call parts into a single ToolCallGroup
  // (matches the original design — same opencode-style "adjacent tool
  // calls share one collapsible container" rule).
  // Pass `isStreaming` so the splitter can force-expand thinking
  // blocks while the attempt is active.
  const renderedParts = useRenderedParts(
    message.parts,
    provider,
    isStreaming,
    readPartText,
  )

  const groupedParts: Array<
    { type: 'single'; part: MessagePart } | { type: 'tool_group'; calls: any[] }
  > = []
  let i = 0
  while (i < renderedParts.length) {
    const part = renderedParts[i]
    if (part.type === 'tool_call') {
      const calls: any[] = []
      while (i < renderedParts.length && renderedParts[i].type === 'tool_call') {
        calls.push(renderedParts[i])
        i++
      }
      groupedParts.push({ type: 'tool_group', calls })
    } else {
      groupedParts.push({ type: 'single', part })
      i++
    }
  }

  // Use agent styles from config
  const agentStyle = getAgentStyle(message.agent_id || '')
  const chatConfig = getAssistantConfig()

  // Label precedence: known agent → "Agent · <name>"; unknown agent but
  // with a model → model name; otherwise the generic prefix. (Never
  // render "<prefix> · Agent" for an unresolved default style.)
  const modelLabel = message.agent_id
    ? `${chatConfig.labels.modelPrefix} ${chatConfig.labels.modelSeparator} ${agentStyle.name}`
    : message.metadata?.model || chatConfig.labels.modelPrefix

  // Live status line while streaming: tool-call count (from parts) and
  // token usage (from metadata.tokens_used when the backend has pushed it).
  const toolCount = (message.parts ?? []).filter((p) => p.type === 'tool_call').length
  const statusChips = isStreaming ? (
    <span className="flex items-center gap-1.5 text-[11px] text-muted">
      {toolCount > 0 && <span>· {toolCount} 工具</span>}
      {typeof message.metadata?.tokens_used === 'number' && (
        <span>· {message.metadata.tokens_used} tokens</span>
      )}
    </span>
  ) : null

  const actions = readOnly ? null : (
    <MessageActions message={message} alwaysVisible={layout === 'flat'} />
  )

  const headerLine = (
    <div className="mb-1.5 flex items-center gap-2">
      {layout === 'bubble' && (
        <span className={`flex items-center gap-1.5 text-xs font-medium ${agentStyle.text}`}>
          {isStreaming && chatConfig.visibility.showStreamingStatus && (
            <span className={`h-1.5 w-1.5 animate-pulse rounded-full bg-${chatConfig.colors.streamingDot}`} />
          )}
          {modelLabel}
          {chatConfig.visibility.showVerifiabilityBadge && (
            <VerifiabilityBadge cv={message.metadata?.claim_validation} />
          )}
          {statusChips}
        </span>
      )}
      {layout === 'flat' && (
        <>
          <span className={`flex items-center gap-1.5 text-xs font-medium ${agentStyle.text}`}>
            {isStreaming && (
              <span className={`h-1.5 w-1.5 animate-pulse rounded-full bg-${chatConfig.colors.streamingDot}`} />
            )}
            {modelLabel}
            <VerifiabilityBadge cv={message.metadata?.claim_validation} />
            {statusChips}
          </span>
          <span className={`text-xs text-${chatConfig.colors.timestamp}`}>{formatTime(message.created_at)}</span>
        </>
      )}
      {message.agent_id && (
        <span className="text-xs text-muted">{message.agent_id}</span>
      )}
    </div>
  )

  const body = (
    <div className="text-sm text-foreground space-y-3">
      {isQueued ? (
        <QueuedIndicator
          queuePosition={message.metadata?.queue_position}
          queueLength={message.metadata?.queue_length}
        />
      ) : (
        // Stream-aware rendering: we always walk `groupedParts`, and
        // each part's `isStreaming` flag decides whether to mount the
        // animated or static variant. This is the core fix for the
        // "工具栏突然出现" bug — thinking blocks and tool_call
        // toolbars now appear the moment their first event lands,
        // not at agent_done.
        groupedParts.map((item, idx) => {
          if (item.type === 'tool_group') {
            return (
              <ToolCallGroup
                key={`group-${idx}`}
                toolCalls={item.calls}
                onRetry={handleToolRetry}
              />
            )
          }
          const part = item.part
          // Only text / tool_call / thinking carry the per-part
          // streaming flag. Static types (file_edit / table / chart
          // / image) are not part of the live stream — they always
          // render via the static PartRenderer branch.
          const isPartStreaming = isStreamingFlagOf(part)
          return (
            <PartRenderer
              key={partKeyFor(part, idx)}
              part={part}
              isStreaming={isPartStreaming}
              readPartText={readPartText}
              onRetry={handleToolRetry}
              agentId={message.agent_id || undefined}
            />
          )
        })
      )}
    </div>
  )

  if (layout === 'flat') {
    return (
      <div className="group relative px-4 py-3 border-b border-subtle/40 last:border-b-0">
        <div className="absolute right-2 top-2">{actions}</div>
        {headerLine}
        {body}
      </div>
    )
  }

  // bubble mode (default)
  return (
    <div className="group relative flex gap-3 px-4 py-2.5">
      <div className="absolute right-2 top-2.5">{actions}</div>
      <div
        className="flex shrink-0 items-center justify-center rounded-full text-white text-xs font-medium"
        style={{
          width: chatConfig.avatar.size,
          height: chatConfig.avatar.size,
          background: `linear-gradient(135deg, var(--${chatConfig.avatar.gradient[0]}), var(--${chatConfig.avatar.gradient[1]}))`,
        }}
      >
        <span className="text-sm">{agentStyle.icon}</span>
      </div>
      <div className="min-w-0 flex-1">
        {headerLine}
        {body}
      </div>
    </div>
  )
}

/** Stable React key per part — survives re-renders while the part
 *  list mutates (append / close). Falls back to idx for parts that
 *  don't have a stable id (file_edit, table, chart, image). */
function partKeyFor(part: MessagePart, idx: number): string {
  if (part.type === 'text') return `text-${part.id}`
  if (part.type === 'tool_call') return `tc-${part.id}`
  if (part.type === 'agent') return `agent-${part.id}`
  if (part.type === 'thinking') {
    // thinking parts share the same conceptual id across renders
    // (built from messageId + index in the textHandlers hooks), so
    // a stable key would require extra state. Use idx here — the
    // ThinkingBlock memoises on text + collapsed so re-mount is fine.
    return `think-${idx}`
  }
  return `${part.type}-${idx}`
}

/** Type-narrowed `isStreaming` lookup. Only the streaming
 *  part types (text / tool_call / thinking) carry the flag; static
 *  types always render in their static (non-animated) variant. */
function isStreamingFlagOf(part: MessagePart): boolean {
  return (
    part.type === 'text' ||
    part.type === 'tool_call' ||
    part.type === 'thinking' ||
    part.type === 'agent'
  )
    ? !!part.isStreaming
    : false
}
