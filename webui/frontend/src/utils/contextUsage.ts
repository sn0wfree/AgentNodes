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

/** 两源交叉校验的结论：两源都存在时按相对差异判定是否一致。 */
export type UsageReconciliation =
  | 'no-data'        // 两源都没有
  | 'measured-only'  // 只有 LLM API 实测
  | 'breakdown-only' // 只有后端 7 段估算
  | 'consistent'     // 两源一致（相对差异 ≤ 阈值）→ 采用实测
  | 'divergent'      // 两源不一致 → 采用 breakdown（与明细同源）

export interface UsageReconciliationInfo {
  /** 判定结论 */
  kind: UsageReconciliation
  /** 相对差异 |measured - breakdown| / max(两者)，无双源时为 null */
  relativeGap: number | null
  /** 是否采用 LLM API 实测值作为 usedTokens */
  usedMeasured: boolean
}

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
  /** 双源交叉校验结果（调试/诊断用，不影响既有 measured/estimated 二分） */
  reconciliation: UsageReconciliationInfo
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

/** 双源一致性阈值：相对差异 ≤ 15% 视为一致（估算 chars/3.0 与实测
 *  prompt_tokens 的正常偏差范围）。 */
export const RECONCILE_GAP_THRESHOLD = 0.15

/**
 * 交叉校验 LLM 实测（context_used）与后端 7 段估算（breakdown.total）。
 *
 * - 两源一致 → 采用实测（更准）
 * - 两源分歧 → 采用 breakdown（与 hover 明细同源，避免展示矛盾）
 * - 单源 → 用该源；无源 → 0
 */
export function reconcileUsage(
  measuredTokens: number,
  breakdownTotal: number,
  threshold = RECONCILE_GAP_THRESHOLD,
): UsageReconciliationInfo {
  const m = Number.isFinite(measuredTokens) && measuredTokens > 0 ? measuredTokens : 0
  const b = Number.isFinite(breakdownTotal) && breakdownTotal > 0 ? breakdownTotal : 0
  if (m > 0 && b > 0) {
    const relativeGap = Math.abs(m - b) / Math.max(m, b)
    const consistent = relativeGap <= threshold
    return {
      kind: consistent ? 'consistent' : 'divergent',
      relativeGap,
      usedMeasured: consistent,
    }
  }
  if (m > 0) return { kind: 'measured-only', relativeGap: null, usedMeasured: true }
  if (b > 0) return { kind: 'breakdown-only', relativeGap: null, usedMeasured: false }
  return { kind: 'no-data', relativeGap: null, usedMeasured: false }
}

/** 组装最终口径：双源交叉校验后决定 usedTokens。 */
export function buildContextUsage(params: {
  measuredTokens: number
  estimatedTokens: number
  limitTokens: number
  outputLimitTokens?: number
  isStale?: boolean
  /** context.breakdown 各段之和；传入时启用双源校验 */
  breakdownTotal?: number
}): ContextUsageInfo {
  const { measuredTokens, estimatedTokens, limitTokens } = params
  const reconciliation = params.breakdownTotal !== undefined
    ? reconcileUsage(measuredTokens, params.breakdownTotal)
    : (() => {
        const useMeasured = measuredTokens > 0
        return {
          kind: (useMeasured ? 'measured-only' : 'no-data') as UsageReconciliation,
          relativeGap: null,
          usedMeasured: useMeasured,
        }
      })()

  let usedTokens: number
  if (reconciliation.kind === 'divergent') {
    // 分歧 → 用 breakdown（与明细同源）
    usedTokens = params.breakdownTotal ?? estimatedTokens
  } else if (reconciliation.usedMeasured) {
    // 一致 / 仅实测 → 用 API 实测
    usedTokens = measuredTokens
  } else if (params.breakdownTotal !== undefined && params.breakdownTotal > 0) {
    // 仅 breakdown → 用后端估算
    usedTokens = params.breakdownTotal
  } else {
    // 无双源 → 旧行为（estimatedTokens，通常为 0）
    usedTokens = estimatedTokens
  }
  const source: UsageSource = reconciliation.usedMeasured ? 'measured' : 'estimated'
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
    reconciliation,
  }
}
