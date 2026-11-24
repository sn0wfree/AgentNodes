// R-014 F4-b —— 上下文用量统一口径（估算 / 格式化 / 组装）。
import { describe, it, expect } from 'vitest'
import {
  CHARS_PER_TOKEN,
  formatTokens,
  estimateTokensFromMessages,
  buildContextUsage,
} from '../utils/contextUsage'

describe('formatTokens', () => {
  it('formats thousands and millions compactly', () => {
    expect(formatTokens(999)).toBe('999')
    expect(formatTokens(1234)).toBe('1.2K')
    expect(formatTokens(1_234_567)).toBe('1.2M')
  })

  it('rounds sub-thousand values', () => {
    expect(formatTokens(12.4)).toBe('12')
  })

  it('handles non-finite input', () => {
    expect(formatTokens(NaN)).toBe('—')
  })
})

describe('estimateTokensFromMessages', () => {
  const msg = (role: string, parts: Record<string, unknown>[]) =>
    ({ role, parts }) as never

  it('estimates from text parts', () => {
    const tokens = estimateTokensFromMessages([
      msg('user', [{ type: 'text', text: 'a'.repeat(350) }]),
    ])
    expect(tokens).toBe(100)
  })

  it('counts thinking parts', () => {
    const tokens = estimateTokensFromMessages([
      msg('assistant', [{ type: 'thinking', text: 'b'.repeat(350) }]),
    ])
    expect(tokens).toBe(100)
  })

  it('counts tool_call name, arguments and result', () => {
    const tokens = estimateTokensFromMessages([
      msg('assistant', [
        { type: 'tool_call', name: 'abcd', arguments: 'x'.repeat(100), result: 'y'.repeat(200) },
      ]),
    ])
    expect(tokens).toBe(Math.round(304 / CHARS_PER_TOKEN))
  })

  it('skips tool-role messages (already folded into tool_call results)', () => {
    const withTool = estimateTokensFromMessages([
      msg('assistant', [{ type: 'tool_call', name: 'x', arguments: '', result: 'z'.repeat(100) }]),
      msg('tool', [{ type: 'text', text: 'z'.repeat(100) }]),
    ])
    const withoutTool = estimateTokensFromMessages([
      msg('assistant', [{ type: 'tool_call', name: 'x', arguments: '', result: 'z'.repeat(100) }]),
    ])
    expect(withTool).toBe(withoutTool)
  })

  it('accepts a Map of messages', () => {
    const map = new Map([
      ['m1', msg('user', [{ type: 'text', text: 'a'.repeat(350) }])],
    ])
    expect(estimateTokensFromMessages(map)).toBe(100)
  })
})

describe('buildContextUsage', () => {
  it('prefers measured tokens over the estimate', () => {
    const u = buildContextUsage({
      measuredTokens: 500,
      estimatedTokens: 100,
      limitTokens: 1000,
    })
    expect(u.usedTokens).toBe(500)
    expect(u.source).toBe('measured')
    expect(u.percent).toBe(50)
    expect(u.remainingTokens).toBe(500)
  })

  it('falls back to the estimate when nothing was measured', () => {
    const u = buildContextUsage({
      measuredTokens: 0,
      estimatedTokens: 250,
      limitTokens: 1000,
    })
    expect(u.usedTokens).toBe(250)
    expect(u.source).toBe('estimated')
    expect(u.percent).toBe(25)
  })

  it('caps the percentage at 100', () => {
    const u = buildContextUsage({
      measuredTokens: 5000,
      estimatedTokens: 0,
      limitTokens: 1000,
    })
    expect(u.percent).toBe(100)
    expect(u.remainingTokens).toBe(0)
  })

  it('reports no limit when context_tokens is unknown', () => {
    const u = buildContextUsage({
      measuredTokens: 100,
      estimatedTokens: 0,
      limitTokens: 0,
    })
    expect(u.hasLimit).toBe(false)
    expect(u.percent).toBe(0)
    expect(u.remainingTokens).toBe(0)
  })

  it('carries the output limit and the stale flag through', () => {
    const u = buildContextUsage({
      measuredTokens: 10,
      estimatedTokens: 0,
      limitTokens: 100,
      outputLimitTokens: 32,
      isStale: true,
    })
    expect(u.outputLimitTokens).toBe(32)
    expect(u.isStale).toBe(true)
  })
})
