import { useEffect } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useChatSessionId } from '../../contexts/ChatSessionContext'
import { useChatStore } from '../../stores/chat'
import { useContextUsage } from '../../hooks/useContextUsage'
import { api } from '../../api/client'
import { tokenTone, toneFillClass } from '../../utils/tokenThresholds'
import { formatTokens } from '../../utils/contextUsage'

// Module-level set tracking which sessions have already been auto-
// compacted in the current page lifetime. Survives component remounts
// (e.g. when chat tab switches) but resets on full page reload —
// which is fine because a fresh session-load will start with a low
// context ratio again.
const autoCompactedSessions = new Set<string>()

export function ContextUsageBar() {
  const sessionId = useChatSessionId()
  const streamingMessageId = useChatStore((s) => s.streamingMessageId)
  // R-014 F4-b：统一口径 —— 实测优先、无实测用字符估算；阈值统一到
  // `utils/tokenThresholds.ts`（此前本文件另有一份 tierFor，两处事实源）。
  const usage = useContextUsage(sessionId)

  const limit = usage.limitTokens
  const pct = usage.percent
  const tone = tokenTone(pct)
  const barClass = toneFillClass(tone)
  const textClass =
    tone === 'danger' ? 'text-danger-fg' : tone === 'warn' ? 'text-warning-fg' : 'text-success-fg'

  // Auto-trigger /compact once the session crosses the red threshold
  // (>= 80% of model context). Throttled per-session: each session
  // gets exactly one auto-compact per page lifetime so we don't spam
  // the queue when the bar oscillates around the threshold. The
  // user's manual /compact invocation goes through the same backend
  // intercept (Tier A P31), so we just POST a slash command and let
  // the existing event-sourcing path handle the rest.
  useEffect(() => {
    if (tone !== 'danger') return
    if (!sessionId) return
    if (streamingMessageId) return // don't fire mid-stream
    if (autoCompactedSessions.has(sessionId)) return

    autoCompactedSessions.add(sessionId)
    // Fire-and-forget. Failure is benign — the user can always type
    // /compact manually.
    api
      .post('/chat/send_async', {
        session_id: sessionId,
        content: '/compact',
      })
      .catch((err) => {
        // Roll back the throttle so a transient failure doesn't
        // permanently lock the session out of auto-compact until
        // page reload.
        autoCompactedSessions.delete(sessionId)
        console.warn('[auto-compact] failed:', err)
      })
  }, [tone, sessionId, streamingMessageId])

  if (limit <= 0) return null

  return (
    <div className="flex items-center gap-2 border-b border-slate-700/50 bg-slate-800/40 px-4 py-1.5 text-[11px]">
      <div className="flex-1">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700/60">
          <div
            className={`h-full transition-all duration-300 ${barClass}`}
            style={{ width: `${pct}%` }}
            data-testid="context-progress-bar"
          />
        </div>
      </div>
      <div className={`flex items-center gap-1 font-mono ${textClass}`}>
        <span data-testid="context-usage-text">
          {usage.source === 'estimated' ? '~' : ''}
          {formatTokens(usage.usedTokens)} / {formatTokens(limit)} ({pct.toFixed(1)}%)
        </span>
        {usage.isStale && (
          <span
            className="ml-1 inline-flex items-center text-warning-fg"
            title="模型信息来自内置/fallback 数据，非最新"
            data-testid="context-stale-hint"
          >
            <AlertTriangle className="h-3 w-3" />
          </span>
        )}
      </div>
    </div>
  )
}
