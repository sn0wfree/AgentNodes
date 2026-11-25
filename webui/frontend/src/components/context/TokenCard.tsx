import { useMemo } from 'react'
import { BarChart3, AlertTriangle } from 'lucide-react'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { useContextUsage } from '../../hooks/useContextUsage'
import { tokenTone, toneFillClass } from '../../utils/tokenThresholds'
import { formatTokens } from '../../utils/contextUsage'

/**
 * Right-panel card showing the active session's token usage against the
 * model context limit, plus the loaded message count. Extracted from the
 * old ContextPanel metrics section when the panels were merged.
 *
 * R-014 F4-b：改用统一的 `useContextUsage` —— 此前本卡无估算兜底，
 * `tokensUsed` 恒为 0 → 永远显示 `0.0%`；且 sessionId 取自全局
 * `currentSessionId`，与 Composer 的会话可能不是同一个。
 */
export function TokenCard() {
  const sessionId = useSessionStore((s) => s.currentSessionId)
  const messages = useChatStore((s) => s.messages)
  const usage = useContextUsage(sessionId)

  const messageCount = useMemo(() => {
    let count = 0
    for (const m of messages.values()) {
      if (!sessionId || m.session_id === sessionId) count++
    }
    return count
  }, [messages, sessionId])

  const detail = [
    `上下文已用：${formatTokens(usage.usedTokens)} / ${formatTokens(usage.limitTokens)} (${usage.percent.toFixed(1)}%)`,
    `剩余可用：${formatTokens(usage.remainingTokens)}`,
    usage.outputLimitTokens > 0 ? `单次输出上限：${formatTokens(usage.outputLimitTokens)}` : null,
    usage.source === 'measured' ? '数据来源：后端实测' : '数据来源：后端估算（非计费实测值）',
  ]
    .filter(Boolean)
    .join('\n')

  return (
    <div className="rounded-lg border border-slate-800/50 bg-slate-900/30 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <BarChart3 className="h-3 w-3" />
        <span>Token 使用情况</span>
        {usage.isStale && (
          <span
            className="ml-auto text-warning-fg"
            title="模型信息来自内置/fallback 数据，非最新抓取"
            data-testid="token-card-stale"
          >
            <AlertTriangle className="h-3 w-3" />
          </span>
        )}
      </div>
      <div className="space-y-2">
        {usage.hasLimit && (
          <div title={detail} data-testid="token-card-context">
            <div className="mb-1 flex justify-between text-[10px] text-slate-400">
              <span>上下文</span>
              <span className="font-mono">
                {usage.source === 'estimated' ? '~' : ''}
                {formatTokens(usage.usedTokens)} / {formatTokens(usage.limitTokens)}
                <span className="ml-1">({usage.percent.toFixed(1)}%)</span>
              </span>
            </div>
            <div className="h-1 w-full overflow-hidden rounded-full bg-slate-800">
              <div
                className={`h-full transition-all ${toneFillClass(tokenTone(usage.percent))}`}
                style={{ width: `${usage.percent}%` }}
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
