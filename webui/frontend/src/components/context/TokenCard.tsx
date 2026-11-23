import { useMemo } from 'react'
import { BarChart3 } from 'lucide-react'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { useSystemStore } from '../../stores/system'
import { tokenTone, toneFillClass } from '../../utils/tokenThresholds'

/**
 * Right-panel card showing the active session's token usage against the
 * model context limit, plus the loaded message count. Extracted from the
 * old ContextPanel metrics section when the panels were merged.
 */
export function TokenCard() {
  const sessionId = useSessionStore((s) => s.currentSessionId)
  const tokensUsed = useChatStore((s) =>
    sessionId ? s.tokensUsed.get(sessionId) ?? 0 : 0
  )
  const messages = useChatStore((s) => s.messages)
  const modelInfo = useSystemStore((s) => s.modelInfo)

  const limit = modelInfo?.context_tokens ?? 0
  const pct = limit > 0 ? Math.min(100, (tokensUsed / limit) * 100) : 0

  const messageCount = useMemo(() => {
    let count = 0
    for (const m of messages.values()) {
      if (!sessionId || m.session_id === sessionId) count++
    }
    return count
  }, [messages, sessionId])

  return (
    <div className="rounded-lg border border-slate-800/50 bg-slate-900/30 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <BarChart3 className="h-3 w-3" />
        <span>Token 使用情况</span>
      </div>
      <div className="space-y-2">
        {limit > 0 && (
          <div>
            <div className="mb-1 flex justify-between text-[10px] text-slate-400">
              <span>上下文</span>
              <span className="font-mono">{pct.toFixed(1)}%</span>
            </div>
            <div className="h-1 w-full overflow-hidden rounded-full bg-slate-800">
              <div
                className={`h-full transition-all ${toneFillClass(tokenTone(pct))}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}
        <div className="flex justify-between text-[10px] text-slate-400">
          <span>消息</span>
          <span className="font-mono">{messageCount}</span>
        </div>
      </div>
    </div>
  )
}
