import { useRef, useState } from 'react'
import { Pencil, Check, X } from 'lucide-react'
import type { Message, MessagePart } from '../../stores/chat'
import type { ChatLayout } from '../../stores/layout'
import { useChatSessionId } from '../../contexts/ChatSessionContext'
import { useToastStore } from '../../stores/toast'
import { api } from '../../api/client'
import { formatTime } from '../../utils/time'

interface MessageBubbleProps {
  message: Message
  layout: ChatLayout
  /** Hide a trailing fenced ```json ... ``` block (e.g. the canvas DAG
   *  snapshot the orchestrator appends to each user message). */
  hideCodeTail?: boolean
}

type TextLike = string

/** Strip a single trailing fenced code block (any language tag) from a
 *  multi-line text. Returns the input untouched when no fence is found. */
function trimTrailingCodeFence(s: string): string {
  // Match an optional fence opener at end, requiring a preceding newline
  // so an opening ``` in the middle of the body is not affected.
  return s.replace(/\n```[a-zA-Z0-9_+\-]*\n[\s\S]*?```\s*$/, '')
}

function PartContent({ part, hideCodeTail }: { part: MessagePart; hideCodeTail?: boolean }) {
  if (part.type === 'text') {
    const text = hideCodeTail ? trimTrailingCodeFence(part.text) : part.text
    return <span className="whitespace-pre-wrap">{text}</span>
  }
  if (part.type === 'image') {
    return (
      <img
        src={part.url}
        alt={part.alt || ''}
        className="mt-2 max-w-full rounded-lg"
      />
    )
  }
  return null
}

function textOf(parts: MessagePart[]): string {
  return parts
    .filter((p) => p.type === 'text')
    .map((p) => (p as { text: TextLike }).text ?? '')
    .join('\n')
}

export function MessageBubble({ message, layout, hideCodeTail }: MessageBubbleProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<string>(textOf(message.parts))
  const [sending, setSending] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const currentSessionId = useChatSessionId()
  const addToast = useToastStore((s) => s.addToast)

  const startEdit = () => {
    setDraft(textOf(message.parts))
    setEditing(true)
    requestAnimationFrame(() => {
      textareaRef.current?.focus()
    })
  }

  const submitEdit = async () => {
    const content = draft.trim()
    if (!content || !currentSessionId) {
      setEditing(false)
      return
    }
    setSending(true)
    try {
      await api.post('/chat/send_async', {
        session_id: currentSessionId,
        content,
      })
      setEditing(false)
    } catch (err: any) {
      addToast('error', `发送失败：${err?.message ?? '未知错误'}`)
    } finally {
      setSending(false)
    }
  }

  const editBtn = (
    <button
      onClick={startEdit}
      className="flex h-6 w-6 items-center justify-center rounded-md border border-slate-700 bg-slate-800/80 text-slate-400 opacity-0 shadow-lg backdrop-blur transition-all hover:text-slate-100 focus:opacity-100 group-hover:opacity-100"
      title="编辑"
      aria-label="编辑消息"
    >
      <Pencil className="h-3 w-3" />
    </button>
  )

  const editingArea = (
    <div className="flex flex-col gap-2">
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={2}
        className="w-full resize-none rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-primary-500"
      />
      <div className="flex items-center gap-1">
        <button
          onClick={() => void submitEdit()}
          disabled={sending || !draft.trim()}
          className="flex items-center gap-1 rounded-md bg-primary-600 px-2 py-1 text-xs text-white transition-colors hover:bg-primary-500 disabled:opacity-40"
        >
          <Check className="h-3 w-3" />
          重发
        </button>
        <button
          onClick={() => setEditing(false)}
          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-400 transition-colors hover:bg-slate-700/40"
        >
          <X className="h-3 w-3" />
          取消
        </button>
      </div>
    </div>
  )

  const msgAny = message as unknown as { content?: string }
  const parts = message.parts ?? (msgAny.content ? [{ type: 'text' as const, text: msgAny.content }] : [])

  if (layout === 'flat') {
    return (
      <div id={`msg-${message.id}`} className="group relative px-4 py-3 transition-all">
        <div className="mb-1 flex items-center gap-2 text-xs">
          <span className="font-medium text-primary-400">You</span>
          <span className="text-slate-600">{formatTime(message.created_at)}</span>
        </div>
        {editing ? (
          editingArea
        ) : (
          <div className="pr-8 text-sm text-slate-200 leading-relaxed">
            {parts.map((part, i) => (
              <PartContent key={i} part={part} hideCodeTail={hideCodeTail} />
            ))}
          </div>
        )}
        <div className="absolute right-2 top-2">{editing ? null : editBtn}</div>
      </div>
    )
  }

  return (
    <div id={`msg-${message.id}`} className="group flex justify-end px-4 py-2 transition-all">
      <div className="relative max-w-[70%]">
        {editing ? (
          <div className="rounded-2xl border border-slate-700 bg-slate-800/60 p-3">{editingArea}</div>
        ) : (
          <div className="user-message-bubble rounded-2xl rounded-br-md px-4 py-2.5 text-sm">
            {parts.map((part, i) => (
              <PartContent key={i} part={part} hideCodeTail={hideCodeTail} />
            ))}
          </div>
        )}
        <div className="absolute -left-8 top-1.5">{editing ? null : editBtn}</div>
      </div>
    </div>
  )
}