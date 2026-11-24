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

/** 语义色带 → Tailwind 进度条填充类。
 *  R-014 F4-b：改用语义令牌类（与 `semantic-color-unification` 的
 *  success/danger 令牌一致），此前是硬编码 `bg-red-500`/`bg-emerald-500`
 *  ——亮色主题下这两个实心亮色在浅底上对比度不足。 */
export function toneFillClass(tone: TokenTone): string {
  return tone === 'danger'
    ? 'bg-danger'
    : tone === 'warn'
      ? 'bg-warning'
      : 'bg-success'
}
