// useContextUsage: 双源交叉校验契约（问题 1 · 2026-11-25）
// context.breakdown.total 与 context_used 两源都存在时按相对差异校验：
// ≤15% 一致 → 用实测；>15% 分歧 → 用 breakdown（与 hover 明细同源）。
// 单源 → 用该源；无源 → 0%。

import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { enableMapSet } from 'immer'

import { useContextUsage } from '../hooks/useContextUsage'
import { useChatStore } from '../stores/chat'
import { useSystemStore } from '../stores/system'

enableMapSet()

const MODEL_INFO = {
  provider: 'minimax',
  model: 'MiniMax-M3.1-Flash',
  models_dev_id: 'minimax-cn-coding-plan',
  context_tokens: 1_000_000,
  max_output_tokens: 128_000,
  supports_vision: false,
  supports_audio: false,
  supports_pdf: false,
  supports_tools: true,
  supports_reasoning: true,
  supports_structured_output: false,
  cost_input: null,
  cost_output: null,
  cost_cache_read: null,
  cost_cache_write: null,
  description: '',
  release_date: null,
  source: 'fetched',
  fetched_at: null,
}

describe('useContextUsage', () => {
  beforeEach(() => {
    useChatStore.setState({
      tokensUsed: new Map(),
      contextBreakdown: new Map(),
      messages: new Map(),
    })
    useSystemStore.setState({ modelInfo: MODEL_INFO } as never)
  })

  it('uses the sum of breakdown segments as the primary usage', () => {
    useChatStore.setState({
      contextBreakdown: new Map([['s1', {
        messages: 12,
        system_prompt: 2374,
        skills: 4,
      }]]),
    })
    const { result } = renderHook(() => useContextUsage('s1'))
    // 12 + 2374 + 4 = 2390，与 hover 明细同源
    expect(result.current.usedTokens).toBe(2390)
    expect(result.current.source).toBe('estimated') // 后端估算，非 API 实测
    expect(result.current.reconciliation.kind).toBe('breakdown-only')
    expect(result.current.percent).toBeCloseTo(0.239, 2)
  })

  it('uses real API usage when both sources agree (≤15% gap)', () => {
    useChatStore.setState({
      contextBreakdown: new Map([['s1', { system_prompt: 1000 }]]),
      tokensUsed: new Map([['s1', 1080]]),
    })
    const { result } = renderHook(() => useContextUsage('s1'))
    expect(result.current.usedTokens).toBe(1080)
    expect(result.current.reconciliation.kind).toBe('consistent')
    expect(result.current.reconciliation.usedMeasured).toBe(true)
  })

  it('falls back to breakdown when sources diverge (>15% gap)', () => {
    useChatStore.setState({
      contextBreakdown: new Map([['s1', { system_prompt: 100 }]]),
      tokensUsed: new Map([['s1', 3200]]),
    })
    const { result } = renderHook(() => useContextUsage('s1'))
    // 3200 vs 100 差异 97% → divergent → 用 breakdown（与 hover 明细同源）
    expect(result.current.usedTokens).toBe(100)
    expect(result.current.reconciliation.kind).toBe('divergent')
    expect(result.current.reconciliation.relativeGap).toBeCloseTo(0.969, 2)
  })

  it('uses measured alone when no breakdown available', () => {
    useChatStore.setState({
      tokensUsed: new Map([['s1', 3200]]),
    })
    const { result } = renderHook(() => useContextUsage('s1'))
    expect(result.current.usedTokens).toBe(3200)
    expect(result.current.reconciliation.kind).toBe('measured-only')
  })

  it('uses breakdown alone when no API usage available', () => {
    useChatStore.setState({
      contextBreakdown: new Map([['s1', { system_prompt: 500 }]]),
    })
    const { result } = renderHook(() => useContextUsage('s1'))
    expect(result.current.usedTokens).toBe(500)
    expect(result.current.reconciliation.kind).toBe('breakdown-only')
  })

  it('reports 0 usage (0%) when neither source is available', () => {
    const { result } = renderHook(() => useContextUsage('s1'))
    expect(result.current.usedTokens).toBe(0)
    expect(result.current.percent).toBe(0)
  })

  it('does not fall back to message-text estimation', () => {
    // 即使 messages 里有大量文本，没有 breakdown/usage 时仍为 0
    useChatStore.setState({
      messages: new Map([['m1', {
        id: 'm1',
        session_id: 's1',
        role: 'user',
        created_at: 0,
        parts: [{ type: 'text', text: '一段很长的消息'.repeat(1000) }],
      } as never]]),
    })
    const { result } = renderHook(() => useContextUsage('s1'))
    expect(result.current.usedTokens).toBe(0)
  })

  it('scopes breakdown to the given session', () => {
    useChatStore.setState({
      contextBreakdown: new Map([['s1', { system_prompt: 500 }]]),
    })
    const { result } = renderHook(() => useContextUsage('other'))
    expect(result.current.usedTokens).toBe(0)
  })
})
