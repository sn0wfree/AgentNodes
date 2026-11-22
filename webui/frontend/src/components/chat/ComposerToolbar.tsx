import { useEffect, useRef, useState } from 'react'
import {
  Bold,
  Italic,
  Code,
  Code2,
  List,
  ListOrdered,
  Link2,
  Quote,
  ChevronDown,
  Bot,
} from 'lucide-react'
import { usePersonaStore } from '../../stores/personas'
import type { ChatPersona } from '../../api/client'

interface ComposerToolbarProps {
  sessionId: string | null
  onApplyMarkdown: (prefix: string, suffix?: string) => void
}

export function ComposerToolbar({ sessionId, onApplyMarkdown }: ComposerToolbarProps) {
  const personas = usePersonaStore((s) => s.personas)
  const loadPersonas = usePersonaStore((s) => s.loadPersonas)
  const getSessionPersona = usePersonaStore((s) => s.getSessionPersona)
  const setSessionPersona = usePersonaStore((s) => s.setSessionPersona)

  const personaId = sessionId ? getSessionPersona(sessionId) : 'chat'
  const selected: ChatPersona | undefined =
    personas.find((p) => p.id === personaId) ?? personas.find((p) => p.id === 'chat')

  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void loadPersonas()
  }, [loadPersonas])

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  return (
    <div className="mb-2 flex flex-wrap items-center gap-1">
      {/* Persona selector */}
      <div className="relative" ref={menuRef}>
        {(() => {
          const isOrchestrator = sessionId?.startsWith('dag:') ?? false
          return (
            <>
        <button
          type="button"
          onClick={() => !isOrchestrator && setOpen((o) => !o)}
          className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-slate-300 transition-colors hover:bg-slate-700/40 disabled:opacity-40"
          disabled={!sessionId}
          title={isOrchestrator ? '编排会话固定使用编排助手人设' : '选择对话智能体'}
        >
          <Bot className="h-3.5 w-3.5 text-primary-400" />
          <span className="font-medium">{selected?.name ?? (isOrchestrator ? '编排助手' : '通用助手')}</span>
          {!isOrchestrator && (
            <ChevronDown className={`h-3 w-3 text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
          )}
        </button>
        {open && (
          <div className="glass absolute left-0 bottom-full z-50 mb-1 w-64 overflow-hidden rounded-xl border border-slate-700/60 shadow-2xl">
            <ul className="max-h-64 overflow-y-auto py-1">
              {personas.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => {
                      if (sessionId) setSessionPersona(sessionId, p.id)
                      setOpen(false)
                    }}
                    className={`flex w-full items-start gap-2 px-3 py-2 text-left transition-colors ${
                      p.id === personaId
                        ? 'bg-slate-700/40 text-slate-100'
                        : 'text-slate-300 hover:bg-slate-700/30'
                    }`}
                  >
                    <span className="mt-0.5 text-primary-400">
                      <Bot className="h-3.5 w-3.5" />
                    </span>
                    <span className="flex-1">
                      <span className="block text-xs font-medium">{p.name}</span>
                      <span className="block text-[11px] text-slate-500">
                        {p.description}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
            </>
          )
        })()}
      </div>

      <div className="mx-1 h-4 w-px bg-slate-700/60" />

      {/* Markdown quick actions */}
      <button
        type="button"
        onClick={() => onApplyMarkdown('**', '**')}
        className="rounded px-1.5 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
        title="粗体"
      >
        <Bold className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={() => onApplyMarkdown('_', '_')}
        className="rounded px-1.5 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
        title="斜体"
      >
        <Italic className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={() => onApplyMarkdown('`', '`')}
        className="rounded px-1.5 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
        title="行内代码"
      >
        <Code className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={() => onApplyMarkdown('```\n', '\n```')}
        className="rounded px-1.5 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
        title="代码块"
      >
        <Code2 className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={() => onApplyMarkdown('- ')}
        className="rounded px-1.5 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
        title="无序列表"
      >
        <List className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={() => onApplyMarkdown('1. ')}
        className="rounded px-1.5 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
        title="有序列表"
      >
        <ListOrdered className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={() => onApplyMarkdown('> ')}
        className="rounded px-1.5 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
        title="引用"
      >
        <Quote className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={() => onApplyMarkdown('[文本](', ')')}
        className="rounded px-1.5 py-1 text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
        title="链接"
      >
        <Link2 className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}