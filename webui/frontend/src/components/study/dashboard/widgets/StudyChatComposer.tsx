/**
 * StudyChatComposer — directive input area for the study chat widget.
 *
 * Sends research directives via the directive API (optimistic).
 * Cmd/Ctrl+Enter submits; directive applies in the next researcher round.
 */
import { useState, useCallback, useRef } from 'react'
import { Send, Check } from 'lucide-react'
import { api } from '../../../../api/client'

interface StudyChatComposerProps {
  studyId: string
}

export function StudyChatComposer({
  studyId,
}: StudyChatComposerProps) {
  const [directiveText, setDirectiveText] = useState('')
  const [sending, setSending] = useState(false)
  const [hint, setHint] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null)
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearHint = useCallback(() => {
    if (hintTimer.current) clearTimeout(hintTimer.current)
    hintTimer.current = null
  }, [])

  const submitDirective = useCallback(async () => {
    const value = directiveText.trim()
    if (!value || sending) return
    setSending(true)
    setHint(null)
    clearHint()
    try {
      await api.study.directive(studyId, value, 'webui')
      setDirectiveText('')
      setHint({ type: 'ok', msg: '已提交，下轮生效' })
      hintTimer.current = setTimeout(() => setHint(null), 3000)
    } catch (err) {
      setHint({ type: 'err', msg: `提交失败: ${(err as Error).message || '未知错误'}` })
      hintTimer.current = setTimeout(() => setHint(null), 5000)
    } finally {
      setSending(false)
      taRef.current?.focus()
    }
  }, [directiveText, sending, studyId, clearHint])

  return (
    <div className="border-t border-subtle p-3">
      <div className="flex items-end gap-2">
        <textarea
          ref={taRef}
          rows={2}
          value={directiveText}
          onChange={(e) => setDirectiveText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              void submitDirective()
            }
          }}
          placeholder="输入研究指令（下轮 researcher 生效）..."
          className="flex-1 resize-none rounded-lg border border-strong bg-slate-950 px-3 py-2 text-xs text-foreground outline-none transition-shadow placeholder:text-muted focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
        />
        <button
          type="button"
          onClick={() => void submitDirective()}
          disabled={sending || !directiveText.trim()}
          className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white transition-all hover:bg-indigo-500 active:scale-95 disabled:opacity-50"
        >
          <Send className="h-3.5 w-3.5" />
          {sending ? '提交中…' : '提交'}
        </button>
      </div>

      {/* Status hint */}
      {hint && (
        <div className={`mt-2 flex items-center gap-1.5 text-[10px] ${
          hint.type === 'ok' ? 'text-success-fg' : 'text-danger-fg'
        }`}>
          {hint.type === 'ok' ? <Check className="h-3 w-3" /> : <span className="h-3 w-3">✗</span>}
          {hint.msg}
        </div>
      )}

      <div className="mt-1.5 text-[10px] text-muted">
        Cmd/Ctrl+Enter 提交 · 指令将在下一轮 researcher 中生效
      </div>
    </div>
  )
}