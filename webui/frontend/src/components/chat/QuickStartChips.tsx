import { useCallback } from 'react'
import { useChatSessionId } from '../../contexts/ChatSessionContext'
import { usePersonaStore } from '../../stores/personas'
import { useToastStore } from '../../stores/toast'
import { api } from '../../api/client'

const SUGGESTIONS = [
  { label: '分析当前持仓风险', prompt: '分析当前持仓的风险，给出建议' },
  { label: '回测一个策略', prompt: '帮我回测一个简单的动量策略' },
  { label: '总结最近的研究', prompt: '总结最近的研究进展' },
  { label: '审查我的策略代码', prompt: '审查我最新的策略代码，指出问题' },
]

export function QuickStartChips() {
  const currentSessionId = useChatSessionId()
  const getSessionPersona = usePersonaStore((s) => s.getSessionPersona)
  const addToast = useToastStore((s) => s.addToast)

  const send = useCallback(
    async (prompt: string) => {
      if (!currentSessionId) return
      const persona = getSessionPersona(currentSessionId)
      try {
        await api.post('/chat/send_async', {
          session_id: currentSessionId,
          content: prompt,
          agent_id: persona && persona !== 'chat' ? persona : undefined,
        })
      } catch (err: any) {
        addToast('error', `发送失败：${err?.message ?? '未知错误'}`)
      }
    },
    [currentSessionId, getSessionPersona, addToast],
  )

  return (
    <div className="mt-6 flex max-w-xl flex-wrap justify-center gap-2">
      {SUGGESTIONS.map((s) => (
        <button
          key={s.label}
          type="button"
          onClick={() => void send(s.prompt)}
          className="rounded-full border border-strong/70 bg-surface px-3 py-1.5 text-xs text-secondary transition-colors hover:border-primary-500/60 hover:bg-surface-raised hover:text-foreground"
        >
          {s.label}
        </button>
      ))}
    </div>
  )
}