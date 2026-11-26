import { useCallback, useState } from 'react'
import { RefreshCw, Copy, ThumbsUp, ThumbsDown } from 'lucide-react'
import type { Message } from '../../stores/chat'
import { useChatStore } from '../../stores/chat'
import { useChatSessionId } from '../../contexts/ChatSessionContext'
import { useToastStore } from '../../stores/toast'
import { api } from '../../api/client'

const VOTE_KEY = 'sr-msg-vote'

interface MessageActionsProps {
  message: Message
  /** When true, the bar is always visible (flat layout); otherwise
   *  it fades in on group hover. */
  alwaysVisible?: boolean
}

function buildMarkdown(message: Message): string {
  const parts: string[] = []
  for (const p of message.parts) {
    if (p.type === 'text') {
      parts.push(p.text ?? '')
    } else if (p.type === 'thinking') {
      parts.push(`<details><summary>Thinking</summary>\n\n${p.text ?? ''}\n\n</details>`)
    } else if (p.type === 'tool_call') {
      const name = (p as { name?: string }).name ?? 'tool'
      parts.push(`> 🔧 ${name}`)
    }
  }
  return parts.filter(Boolean).join('\n\n')
}

export function MessageActions({ message, alwaysVisible = false }: MessageActionsProps) {
  const addToast = useToastStore((s) => s.addToast)
  const currentSessionId = useChatSessionId()
  const messages = useChatStore((s) => s.messages)
  const [vote, setVote] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    try {
      return localStorage.getItem(`${VOTE_KEY}-${message.id}`)
    } catch {
      return null
    }
  })

  const copyAsMarkdown = useCallback(async () => {
    const md = buildMarkdown(message)
    if (!md) return
    try {
      await navigator.clipboard.writeText(md)
      addToast('success', '已复制为 Markdown')
    } catch {
      addToast('error', '复制失败')
    }
  }, [message, addToast])

  const regenerate = useCallback(async () => {
    // Find the user message immediately preceding this assistant message.
    const sorted = Array.from(messages.values()).sort(
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
      addToast('error', '找不到可重发的上一条消息')
      return
    }
    if (!currentSessionId) return
    try {
      await api.post('/chat/send_async', {
        session_id: currentSessionId,
        content,
      })
      addToast('success', '已重发')
    } catch (err: any) {
      addToast('error', `重发失败：${err?.message ?? '未知错误'}`)
    }
  }, [messages, message.id, currentSessionId, addToast])

  const setVoteFor = useCallback(
    (v: 'up' | 'down') => {
      const next = vote === v ? null : v
      setVote(next)
      if (typeof window !== 'undefined') {
        try {
          if (next) localStorage.setItem(`${VOTE_KEY}-${message.id}`, next)
          else localStorage.removeItem(`${VOTE_KEY}-${message.id}`)
        } catch {
          /* ignore */
        }
      }
    },
    [vote, message.id],
  )

  const btn =
    'flex h-6 w-6 items-center justify-center rounded-md border border-strong bg-surface-raised/80 text-muted opacity-0 shadow-lg backdrop-blur transition-all hover:text-foreground group-hover:opacity-100'
  const visibleBtn = btn.replace(' opacity-0', '')

  return (
    <div
      className={`flex items-center gap-0.5 ${alwaysVisible ? '' : 'opacity-0 group-hover:opacity-100'}`}
    >
      <button
        onClick={() => void regenerate()}
        className={alwaysVisible ? visibleBtn : btn}
        title="重新生成"
        aria-label="重新生成"
      >
        <RefreshCw className="h-3 w-3" />
      </button>
      <button
        onClick={() => void copyAsMarkdown()}
        className={alwaysVisible ? visibleBtn : btn}
        title="复制为 Markdown"
        aria-label="复制为 Markdown"
      >
        <Copy className="h-3 w-3" />
      </button>
      <button
        onClick={() => setVoteFor('up')}
        className={`${alwaysVisible ? visibleBtn : btn} ${vote === 'up' ? 'text-success-fg hover:text-success-fg' : ''}`}
        title="有帮助"
        aria-label="有帮助"
      >
        <ThumbsUp className="h-3 w-3" />
      </button>
      <button
        onClick={() => setVoteFor('down')}
        className={`${alwaysVisible ? visibleBtn : btn} ${vote === 'down' ? 'text-danger-fg hover:text-danger-fg' : ''}`}
        title="不准确"
        aria-label="不准确"
      >
        <ThumbsDown className="h-3 w-3" />
      </button>
    </div>
  )
}