import { describe, it, expect } from 'vitest'
import { TOKEN_THRESHOLDS, tokenTone, toneFillClass } from '../utils/tokenThresholds'

describe('tokenThresholds (P9 单点)', () => {
  it('keeps the agreed 50/80 thresholds frozen', () => {
    expect(TOKEN_THRESHOLDS).toEqual({ warn: 50, danger: 80 })
  })

  it('maps percent to tone at the boundaries', () => {
    expect(tokenTone(0)).toBe('normal')
    expect(tokenTone(49.9)).toBe('normal')
    expect(tokenTone(50)).toBe('warn')
    expect(tokenTone(79.9)).toBe('warn')
    expect(tokenTone(80)).toBe('danger')
    expect(tokenTone(100)).toBe('danger')
  })

  it('maps tones to fill classes', () => {
    // R-014 F4-b：改用语义令牌类（success/danger），不再硬编码色阶。
    expect(toneFillClass('normal')).toBe('bg-success')
    expect(toneFillClass('warn')).toBe('bg-warning')
    expect(toneFillClass('danger')).toBe('bg-danger')
  })
})
