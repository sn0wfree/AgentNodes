import { useState, useRef, useCallback, useEffect, useLayoutEffect } from 'react'
import { Send, Image as ImageIcon, X, Square, AlertTriangle } from 'lucide-react'
import { useChatStore } from '../../stores/chat'
import { useChatSessionId } from '../../contexts/ChatSessionContext'
import { usePersonaStore } from '../../stores/personas'
import { useToastStore } from '../../stores/toast'
import { useModeStore } from '../../stores/mode'
import { useModelStore } from '../../stores/model'
import { useThinkingPrefStore } from '../../stores/thinkingPref'
import { api } from '../../api/client'
import { uuid } from '../../utils/uuid'
import {
  wrapSelection,
  applyLinePrefix,
  type WrapKind,
  type LinePrefixKind,
} from '../../utils/markdownShortcuts'
import { ComposerStatusBar } from './ComposerStatusBar'
import { SlashCommandMenu } from './SlashCommandMenu'
import { ContextRing, BreakdownList } from './ContextRing'
import { useContextUsage } from '../../hooks/useContextUsage'
import { useSystemStore } from '../../stores/system'
import { tokenTone } from '../../utils/tokenThresholds'
import { formatTokens } from '../../utils/contextUsage'

/** 稳定空对象：避免 zustand selector 每次返回新引用导致无限重渲染。 */
const EMPTY_SEGMENTS: Record<string, number> = {}

export interface ComposerProps {
  /** Transform the user-typed text before it is sent to the server.
   *  Used by the orchestrator panel to append a ```json snapshot of
   *  the current canvas DAG to every user message. */
  composeMessage?: (raw: string) => string
  /** Disable attachments / slash commands — orchestrator mode doesn't
   *  use them. Image attachments, slash-command menu and drag-drop
   *  upload are hidden. */
  readOnly?: 'image'
}

export function Composer({ composeMessage, readOnly }: ComposerProps = {}) {
  const [text, setText] = useState('')
  const [images, setImages] = useState<string[]>([])
  const [sending, setSending] = useState(false)
  const [slashQuery, setSlashQuery] = useState<string | null>(null)
  /**
   * When the user picks an ``autoSend: true`` slash command from the
   * menu, we set this flag and let the useEffect below pick it up
   * once the textarea has actually been updated to the new text.
   * Driving the send through useEffect (rather than a
   * requestAnimationFrame) keeps handleSend reading the latest
   * committed state — same model as Enter-key send.
   */
  const [pendingAutoSend, setPendingAutoSend] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const currentSessionId = useChatSessionId()
  const activeSessionRef = useRef(currentSessionId)
  // 设计定稿 2026-11-25：用量环移到卡片右上角（hover 向上弹明细）。
  const usage = useContextUsage(currentSessionId)
  const modelInfo = useSystemStore((s) => s.modelInfo)
  const breakdown = useChatStore((s) =>
    currentSessionId ? s.contextBreakdown.get(currentSessionId) ?? EMPTY_SEGMENTS : EMPTY_SEGMENTS,
  )
  const addMessage = useChatStore((s) => s.addMessage)
  const streamingMessageId = useChatStore((s) => s.streamingMessageId)
  const setActiveAttempt = useChatStore((s) => s.setActiveAttempt)
  const cancelAttempt = useChatStore((s) => s.cancelAttempt)
  const addToast = useToastStore((s) => s.addToast)
  const getSessionPersona = usePersonaStore((s) => s.getSessionPersona)
  const mode = useModeStore((s) => s.mode)
  const loadSessionMode = useModeStore((s) => s.loadSessionMode)
  const getModelForSession = useModelStore((s) => s.getModelForSession)
  const thinkingMode = useThinkingPrefStore((s) => s.thinkingMode)

  const isStreaming = streamingMessageId !== null

  const draftKey = currentSessionId ? `sr-draft-${currentSessionId}` : null

  useLayoutEffect(() => {
    activeSessionRef.current = currentSessionId
    return () => {
      activeSessionRef.current = null
    }
  }, [currentSessionId])

  // Load persisted mode when switching sessions
  useEffect(() => {
    if (currentSessionId) {
      loadSessionMode(currentSessionId)
    }
  }, [currentSessionId, loadSessionMode])

  // Restore draft when switching sessions
  useEffect(() => {
    if (!draftKey) {
      setText('')
      return
    }
    setText(localStorage.getItem(draftKey) ?? '')
  }, [draftKey])

  // Persist draft (debounced); empty text clears the draft
  useEffect(() => {
    if (!draftKey) return
    if (!text.trim()) {
      localStorage.removeItem(draftKey)
      return
    }
    const t = setTimeout(() => localStorage.setItem(draftKey, text), 300)
    return () => {
      clearTimeout(t)
      if (activeSessionRef.current !== currentSessionId) {
        localStorage.setItem(draftKey, text)
      }
    }
  }, [text, draftKey, currentSessionId])

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
    }
  }, [text])

  const handleSend = useCallback(async () => {
    if (sending) return
    if (!text.trim() && images.length === 0) return
    if (!currentSessionId) return

    const content = (composeMessage ?? ((s) => s))(text.trim())
    const messageImages = [...images]
    const persona = currentSessionId ? getSessionPersona(currentSessionId) : undefined
    if (draftKey) localStorage.removeItem(draftKey)
    setText('')
    setImages([])
    setSending(true)
    setSlashQuery(null)

    // Optimistic: add user message to store immediately with a local temp id.
    const tempUserId = uuid('msg')
    addMessage({
      id: tempUserId,
      session_id: currentSessionId,
      role: 'user',
      parts: [
        ...(content ? [{ type: 'text' as const, id: `seed-${tempUserId}`, text: content }] : []),
        ...messageImages.map((url) => ({ type: 'image' as const, url })),
      ],
      created_at: Date.now() / 1000,
    })

    try {
      const res = await api.post<{
        message_id: string
        user_message_id?: string
        assistant_message_id?: string
        attempt_id?: string
        status: string
      }>('/chat/send_async', {
        session_id: currentSessionId,
        content,
        images: messageImages.length > 0 ? messageImages : undefined,
        agent_id: persona && persona !== 'chat' ? persona : undefined,
        mode,
        model: currentSessionId ? getModelForSession(currentSessionId) : undefined,
        thinking: thinkingMode,
      })

      // Store attempt_id for cancel support
      if (res.attempt_id) {
        setActiveAttempt(res.attempt_id)
      }

      // Rename optimistic user message → backend's user_message_id.
      // If the message_received SSE already delivered the backend
      // message (with authoritative created_at/parts), keep THAT one —
      // overwriting it with the local optimistic copy would clobber
      // server data and can flip the user/assistant sort order (B9).
      const userMsgId = res.user_message_id || res.message_id
      if (userMsgId && userMsgId !== tempUserId) {
        useChatStore.setState((state) => {
          const local = state.messages.get(tempUserId)
          if (local && !state.messages.has(userMsgId)) {
            state.messages.delete(tempUserId)
            state.messages.set(userMsgId, { ...local, id: userMsgId })
          } else if (local && state.messages.has(userMsgId)) {
            // Backend copy already present — drop the temp placeholder
            state.messages.delete(tempUserId)
          }
        })
      }
    } catch (err: any) {
      console.error('Send failed:', err)
      if (activeSessionRef.current === currentSessionId) {
        setText((draft) => [text, draft].filter(Boolean).join('\n\n'))
        setImages((draft) => [...messageImages, ...draft])
      } else if (text && draftKey) {
        const draft = localStorage.getItem(draftKey) ?? ''
        localStorage.setItem(draftKey, [text, draft].filter(Boolean).join('\n\n'))
      }
      const status = err?.status
      const detail = err?.detail
      if (status === 429 && detail?.error === 'queue_full') {
        addToast(
          'error',
          `队列已满（上限 ${detail.limit} 条），请等待完成或取消当前轮次`,
        )
      } else if (err?.message) {
        addToast('error', `发送失败：${err.message}`)
      }
      useChatStore.setState((state) => {
        state.messages.delete(tempUserId)
      })
    } finally {
      setSending(false)
    }
  }, [text, images, currentSessionId, draftKey, composeMessage, sending, addMessage, setActiveAttempt, getSessionPersona, addToast, mode, getModelForSession, thinkingMode])

  const handleCancel = useCallback(() => {
    cancelAttempt()
  }, [cancelAttempt])

  const applyMarkdown = useCallback((kind: WrapKind) => {
    const ta = textareaRef.current
    if (!ta) return
    const r = wrapSelection(text, ta.selectionStart, ta.selectionEnd, kind)
    setText(r.text)
    setSlashQuery(null)
    // Restore focus + selection after state update
    requestAnimationFrame(() => {
      ta.focus()
      ta.setSelectionRange(r.selectionStart, r.selectionEnd)
    })
  }, [text])

  /** 行前缀型（列表 / 引用）——前缀落在整行行首，多行选中逐行加。 */
  const applyLinePrefixTo = useCallback((kind: LinePrefixKind) => {
    const ta = textareaRef.current
    if (!ta) return
    const r = applyLinePrefix(text, ta.selectionStart, ta.selectionEnd, kind)
    setText(r.text)
    setSlashQuery(null)
    requestAnimationFrame(() => {
      ta.focus()
      ta.setSelectionRange(r.selectionStart, r.selectionEnd)
    })
  }, [text])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Markdown 快捷键（R-014 F3）：只在输入框内生效。
    // 包裹型必须有选区才触发（无选区不插空标记，避免「****」噪音）；
    // 行前缀型无选区也生效（给当前行加前缀是明确意图）。
    // 注意 Cmd/Ctrl+B 在输入框内让位给「粗体」，输入框外仍是全局「切换右侧面板」。
    const mod = e.metaKey || e.ctrlKey
    if (mod && !e.altKey) {
      const key = e.key.toLowerCase()
      const hasSelection = textareaRef.current
        ? textareaRef.current.selectionStart !== textareaRef.current.selectionEnd
        : false
      const wrapMap: Record<string, WrapKind> = {
        b: 'bold',
        i: 'italic',
        e: 'code',
      }
      if (hasSelection && wrapMap[key]) {
        e.preventDefault()
        e.stopPropagation()
        applyMarkdown(wrapMap[key])
        return
      }
      if (e.shiftKey) {
        // 行前缀型用 e.code（物理键位）判定：Shift+8 的 e.key 是 "*"、
        // Shift+7 是 "&"（美式键盘），按 e.key 匹配会漏。
        const lineMap: Record<string, LinePrefixKind> = {
          Digit7: 'ol',
          Digit8: 'ul',
          Period: 'quote',
        }
        const lineKind = lineMap[e.code]
        if (lineKind) {
          e.preventDefault()
          e.stopPropagation()
          applyLinePrefixTo(lineKind)
          return
        }
      }
    }

    // Slash command menu navigation
    if (slashQuery !== null) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('sr:slash-nav', { detail: 1 }))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('sr:slash-nav', { detail: -1 }))
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setSlashQuery(null)
        return
      }
      if (e.key === 'Enter') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('sr:slash-enter'))
        return
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (isStreaming) {
        handleCancel()
      } else {
        handleSend()
      }
    }
  }

  // Track typing to open/close the slash command menu
  const handleChange = (v: string) => {
    setText(v)
    if (readOnly) return
    const ta = textareaRef.current
    if (ta) {
      const caret = ta.selectionStart ?? v.length
      // Find the extent of the current "word" being typed after the last
      // whitespace to decide whether to show the menu.
      const before = v.slice(0, caret)
      const m = /(\/[\w]*)$/.exec(before)
      if (m && m[1].startsWith('/')) {
        setSlashQuery(m[1])
      } else {
        setSlashQuery(null)
      }
    }
  }

  const selectSlashCommand = useCallback((command: string, autoSend?: boolean) => {
    // Replace the partial `/xxx` token with the full command
    setText((prev) => {
      const m = /(\/[\w]*)$/.exec(prev)
      if (m) return prev.slice(0, m.index) + command + ' '
      return prev + command + ' '
    })
    setSlashQuery(null)
    if (autoSend) {
      // Flag the intent — the useEffect below fires handleSend once
      // `text` has actually been committed by React. Reading `text`
      // from the next render (vs the closure here) avoids a race
      // where handleSend still sees the pre-command draft.
      setPendingAutoSend(true)
      return
    }
    textareaRef.current?.focus()
  }, [])

  // Drain the pendingAutoSend flag once the new draft has been
  // committed to state. Mirrors the Enter-key send path: the effect
  // runs after `text` settles, then calls handleSend exactly once.
  useEffect(() => {
    if (!pendingAutoSend) return
    if (sending) return
    if (!currentSessionId) {
      setPendingAutoSend(false)
      return
    }
    setPendingAutoSend(false)
    void handleSend()
  }, [pendingAutoSend, text, sending, currentSessionId, handleSend])

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData.items)
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault()
        const blob = item.getAsFile()
        if (blob && blob.size <= 5 * 1024 * 1024) {
          const reader = new FileReader()
          reader.onload = () => {
            setImages((prev) => [...prev, reader.result as string])
          }
          reader.readAsDataURL(blob)
        }
      }
    }
  }, [])

  const handleFileSelect = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file && file.size <= 5 * 1024 * 1024) {
      const reader = new FileReader()
      reader.onload = () => {
        setImages((prev) => [...prev, reader.result as string])
      }
      reader.readAsDataURL(file)
    }
    e.target.value = ''
  }, [])

  return (
    <div className="border-t border-slate-800 bg-slate-900/80 p-4">
      {/* Hidden file input reused for image attachments */}
      {!readOnly && (
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          onChange={handleFileChange}
          className="hidden"
        />
      )}
      {/* Image previews */}
      {images.length > 0 && (
        <div className="mb-2 flex gap-2">
          {images.map((src, i) => (
            <div key={i} className="relative h-16 w-16">
              <img src={src} alt="" className="h-full w-full rounded-lg object-cover" />
              <button
                onClick={() => setImages((prev) => prev.filter((_, j) => j !== i))}
                className="absolute -right-1.5 -top-1.5 rounded-full bg-slate-700 p-0.5 text-slate-300 hover:text-white"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Slash command menu (anchored above the toolbar row) */}
      <div className="relative">
        {!readOnly && slashQuery !== null && (
          <SlashCommandMenu query={slashQuery} onSelect={selectSlashCommand} />
        )}
      </div>

      {/* Composer 卡片（R-014 G0-A）：对齐 ZCode data-composer-layout ——
          一个大圆角容器包住「编辑器行 + 工具条行（状态行移入框内）」。
          focus-within 时边框/底色高亮，与 ZCode 的 focus-within 语义一致。 */}
      <div
        className="glass relative flex flex-col gap-2 rounded-2xl border-[0.5px] border-slate-700/60 bg-slate-900/40 px-4 py-3 transition-colors hover:border-slate-600 focus-within:border-sky-500/60"
        data-composer-leading-content
      >
        {/* 上下文用量环：卡片右上角绝对定位（hover 向上弹七段明细） */}
        {usage.hasLimit && modelInfo && (
          <div className="group/usage absolute right-4 top-3 z-10" data-testid="composer-context-ring-wrap">
            <div aria-hidden="true" data-testid="composer-context-ring" className="relative">
              <ContextRing segments={EMPTY_SEGMENTS} percent={usage.percent} tone={tokenTone(usage.percent)} size={20} />
              {usage.isStale && (
                <span
                  className="absolute -right-1 -top-1 text-warning-fg"
                  title="模型信息来自内置/fallback 数据，非最新抓取"
                  data-testid="composer-context-stale"
                >
                  <AlertTriangle className="h-2.5 w-2.5" />
                </span>
              )}
            </div>
            <div className="glass invisible absolute bottom-full right-0 z-50 mb-2 w-72 rounded-xl border border-slate-700/60 p-3 opacity-0 shadow-2xl transition-opacity group-hover/usage:visible group-hover/usage:opacity-100" data-testid="context-breakdown-popover">
              <div className="mb-2 flex items-center justify-between text-[11px]">
                <span className="font-medium text-slate-200">上下文用量</span>
                <span className="font-mono text-slate-400">
                  {formatTokens(usage.usedTokens)} / {formatTokens(usage.limitTokens)} ({usage.percent.toFixed(1)}%)
                </span>
              </div>
              <BreakdownList segments={breakdown} />
              <div className="mt-2 border-t border-slate-700/60 pt-2 text-[10px] text-slate-500">
                剩余可用 {formatTokens(usage.remainingTokens)} · {usage.source === 'measured' ? '后端实测' : '后端估算'}
              </div>
            </div>
          </div>
        )}
        {/* 编辑器行（textarea 与发送按钮同行；发送另绝对定位右下角，行尾占位避免遮挡） */}
        <div className="flex items-end gap-2" data-composer-trailing-actions>
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => handleChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={readOnly ? undefined : handlePaste}
            placeholder={
              isStreaming
                ? '正在生成中... (Enter 停止)'
                : currentSessionId
                  ? '输入消息... (Shift+Enter 换行)'
                  : '选择或创建会话'
            }
            disabled={!currentSessionId}
            rows={1}
            className="min-w-0 flex-1 resize-none bg-transparent pr-10 text-sm text-slate-100 placeholder-slate-500 outline-none disabled:opacity-50"
          />
          {/* 上传 + 发送/停止：发送绝对定位卡片右下角（设计定稿 2026-11-25），上传保持行内 */}
          <div className="flex items-center gap-1 pb-0.5">
            {!readOnly && (
              <button
                onClick={handleFileSelect}
                disabled={isStreaming}
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-700/50 hover:text-slate-200 transition-colors disabled:opacity-30"
                title="上传图片"
              >
                <ImageIcon className="h-4 w-4" />
              </button>
            )}
            {isStreaming ? (
              <button
                onClick={handleCancel}
                className="absolute bottom-3 right-4 rounded-full bg-red-600 p-2 text-white hover:bg-red-700 transition-colors"
                title="停止生成"
                aria-label="停止生成"
              >
                <Square className="h-4 w-4" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={sending || (!text.trim() && images.length === 0) || !currentSessionId}
                className="absolute bottom-3 right-4 rounded-full bg-sky-500 p-2 text-white transition-colors hover:bg-sky-400 disabled:opacity-30"
                title="发送 (Enter)"
                data-testid="composer-send"
              >
                <Send className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* 工具条行（框内底部）*/}
        <ComposerStatusBar sessionId={currentSessionId} />
      </div>
    </div>
  )
}
