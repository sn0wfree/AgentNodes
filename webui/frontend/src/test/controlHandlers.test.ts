// hooks/sse/controlHandlers — sessionTotalTokens, llmUsage, compact,
// agentDone, errorEvent. These manage token accounting and streaming
// lifecycle.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  sessionTotalTokens,
  llmUsage,
  contextBreakdown,
  compact,
  agentDone,
  errorEvent,
} from '../hooks/sse/controlHandlers'
import { useChatStore } from '../stores/chat'
import type { Agent } from '../stores/agents'
import type { SSEContext } from '../hooks/sse/types'

function ctx(overrides: Partial<SSEContext> = {}): SSEContext {
  const store = useChatStore.getState()
  return {
    sessionId: 'sess-1',
    state: {
      hasSeenTotalTokens: (sid) =>
        useChatStore.getState().totalTokensSeen.get(sid) === true,
      getMessages: () => Array.from(store.messages.values()),
      getMessage: (id) => store.messages.get(id),
      isQueuePaused: () => false,
      getTokensUsed: () => 0,
    },
    addMessage: store.addMessage,
    updateMessage: store.updateMessage,
    setStreamingMessage: store.setStreamingMessage,
    setStreamingText: store.setStreamingText,
    appendStreamingText: store.appendStreamingText,
    setActiveAttempt: store.setActiveAttempt,
    setAskedUser: store.setAskedUser,
    setQueuePaused: () => {},
    setQueueLength: () => {},
    setTokensUsed: store.setTokensUsed,
    setContextBreakdown: store.setContextBreakdown,
    markTotalTokensSeen: store.markTotalTokensSeen,
    setLastCompaction: store.setLastCompaction,
    accumulatePartText: store.accumulatePartText,
    clearPartAccum: store.clearPartAccum,
    updateAgent: () => {},
    updateNodeStatus: () => {},
    setExecutionProgress: () => {},
    setGoal: () => {},
    updateGoal: () => {},
    addToast: (...args: [string, string]) => toasts.push(args),
    addErrorToastOnce: (_key: string, msg: string) => { toasts.push(['error', msg]); return true },
    patchSessionMeta: () => {},
    ...overrides,
  } as SSEContext
}

const toasts: unknown[] = []


beforeEach(() => {
  useChatStore.setState({
    messages: new Map(),
    streamingMessageId: null,
    streamingText: '',
    partTextAccumDelta: {},
    tokensUsed: new Map(),
    contextBreakdown: new Map(),
    totalTokensSeen: new Map(),
    lastCompaction: null,
  })
  toasts.length = 0
})

describe('sessionTotalTokens', () => {
  it('uses context_used when present and marks the session seen', () => {
    sessionTotalTokens(
      { context_used: 1024, total_tokens: 9999 },
      ctx()
    )
    const s = useChatStore.getState()
    expect(s.tokensUsed.get('sess-1')).toBe(1024)
    expect(s.totalTokensSeen.get('sess-1')).toBe(true)
  })

  it('falls back to total_tokens when context_used is missing', () => {
    sessionTotalTokens({ total_tokens: 500 }, ctx())
    expect(useChatStore.getState().tokensUsed.get('sess-1')).toBe(500)
  })

  it('does nothing when no usable number is provided', () => {
    sessionTotalTokens({}, ctx())
    expect(useChatStore.getState().tokensUsed.get('sess-1')).toBeUndefined()
  })

  it('ignores events with no sessionId', () => {
    sessionTotalTokens(
      { context_used: 100 },
      ctx({ sessionId: '' as never })
    )
    expect(useChatStore.getState().tokensUsed.get('sess-1')).toBeUndefined()
  })
})

describe('llmUsage', () => {
  it('does not double-count after sessionTotalTokens has been seen', () => {
    useChatStore.setState((s) => {
      const m = new Map(s.totalTokensSeen)
      m.set('sess-1', true)
      return { totalTokensSeen: m }
    })
    llmUsage(
      { prompt_tokens: 9999, total_tokens: 9999 },
      ctx()
    )
    expect(useChatStore.getState().tokensUsed.get('sess-1')).toBeUndefined()
  })

  it('uses prompt_tokens when total-tokens has not been seen', () => {
    llmUsage({ prompt_tokens: 512 }, ctx())
    expect(useChatStore.getState().tokensUsed.get('sess-1')).toBe(512)
  })

  it('prefers prompt_tokens > input_tokens > total_tokens', () => {
    llmUsage(
      { prompt_tokens: 100, input_tokens: 200, total_tokens: 300 },
      ctx()
    )
    expect(useChatStore.getState().tokensUsed.get('sess-1')).toBe(100)
  })

  it('ignores zero or missing values', () => {
    llmUsage({ prompt_tokens: 0, total_tokens: 0 }, ctx())
    expect(useChatStore.getState().tokensUsed.get('sess-1')).toBeUndefined()
  })
})

describe('compact', () => {
  it('sets lastCompaction on the chat store', () => {
    compact({ layer: 'context', iteration: 3 }, ctx())
    expect(useChatStore.getState().lastCompaction?.layer).toBe('context')
  })

  it('falls back to "unknown" when layer is missing', () => {
    compact({}, ctx())
    expect(useChatStore.getState().lastCompaction?.layer).toBe('unknown')
  })

  it('increments the agent compaction counter when agent_id is provided', () => {
    let updated: { id: string; updater: (a: Agent) => void } | null = null
    const updateAgent = (id: string, updater: (a: Agent) => void): void => {
      updated = { id, updater }
    }
    compact({ agent_id: 'a-1', layer: 'context' }, ctx({ updateAgent }))
    expect(updated).not.toBeNull()
    expect(updated!.id).toBe('a-1')
    const a = {} as Agent
    updated!.updater(a)
    expect(a.compaction_count).toBe(1)
  })
})

describe('agentDone', () => {
  it('clears the streaming message and active attempt', () => {
    const setStreamingMessage = vi.fn()
    const setActiveAttempt = vi.fn()
    agentDone({}, ctx({ setStreamingMessage, setActiveAttempt }))
    expect(setStreamingMessage).toHaveBeenCalledWith(null)
    expect(setActiveAttempt).toHaveBeenCalledWith(null)
  })

  it('does not throw when there are no messages', () => {
    expect(() => agentDone({}, ctx())).not.toThrow()
  })

  it('flags the session when the attempt ended with a question', () => {
    const setAskedUser = vi.fn()
    agentDone({ asked_user: true }, ctx({ setAskedUser }))
    expect(setAskedUser).toHaveBeenCalledWith('sess-1', true)
  })

  it('clears the flag when the attempt ended normally', () => {
    const setAskedUser = vi.fn()
    agentDone({ asked_user: false }, ctx({ setAskedUser }))
    expect(setAskedUser).toHaveBeenCalledWith('sess-1', false)
  })
})

describe('errorEvent', () => {
  it('pushes a toast with the error message and clears streaming', () => {
    const setStreamingMessage = vi.fn()
    const setActiveAttempt = vi.fn()
    errorEvent(
      { error: 'model timeout' },
      ctx({ setStreamingMessage, setActiveAttempt })
    )
    expect(toasts).toEqual([['error', 'model timeout']])
    expect(setStreamingMessage).toHaveBeenCalledWith(null)
    expect(setActiveAttempt).toHaveBeenCalledWith(null)
  })

  it('does not push a toast when no error string is provided', () => {
    errorEvent({}, ctx())
    expect(toasts).toEqual([])
  })

  it('dedupes historical error events by message_id (§9: SSE replay must not re-toast)', () => {
    // 用真实去重逻辑（Set）而非透传 mock，验证同一 message_id 的回放不重复提示
    const seen = new Set<string>()
    const once = (_key: string, msg: string) => {
      if (seen.has(_key)) return false
      seen.add(_key)
      toasts.push(['error', msg])
      return true
    }
    const c = ctx({ addErrorToastOnce: once })

    errorEvent({ message_id: '85dfb024', error: 'rate limited (429)' }, c)
    errorEvent({ message_id: '85dfb024', error: 'rate limited (429)' }, c) // 回放
    const rateLimitedToasts = toasts.filter(
      (t) => Array.isArray(t) && typeof t[1] === 'string' && t[1].includes('rate limited'),
    )
    expect(rateLimitedToasts.length).toBe(1)
  })
})

describe('contextBreakdown (R-014 H1)', () => {
  it('stores the 7 segments per session', () => {
    const segments = {
      messages: 100, system_prompt: 50, meta_user_context: 30, skills: 10,
      tool_prompt: 40, system_tool_schemas: 60, mcp_tool_schemas: 5,
    }
    contextBreakdown({ segments }, ctx())
    expect(useChatStore.getState().contextBreakdown.get('sess-1')).toEqual(segments)
  })

  it('ignores malformed segments', () => {
    contextBreakdown({ segments: { messages: 'bad', skills: 12 } }, ctx())
    const stored = useChatStore.getState().contextBreakdown.get('sess-1')
    expect(stored).toEqual({ skills: 12 })
  })

  it('does nothing without a session id', () => {
    contextBreakdown({ segments: { skills: 12 } }, ctx({ sessionId: null }))
    expect(useChatStore.getState().contextBreakdown.size).toBe(0)
  })
})
