/**
 * 上下文用量 —— 统一计算口径（R-014 F4-b · composer-context-usage-refine）。
 *
 * **为什么统一**：此前三处 UI 各算各的（`ContextUsageBar` 有字符估算兜底、
 * `ComposerStatusBar` 与 `TokenCard` 没有 → 恒显 `0%`），阈值有两份事实源
 * （`tokenThresholds.ts` vs `ContextUsageBar` 本地 `tierFor`），sessionId
 * 来源也各不相同。本模块把「算」与「取」分开：
 * - 本模块（纯函数）：估算、格式化、口径组装 —— 可直接单测
 * - `hooks/useContextUsage.ts`：取数（store + props sessionId）
 *
 * **数据现实**（2026-11-24 调研）：生产 chat 路径下后端**尚未**把 token
 * 用量事件推到前端（`llm_usage` / `session_total_tokens` 在
 * `apps/chat/events.py` 翻译层被丢弃），因此 `measured` 分支目前几乎不会
 * 命中，界面上以**估算值 + `~` 标记**呈现。后端透传后无需改本模块，
 * `source` 会自动从 `estimated` 切到 `measured`。
 */

/** 字符 → token 的估算常数（仅在无实测数据时使用）。
 *  与后端 `foundation/utils/token_utils.py` 的 3.0 略有差异：前端统计的是
 *  parts 文本 + tool_call 参数 + tool 结果，后端统计 messages content，
 *  样本不同。**保持 3.5 不动**——改小会让估算值变大、提前触发 80% 自动压缩。 */
export const CHARS_PER_TOKEN = 3.5

export type UsageSource = 'measured' | 'estimated'

export interface ContextUsageInfo {
  /** 当前上下文已用 token（实测或估算） */
  usedTokens: number
  /** 模型上下文上限（0 = 未知） */
  limitTokens: number
  /** 单次输出上限（0 = 未知） */
  outputLimitTokens: number
  /** 占用百分比 0-100 */
  percent: number
  /** 剩余可用 token */
  remainingTokens: number
  /** 数据来源：measured = 后端实测；estimated = 字符估算 */
  source: UsageSource
  /** 模型信息是否陈旧（bundled/fallback，非最新抓取） */
  isStale: boolean
  /** 是否有有效的上下文上限 */
  hasLimit: boolean
}

/** token 数的紧凑展示：1234 → 1.2K，1_234_567 → 1.2M。 */
export function formatTokens(n: number): string {
  if (!Number.isFinite(n)) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(Math.round(n))
}

/** 参与估算的消息最小结构（只要求 role 与 parts）。 */
export interface EstimableMessage {
  role: string
  parts: {
    type: string
    text?: string
    name?: string
    arguments?: unknown
    result?: unknown
  }[]
}

/**
 * 估算上下文占用：从已加载消息的字符数推 token。
 * 跳过 `role === 'tool'` 的消息——其内容已折进 assistant 的 tool_call 结果里，
 * 重复计会高估。
 */
export function estimateTokensFromMessages(
  messages: Map<string, EstimableMessage> | Iterable<EstimableMessage>,
): number {
  const list: Iterable<EstimableMessage> =
    messages instanceof Map ? messages.values() : messages
  let chars = 0
  for (const m of list) {
    if (m.role === 'tool') continue
    for (const p of m.parts) {
      if (p.type === 'text' || p.type === 'thinking') chars += (p.text ?? '').length
      else if (p.type === 'tool_call') {
        chars += (p.name?.length ?? 0) + (typeof p.arguments === 'string' ? p.arguments.length : 0)
        if (typeof p.result === 'string') chars += p.result.length
      }
    }
  }
  return Math.round(chars / CHARS_PER_TOKEN)
}

/** 组装最终口径：实测优先，无实测则用估算。 */
export function buildContextUsage(params: {
  measuredTokens: number
  estimatedTokens: number
  limitTokens: number
  outputLimitTokens?: number
  isStale?: boolean
}): ContextUsageInfo {
  const { measuredTokens, estimatedTokens, limitTokens } = params
  const useMeasured = measuredTokens > 0
  const usedTokens = useMeasured ? measuredTokens : estimatedTokens
  const source: UsageSource = useMeasured ? 'measured' : 'estimated'
  const hasLimit = limitTokens > 0
  const percent = hasLimit ? Math.min(100, (usedTokens / limitTokens) * 100) : 0
  return {
    usedTokens,
    limitTokens,
    outputLimitTokens: params.outputLimitTokens ?? 0,
    percent,
    remainingTokens: hasLimit ? Math.max(0, limitTokens - usedTokens) : 0,
    source,
    isStale: params.isStale ?? false,
    hasLimit,
  }
}
