import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Bot } from 'lucide-react'
import { usePersonaStore } from '../../stores/personas'
import type { ChatPersona } from '../../api/client'

interface ComposerToolbarProps {
  sessionId: string | null
}

/**
 * Composer 工具行 —— 只含**角色（persona）选择器**。
 *
 * R-014 F3（composer-toolbar-p0p1-fix-20261124）：原实现的 8 个常驻
 * Markdown 格式化按钮（粗体/斜体/行内代码/代码块/列表/引用/链接）已移除——
 * 它们在窄屏下独占两行、把整行撑到 4 行，且与浏览器/编辑器的标准快捷键
 * 重复。能力改由键盘提供（`Cmd/Ctrl+B/I/E`、`Cmd/Ctrl+Shift+7/8/.`），
 * 变换逻辑在 `utils/markdownShortcuts.ts`。
 */
export function ComposerToolbar({ sessionId }: ComposerToolbarProps) {
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

  const isOrchestrator = sessionId?.startsWith('dag:') ?? false

  return (
    <div className="mb-2 flex flex-wrap items-center gap-1">
      <div className="relative" ref={menuRef}>
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
      </div>
    </div>
  )
}
