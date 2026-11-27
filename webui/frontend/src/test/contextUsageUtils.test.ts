// utils/contextUsage — pure-function contracts for the unified usage
// pipeline (R-014 F4-b): estimation, dual-source reconciliation and the
// final usage assembly. Zero coverage before this suite.

import { describe, it, expect } from 'vitest'
import {
  CHARS_PER_TOKEN,
  RECONCILE_GAP_THRESHOLD,
  formatTokens,
  estimateTokensFromMessages,
  reconcileUsage,
  buildContextUsage,
  type EstimableMessage,
} from '../utils/contextUsage'

describe('formatTokens', () => {
  it('formats thousands as K with one decimal', () => {
    expect(formatTokens(1234)).toBe('1.2K')
  })

  it('formats millions as M', () => {
    expect(formatTokens(1_234_567)).toBe('1.2M')
  })

  it('rounds small values to integers', () => {
    expect(formatTokens(999)).toBe('999')
    expect(formatTokens(0)).toBe('0')
  })

  it('renders a dash for non-finite input', () => {
    expect(formatTokens(NaN)).toBe('—')
    expect(formatTokens(Infinity)).toBe('—')
  })
})

describe('estimateTokensFromMessages', () => {
  const msg = (over: Partial<EstimableMessage>): EstimableMessage => ({
    role: 'assistant',
    parts: [],
    ...over,
  })

  it('divides accumulated chars by CHARS_PER_TOKEN', () => {
    const msgs = [msg({ parts: [{ type: 'text', text: 'a'.repeat(35) }] })]
    expect(estimateTokensFromMessages(msgs)).toBe(10)
    expect(35 / CHARS_PER_TOKEN).toBe(10)
  })

  it('skips role=tool messages (their content lives in tool_call results)', () => {
    const msgs = [
      msg({ role: 'tool', parts: [{ type: 'text', text: 'x'.repeat(3500) }] }),
      msg({ parts: [{ type: 'text', text: 'b'.repeat(35) }] }),
    ]
    expect(estimateTokensFromMessages(msgs)).toBe(10)
  })

  it('counts tool_call name + string arguments + string result', () => {
    const msgs = [
      msg({
        parts: [{
          type: 'tool_call',
          name: 'abcd', // 4 chars
          arguments: '{"k":1}', // 7 chars
          result: '1234567890', // 10 chars
        }],
      }),
    ]
    // 21 chars / 3.5 = 6
    expect(estimateTokensFromMessages(msgs)).toBe(6)
  })

  it('counts thinking parts as text', () => {
    const msgs = [msg({ parts: [{ type: 'thinking', text: 'c'.repeat(70) }] })]
    expect(estimateTokensFromMessages(msgs)).toBe(20)
  })

  it('accepts both a Map and a plain iterable', () => {
    const m1 = msg({ parts: [{ type: 'text', text: 'a'.repeat(35) }] })
    const asMap = new Map([['m1', m1]])
    expect(estimateTokensFromMessages(asMap)).toBe(estimateTokensFromMessages([m1]))
  })
})

describe('reconcileUsage（双源交叉校验）', () => {
  it('two consistent sources adopt the measured value', () => {
    const r = reconcileUsage(1000, 1050)
    expect(r.kind).toBe('consistent')
    expect(r.usedMeasured).toBe(true)
    expect(r.relativeGap).toBeCloseTo(50 / 1050, 5)
  })

  it('gap exactly at the threshold (15%) is still consistent', () => {
    // measured 1000 vs breakdown 1150 → gap 150/1150 ≈ 13.04% ≤ 15%
    const r = reconcileUsage(1000, 1150)
    expect(r.kind).toBe('consistent')
    expect(r.usedMeasured).toBe(true)
  })

  it('divergent sources fall back to breakdown', () => {
    const r = reconcileUsage(1000, 3000)
    expect(r.kind).toBe('divergent')
    expect(r.usedMeasured).toBe(false)
    expect(r.relativeGap).toBeGreaterThan(RECONCILE_GAP_THRESHOLD)
  })

  it('measured-only uses the API value', () => {
    expect(reconcileUsage(800, 0)).toEqual({
      kind: 'measured-only', relativeGap: null, usedMeasured: true,
    })
  })

  it('breakdown-only uses the backend estimate', () => {
    expect(reconcileUsage(0, 700)).toEqual({
      kind: 'breakdown-only', relativeGap: null, usedMeasured: false,
    })
  })

  it('no data at all', () => {
    expect(reconcileUsage(0, 0)).toEqual({
      kind: 'no-data', relativeGap: null, usedMeasured: false,
    })
  })

  it('treats non-finite measured input as absent', () => {
    expect(reconcileUsage(NaN, 700).kind).toBe('breakdown-only')
  })
})

describe('buildContextUsage（口径组装）', () => {
  it('divergent sources use breakdownTotal (same source as the hover detail)', () => {
    const u = buildContextUsage({
      measuredTokens: 1000, estimatedTokens: 0, limitTokens: 10_000,
      breakdownTotal: 3000,
    })
    expect(u.usedTokens).toBe(3000)
    expect(u.source).toBe('estimated')
    expect(u.reconciliation.kind).toBe('divergent')
    expect(u.percent).toBe(30)
    expect(u.remainingTokens).toBe(7000)
  })

  it('consistent sources use the measured value', () => {
    const u = buildContextUsage({
      measuredTokens: 1000, estimatedTokens: 0, limitTokens: 10_000,
      breakdownTotal: 1050,
    })
    expect(u.usedTokens).toBe(1000)
    expect(u.source).toBe('measured')
    expect(u.reconciliation.kind).toBe('consistent')
  })

  it('breakdown-only falls back to the backend estimate', () => {
    const u = buildContextUsage({
      measuredTokens: 0, estimatedTokens: 0, limitTokens: 10_000,
      breakdownTotal: 500,
    })
    expect(u.usedTokens).toBe(500)
    expect(u.reconciliation.kind).toBe('breakdown-only')
  })

  it('no dual source keeps the legacy estimatedTokens behavior', () => {
    const u = buildContextUsage({
      measuredTokens: 0, estimatedTokens: 120, limitTokens: 10_000,
    })
    expect(u.usedTokens).toBe(120)
    expect(u.reconciliation.kind).toBe('no-data')
  })

  it('percent is clamped to 100 when over the limit', () => {
    const u = buildContextUsage({
      measuredTokens: 0, estimatedTokens: 0, limitTokens: 100,
      breakdownTotal: 5000,
    })
    expect(u.percent).toBe(100)
    expect(u.remainingTokens).toBe(0)
  })

  it('no limit → percent 0 and hasLimit false', () => {
    const u = buildContextUsage({
      measuredTokens: 0, estimatedTokens: 500, limitTokens: 0,
    })
    expect(u.hasLimit).toBe(false)
    expect(u.percent).toBe(0)
    expect(u.remainingTokens).toBe(0)
  })

  it('isStale/outputLimitTokens pass through', () => {
    const u = buildContextUsage({
      measuredTokens: 0, estimatedTokens: 0, limitTokens: 1000,
      outputLimitTokens: 128_000, isStale: true,
    })
    expect(u.isStale).toBe(true)
    expect(u.outputLimitTokens).toBe(128_000)
  })
})
