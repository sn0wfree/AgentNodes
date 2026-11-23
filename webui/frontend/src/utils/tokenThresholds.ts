/**
 * Token 阈值单点（P9 grilling Q7/Q11）：现状 50% 警告 / 80% 危险，
 * 阈值爬升不直接接设置页 —— 抽常量留 seam，将来配置只需改这一处。
 */
export const TOKEN_THRESHOLDS = {
  warn: 50,
  danger: 80,
} as const

export type TokenTone = 'normal' | 'warn' | 'danger'

/** 百分比 → 语义色带（两处 meter 共用，保证口径一致）。 */
export function tokenTone(pct: number): TokenTone {
  if (pct >= TOKEN_THRESHOLDS.danger) return 'danger'
  if (pct >= TOKEN_THRESHOLDS.warn) return 'warn'
  return 'normal'
}

/** 语义色带 → Tailwind 进度条填充类。 */
export function toneFillClass(tone: TokenTone): string {
  return tone === 'danger'
    ? 'bg-red-500'
    : tone === 'warn'
      ? 'bg-amber-500'
      : 'bg-emerald-500'
}
