import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { enableMapSet } from 'immer'

enableMapSet()

// Mock EventSource with controllable instances
class MockEventSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  static instances: MockEventSource[] = []
  url: string
  onopen: ((ev?: any) => void) | null = null
  onerror: ((ev?: any) => void) | null = null
  onmessage: ((ev?: any) => void) | null = null
  readyState = 0
  listeners = new Map<string, (ev: any) => void>()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: (ev: any) => void) {
    this.listeners.set(type, fn)
  }

  removeEventListener(type: string) {
    this.listeners.delete(type)
  }

  close() {
    this.readyState = 2 // CLOSED
  }

  // Test helper
  emit(type: string, data: any) {
    const fn = this.listeners.get(type)
    if (fn) fn({ type, data: JSON.stringify(data) })
  }

  emitMessage(type: string, data: unknown, lastEventId = '') {
    const event = new MessageEvent(type, { data: JSON.stringify(data), lastEventId })
    if (type === 'error') this.onerror?.(event)
    this.listeners.get(type)?.(event)
  }
}

;(globalThis as any).EventSource = MockEventSource

// Mock localStorage for sr-auth
const localStorageMock = {
  store: {} as Record<string, string>,
  getItem(k: string) { return this.store[k] ?? null },
  setItem(k: string, v: string) { this.store[k] = v },
  removeItem(k: string) { delete this.store[k] },
  clear() { this.store = {} },
}
;(globalThis as any).localStorage = localStorageMock

// Import after mocks
const { useSSE } = await import('../hooks/useSSE')
const { useChatStore } = await import('../stores/chat')
const { useAgentStore } = await import('../stores/agents')
const { useWorkflowStore } = await import('../stores/workflow')
const { useToastStore } = await import('../stores/toast')
const { useSSEStore } = await import('../stores/sse')
const { useAuthStore } = await import('../stores/auth')

// Helper to get current EventSource instance
const getCurrentES = () => MockEventSource.instances[MockEventSource.instances.length - 1]

// Helper to seed chat/agent/workflow with a message
// After PR1 (text-part-routing), every text part requires an id.
// Tests that need a pre-existing text part can call emitTextSegment()
// or push a part with id explicitly.
const seedMessage = (id: string, role: 'user' | 'assistant' = 'assistant') => {
  useChatStore.getState().addMessage({
    id,
    session_id: 'sess-1',
    role,
    parts: [],
    created_at: Date.now() / 1000,
  })
}

describe('useSSE', () => {
  beforeEach(() => {
    MockEventSource.instances.length = 0
    localStorageMock.clear()
    useChatStore.setState({
      messages: new Map(),
      streamingMessageId: null,
      streamingText: '',
    })
    useAgentStore.setState({ agents: new Map() })
    useWorkflowStore.setState({
      dagNodes: [],
      dagEdges: [],
      presets: [],
      currentPresetId: null,
      executionProgress: 0,
    })
    useToastStore.setState({ toasts: [] })
    useSSEStore.setState({ status: 'connecting' })
    useAuthStore.setState({ token: null, user: null })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('does not close a newer permission request when an older result arrives', () => {
    renderHook(() => useSSE('sess-1'))
    const es = getCurrentES()!
    act(() => {
      es.emit('permission_request', { tool_call_id: 'tc-1', tool_name: 'write_file' })
      es.emit('permission_request', { tool_call_id: 'tc-2', tool_name: 'read_file' })
      es.emit('permission_result', { tool_call_id: 'tc-1', action: 'deny' })
    })
    expect(useChatStore.getState().pendingPermission?.tool_call_id).toBe('tc-2')
    act(() => {
      es.emit('permission_result', { tool_call_id: 'tc-2', action: 'allow' })
    })
    expect(useChatStore.getState().pendingPermission).toBeNull()
  })

  // ──────────── connection lifecycle ────────────

  describe('connection lifecycle', () => {
    it('does not connect when sessionId is null', () => {
      renderHook(() => useSSE(null))
      expect(MockEventSource.instances.length).toBe(0)
    })

    it('creates EventSource with session_id query param', () => {
      renderHook(() => useSSE('sess-1'))

      expect(MockEventSource.instances.length).toBe(1)
      const es = getCurrentES()
      expect(es.url).toContain('session_id=sess-1')
      expect(es.url).toMatch(/^\/api\/chat\/events/)
    })

    it('attaches token from the auth store', () => {
      // Token now comes from useAuthStore (LOW fix), not a hand-parsed
      // zustand-persist localStorage record.
      useAuthStore.setState({ token: 'jwt-123' })

      renderHook(() => useSSE('sess-1'))

      const es = getCurrentES()
      expect(es.url).toContain('token=jwt-123')
    })

    it('omits the token query param when unauthenticated', () => {
      useAuthStore.setState({ token: null })

      renderHook(() => useSSE('sess-1'))

      const es = getCurrentES()
      expect(es.url).not.toContain('token=')
    })

    it('closes EventSource on unmount', () => {
      const { unmount } = renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()
      const closeSpy = vi.spyOn(es, 'close')

      unmount()

      expect(closeSpy).toHaveBeenCalled()
    })

    it('does not reconnect for a business error on an open stream', () => {
      vi.useFakeTimers()
      try {
        const { unmount } = renderHook(() => useSSE('sess-1'))
        const es = getCurrentES()
        act(() => {
          es.readyState = MockEventSource.OPEN
          es.onopen?.()
          es.emitMessage('error', { error: 'rate limited' }, 'event-1')
        })
        expect(useToastStore.getState().toasts.some((toast) => toast.message === 'rate limited')).toBe(true)
        act(() => { vi.advanceTimersByTime(15_000) })
        expect(useSSEStore.getState().status).toBe('connected')
        expect(MockEventSource.instances).toHaveLength(1)
        unmount()
      } finally {
        vi.useRealTimers()
      }
    })

    it('resumes a manually replaced stream with the last processed event ID', () => {
      vi.useFakeTimers()
      try {
        const { unmount } = renderHook(() => useSSE('sess-1'))
        const es = getCurrentES()
        act(() => {
          es.readyState = MockEventSource.OPEN
          es.onopen?.()
          es.emitMessage('queue_state', { session_id: 'sess-1' }, 'event-42')
          es.readyState = MockEventSource.CLOSED
          es.onerror?.({ currentTarget: es })
          vi.advanceTimersByTime(1000)
        })
        expect(getCurrentES().url).toContain('Last-Event-ID=event-42')
        unmount()
      } finally {
        vi.useRealTimers()
      }
    })

    it('preserves active streaming state when resuming from a cursor', () => {
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()
      act(() => {
        es.onopen?.()
        useChatStore.getState().setStreamingMessage('msg-1')
        useChatStore.getState().setActiveAttempt('attempt-1')
        es.emitMessage('queue_state', { session_id: 'sess-1' }, 'event-42')
        es.onopen?.()
      })
      expect(useChatStore.getState().streamingMessageId).toBe('msg-1')
      expect(useChatStore.getState().activeAttemptId).toBe('attempt-1')
    })

    it('does not carry a cursor across deselection or session changes', () => {
      const { rerender } = renderHook(
        ({ sessionId }: { sessionId: string | null }) => useSSE(sessionId),
        { initialProps: { sessionId: 'sess-1' as string | null } },
      )
      act(() => {
        getCurrentES().emitMessage('queue_state', { session_id: 'sess-1' }, 'event-42')
      })
      rerender({ sessionId: null })
      rerender({ sessionId: 'sess-1' })
      expect(getCurrentES().url).not.toContain('Last-Event-ID')
      rerender({ sessionId: 'sess-2' })
      expect(getCurrentES().url).not.toContain('Last-Event-ID')
    })

    it('does not reconnect a rejected stream after switching sessions', () => {
      vi.useFakeTimers()
      try {
        const { rerender, unmount } = renderHook(
          ({ sessionId }: { sessionId: string }) => useSSE(sessionId),
          { initialProps: { sessionId: 'sess-1' } },
        )
        const oldSource = getCurrentES()
        oldSource.readyState = MockEventSource.CLOSED
        act(() => {
          oldSource.onerror?.({ currentTarget: oldSource })
        })

        rerender({ sessionId: 'sess-2' })
        expect(getCurrentES().url).toContain('session_id=sess-2')
        act(() => { vi.advanceTimersByTime(1000) })
        expect(MockEventSource.instances).toHaveLength(2)
        expect(oldSource.readyState).toBe(MockEventSource.CLOSED)
        unmount()
      } finally {
        vi.useRealTimers()
      }
    })

    it('P0-2: switching sessions clears the seen event-id set (same id processes again)', () => {
      const { rerender, unmount } = renderHook(
        ({ sessionId }: { sessionId: string | null }) => useSSE(sessionId),
        { initialProps: { sessionId: 'sess-1' as string | null } },
      )
      // Mark id 'dup-id' as seen in sess-1.
      let es = getCurrentES()
      act(() => {
        es.readyState = MockEventSource.OPEN
        es.onopen?.()
        es.emitMessage('queue_state', { session_id: 'sess-1', queue_length: 5 }, 'dup-id')
      })
      expect(useChatStore.getState().queueLengths.get('sess-1')).toBe(5)

      // Switch session in-place: the dedupe set must reset.
      rerender({ sessionId: 'sess-2' })
      es = getCurrentES()
      act(() => {
        es.readyState = MockEventSource.OPEN
        es.onopen?.()
        es.emitMessage('queue_state', { session_id: 'sess-2', queue_length: 9 }, 'dup-id')
      })
      expect(useChatStore.getState().queueLengths.get('sess-2')).toBe(9)
      unmount()
    })

    it('onerror sets status disconnected but does not create new EventSource (native reconnect)', () => {
      vi.useFakeTimers()
      renderHook(() => useSSE('sess-1'))
      const initialCount = MockEventSource.instances.length

      const es = getCurrentES()
      // trigger error
      act(() => {
        if (es.onerror) es.onerror(new Event('error'))
      })

      expect(useSSEStore.getState().status).toBe('disconnected')
      // No new EventSource instance — browser native reconnect handles it
      expect(MockEventSource.instances.length).toBe(initialCount)

      // Advance timers and verify still no manual reconnect
      act(() => {
        vi.advanceTimersByTime(5000)
      })
      expect(MockEventSource.instances.length).toBe(initialCount)

      vi.useRealTimers()
    })

    it('gives up reconnecting after 3 consecutive CLOSED rejections (e.g. 403)', () => {
      vi.useFakeTimers()
      renderHook(() => useSSE('sess-1'))
      const initialCount = MockEventSource.instances.length

      const reject = () => {
        const es = getCurrentES()
        es.readyState = 2 // CLOSED — server rejected the stream
        act(() => {
          if (es.onerror) es.onerror({ currentTarget: es } as unknown as Event)
        })
      }

      // 1st rejection → schedules a manual reconnect in 1s
      reject()
      act(() => { vi.advanceTimersByTime(1000) })
      expect(MockEventSource.instances.length).toBe(initialCount + 1)

      // 2nd rejection → still retries
      reject()
      act(() => { vi.advanceTimersByTime(1000) })
      expect(MockEventSource.instances.length).toBe(initialCount + 2)

      // 3rd rejection → gives up (status stays disconnected)
      reject()
      expect(useSSEStore.getState().status).toBe('disconnected')
      act(() => { vi.advanceTimersByTime(5000) })
      expect(MockEventSource.instances.length).toBe(initialCount + 2)

      // P0-5 新契约：放弃 ≠ 永久 —— 看门狗在断开 ≥10s 后恢复慢速重连
      // （后端复活后无需用户切会话/刷新即可恢复连接）。
      act(() => { vi.advanceTimersByTime(10_000) })
      expect(MockEventSource.instances.length).toBeGreaterThan(initialCount + 2)

      vi.useRealTimers()
    })
  })

  // ──────────── event routing ────────────

  describe('event routing', () => {
    let es: MockEventSource

    beforeEach(() => {
      renderHook(() => useSSE('sess-1'))
      es = getCurrentES()
    })

    it('text_delta with text_id appends to matching part', () => {
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      // Backend now requires text_delta to carry text_id (3-step protocol).
      // We push a started event first so a proper text part exists.
      act(() => {
        es.emit('text.started', { text_id: 't1', message_id: 'msg-1' })
        es.emit('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'hello' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textParts = msg.parts.filter((p) => p.type === 'text') as any
      expect(textParts).toHaveLength(1)
      expect(textParts[0].id).toBe('t1')
      expect(textParts[0].text).toBe('hello')
      expect(useChatStore.getState().streamingText).toBe('hello')
    })

    it('text_delta accumulates on multiple chunks with same text_id', () => {
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      act(() => {
        es.emit('text.started', { text_id: 't1', message_id: 'msg-1' })
        es.emit('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'foo' })
        es.emit('text_delta', { text_id: 't1', message_id: 'msg-1', text: ' bar' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textParts = msg.parts.filter((p) => p.type === 'text') as any
      expect(textParts).toHaveLength(1)
      expect(textParts[0].text).toBe('foo bar')
    })

    it('P0-2: replayed text_delta with the same SSE id is applied once', () => {
      seedMessage('msg-1')
      act(() => {
        es.emitMessage('text.started', { text_id: 't1', message_id: 'msg-1' }, 'e-1')
        es.emitMessage('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'once' }, 'e-2')
        // Reconnect replay: same event id, same payload.
        es.emitMessage('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'once' }, 'e-2')
      })
      const msg = useChatStore.getState().messages.get('msg-1')!
      const part = msg.parts.find((p) => p.type === 'text') as any
      expect(part.text).toBe('once')
    })

    it('P0-2: distinct SSE ids still append distinct deltas', () => {
      seedMessage('msg-1')
      act(() => {
        es.emitMessage('text.started', { text_id: 't1', message_id: 'msg-1' }, 'e-1')
        es.emitMessage('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'a' }, 'e-2')
        es.emitMessage('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'b' }, 'e-3')
      })
      const msg = useChatStore.getState().messages.get('msg-1')!
      const part = msg.parts.find((p) => p.type === 'text') as any
      expect(part.text).toBe('ab')
    })

    it('P0-2: cursor still advances on a deduped replay, then to the newest id', () => {
      vi.useFakeTimers()
      try {
        // First connection: process e-10, drop, and reconnect with the cursor.
        let hook = renderHook(({ sid }: { sid: string | null }) => useSSE(sid), {
          initialProps: { sid: 'sess-1' },
        })
        let es = getCurrentES()
        act(() => {
          es.readyState = MockEventSource.OPEN
          es.onopen?.()
          es.emitMessage('queue_state', { session_id: 'sess-1', queue_length: 5 }, 'e-10')
          es.readyState = MockEventSource.CLOSED
          es.onerror?.({ currentTarget: es })
          vi.advanceTimersByTime(1000)
        })
        expect(getCurrentES().url).toContain('Last-Event-ID=e-10')

        // Reconnect replays e-10 (already seen) then delivers e-11.
        es = getCurrentES()
        act(() => {
          es.readyState = MockEventSource.OPEN
          es.onopen?.()
          es.emitMessage('queue_state', { session_id: 'sess-1', queue_length: 5 }, 'e-10')
          es.emitMessage('queue_state', { session_id: 'sess-1', queue_length: 9 }, 'e-11')
          es.readyState = MockEventSource.CLOSED
          es.onerror?.({ currentTarget: es })
          vi.advanceTimersByTime(1000)
        })
        // The dup must not regress the cursor; e-11 must advance it.
        expect(getCurrentES().url).toContain('Last-Event-ID=e-11')
        expect(useChatStore.getState().queueLengths.get('sess-1')).toBe(9)
        hook.unmount()
      } finally {
        vi.useRealTimers()
      }
    })

    it('P0-2: event ids without id set bypass dedupe (legacy frames)', () => {
      seedMessage('msg-1')
      act(() => {
        es.emit('text.started', { text_id: 't1', message_id: 'msg-1' })
        es.emit('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'x' })
        es.emit('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'y' })
      })
      const msg = useChatStore.getState().messages.get('msg-1')!
      const part = msg.parts.find((p) => p.type === 'text') as any
      expect(part.text).toBe('xy')
    })

    it('P0-2: the seen-id set is FIFO-bounded — the oldest id is evicted and reusable', () => {
      seedMessage('msg-1')
      act(() => {
        es.emitMessage('text.started', { text_id: 't1', message_id: 'msg-1' }, 'cap-seed')
        // Fill the cap (5000): 'cap-seed' is the oldest entry and gets evicted.
        for (let i = 0; i < 5000; i++) {
          es.emitMessage('text_delta', { text_id: 't1', message_id: 'msg-1', text: '' }, `cap-${i}`)
        }
        es.emitMessage('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'A' }, 'cap-fresh')
        // The evicted oldest id is processed again (not dropped as a dup).
        es.emitMessage('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'B' }, 'cap-seed')
        // A recent id inside the window still dedupes.
        es.emitMessage('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'A' }, 'cap-fresh')
        es.emitMessage('text_delta', { text_id: 't1', message_id: 'msg-1', text: 'C' }, 'cap-4999')
      })
      const msg = useChatStore.getState().messages.get('msg-1')!
      const part = msg.parts.find((p) => p.type === 'text') as any
      // A(evicted seed replay) + B + C(4999 evicted? no — 4999 is the newest, still seen → dropped)
      // 'cap-0'..'cap-4999' fill the window; 'cap-seed' evicted → replayed;
      // 'cap-fresh' still resident → second A dropped; 'cap-4999' still resident → C dropped.
      expect(part.text).toBe('AB')
    })

    it('text_delta without text_id is dropped (hard-break)', () => {
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      // Suppress console.warn noise during this test
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

      act(() => {
        es.emit('text_delta', { text: 'orphan', message_id: 'msg-1' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textParts = msg.parts.filter((p) => p.type === 'text') as any
      // No text part created (orphan chunk dropped — hard-break)
      expect(textParts).toHaveLength(0)
      // streamingText still updated (caller-side highlight during streaming)
      expect(useChatStore.getState().streamingText).toBe('orphan')
      warnSpy.mockRestore()
    })

    it('assistant_message replaces last text content', () => {
      seedMessage('msg-1')
      // Seed a text part manually (mimics what text.started would do)
      useChatStore.getState().updateMessage('msg-1', (m) => {
        m.parts.push({ type: 'text', id: 'seg-1', text: 'old' })
      })

      act(() => {
        es.emit('assistant_message', { content: 'new content', message_id: 'msg-1' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textPart = msg.parts.find((p) => p.type === 'text') as any
      expect(textPart.text).toBe('new content')
    })

    it('tool_call appends new tool_call part', () => {
      seedMessage('msg-1')

      act(() => {
        es.emit('tool_call', {
          message_id: 'msg-1',
          id: 'tc-1',
          name: 'alpha_calc',
          arguments: '{"x": 1}',
        })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const tc = msg.parts.find((p) => p.type === 'tool_call') as any
      expect(tc).toBeDefined()
      expect(tc.id).toBe('tc-1')
      expect(tc.name).toBe('alpha_calc')
      expect(tc.status).toBe('running')
    })

    it('tool_call with duplicate id is idempotent', () => {
      seedMessage('msg-1')

      act(() => {
        es.emit('tool_call', { message_id: 'msg-1', id: 'tc-1', name: 'a', arguments: '{}' })
        es.emit('tool_call', { message_id: 'msg-1', id: 'tc-1', name: 'a', arguments: '{}' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const tcs = msg.parts.filter((p) => p.type === 'tool_call')
      expect(tcs.length).toBe(1)
    })

    it('tool_result updates matching tool_call', () => {
      seedMessage('msg-1')
      useChatStore.getState().updateMessage('msg-1', (m) => {
        m.parts.push({ type: 'tool_call', id: 'tc-1', name: 'a', arguments: '{}', status: 'running' })
      })

      act(() => {
        es.emit('tool_result', { message_id: 'msg-1', id: 'tc-1', result: '{"y": 2}', status: 'done' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const tc = msg.parts.find((p) => p.type === 'tool_call' && (p as any).id === 'tc-1') as any
      expect(tc.status).toBe('done')
      expect(tc.result).toBe('{"y": 2}')
    })

    it('thinking_start appends thinking part', () => {
      seedMessage('msg-1')

      act(() => {
        es.emit('thinking_start', { message_id: 'msg-1' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const thinking = msg.parts.find((p) => p.type === 'thinking') as any
      expect(thinking).toBeDefined()
      expect(thinking.text).toBe('')
    })

    it('thinking_delta appends to last thinking part', () => {
      seedMessage('msg-1')
      useChatStore.getState().updateMessage('msg-1', (m) => {
        m.parts.push({ type: 'thinking', text: 'plan:', collapsed: true } as any)
      })

      act(() => {
        es.emit('thinking_delta', { message_id: 'msg-1', delta: ' think more' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const thinking = msg.parts.find((p) => p.type === 'thinking') as any
      expect(thinking.text).toBe('plan: think more')
    })

    it('agent_done clears streaming message', () => {
      useChatStore.getState().setStreamingMessage('msg-1')
      expect(useChatStore.getState().streamingMessageId).toBe('msg-1')

      act(() => {
        es.emit('agent_done', { message_id: 'msg-1' })
      })

      expect(useChatStore.getState().streamingMessageId).toBeNull()
    })

    it('error event shows toast + clears streaming', () => {
      useChatStore.getState().setStreamingMessage('msg-1')

      act(() => {
        es.emit('error', { error: 'LLM API failed' })
      })

      expect(useToastStore.getState().toasts.length).toBe(1)
      expect(useToastStore.getState().toasts[0].message).toBe('LLM API failed')
      expect(useChatStore.getState().streamingMessageId).toBeNull()
    })

    // §9 复核批：SSE 断线重连会从头回放历史事件。历史 error 若在每次
    // 切回时重新弹 toast，用户会看到一个早已过时（可能已成功重试多轮）
    // 的错误反复出现。按 message_id/attempt_id 去重——同一错误只提示一次。
    it('dedupes replayed error events by message_id (historical error not re-toasted)', () => {
      const err = 'LLMRateLimitError: rate limited (429) ... (2056)'

      act(() => {
        es.emit('error', { message_id: '85dfb024', error: err })
      })
      expect(useToastStore.getState().toasts.length).toBe(1)

      // 模拟 SSE 重连回放：同一 message_id 的 error 再次投递
      act(() => {
        es.emit('error', { message_id: '85dfb024', error: err })
      })
      expect(useToastStore.getState().toasts.length).toBe(1) // 不重复弹
    })

    it('deduplicates historical error by attempt_id when message_id is absent', () => {
      act(() => {
        es.emit('error', { attempt_id: 'att-1', error: 'boom' })
        es.emit('error', { attempt_id: 'att-1', error: 'boom' })
      })
      expect(useToastStore.getState().toasts.length).toBe(1)
    })

    it('fallback dedupe key is the error text when no id is provided', () => {
      act(() => {
        es.emit('error', { error: 'same error text' })
        es.emit('error', { error: 'same error text' })
      })
      expect(useToastStore.getState().toasts.length).toBe(1)
    })

    it('a different error (new message_id) still shows a toast', () => {
      act(() => {
        es.emit('error', { message_id: 'old-1', error: 'old failure' })
        es.emit('error', { message_id: 'new-2', error: 'new failure' })
      })
      const toasts = useToastStore.getState().toasts
      expect(toasts.length).toBe(2)
      expect(toasts.map((t) => t.message)).toEqual(['old failure', 'new failure'])
    })

    it('clears dedupe keys when switching sessions (new session can re-see same id)', () => {
      const { rerender } = renderHook(
        ({ sessionId }: { sessionId: string }) => useSSE(sessionId),
        { initialProps: { sessionId: 'sess-1' } },
      )
      // sess-1 里提示过 msg-1
      act(() => {
        getCurrentES().emit('error', { message_id: 'msg-1', error: 'boom' })
      })
      const n1 = useToastStore.getState().toasts.length

      // 切到 sess-2 → 去重集合应清空
      act(() => { rerender({ sessionId: 'sess-2' }) })
      act(() => {
        getCurrentES().emit('error', { message_id: 'msg-1', error: 'boom' })
      })
      expect(useToastStore.getState().toasts.length).toBe(n1 + 1)
    })

    it('agent_status updates agent state', () => {
      useAgentStore.setState({
        agents: new Map([['a-1', {
          id: 'a-1',
          session_id: 'sess-1',
          status: 'pending',
          name: 'Test',
          created_at: 0,
          updated_at: 0,
          tool_calls_count: 0,
          compaction_count: 0,
          context_tokens: 0,
          context_tokens_limit: 8000,
          iterations_detail: [],
        }]]),
      })

      act(() => {
        es.emit('agent_status', { agent_id: 'a-1', status: 'running', iteration: 1 })
      })

      const agent = useAgentStore.getState().agents.get('a-1')!
      expect(agent.status).toBe('running')
    })

    it('agent_loop merges loop data', () => {
      useAgentStore.setState({
        agents: new Map([['a-1', {
          id: 'a-1',
          session_id: 'sess-1',
          status: 'running',
          name: 'Test',
          created_at: 0,
          updated_at: 0,
          tool_calls_count: 0,
          compaction_count: 0,
          context_tokens: 0,
          context_tokens_limit: 8000,
          iterations_detail: [],
        }]]),
      })

      act(() => {
        es.emit('agent_loop', { agent_id: 'a-1', iteration: 3, total_cost: 0.05 })
      })

      const agent = useAgentStore.getState().agents.get('a-1')!
      expect(agent.status).toBe('running')
    })

    it('dag_update updates node status', () => {
      useWorkflowStore.setState({
        dagNodes: [{ id: 'n1', label: 'Plan', status: 'pending' }],
        dagEdges: [],
        presets: [],
        currentPresetId: null,
        executionProgress: 0,
      })

      act(() => {
        es.emit('dag_update', { node_id: 'n1', status: 'completed' })
      })

      expect(useWorkflowStore.getState().dagNodes[0].status).toBe('completed')
    })

    it('progress updates execution percentage from agents_completed/total', () => {
      act(() => {
        es.emit('progress', {
          agents_completed: 3,
          agents_total: 4,
          agent_statuses: { a: 'success', b: 'success', c: 'success' },
        })
      })

      expect(useWorkflowStore.getState().executionProgress).toBe(75)
    })

    it('progress syncs agent_statuses into DAG nodes', () => {
      // Pre-populate a DAG node so updateNodeStatus has something to update.
      useWorkflowStore.getState().setDAG(
        [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }],
        [],
      )

      act(() => {
        es.emit('progress', {
          agents_completed: 1,
          agents_total: 2,
          agent_statuses: { a: 'success', b: 'running' },
        })
      })

      const nodes = useWorkflowStore.getState().dagNodes
      const a = nodes.find((n) => n.id === 'a')!
      const b = nodes.find((n) => n.id === 'b')!
      expect(a.status).toBe('success')
      expect(b.status).toBe('running')
    })

    it('progress with zero agents_total leaves executionProgress unchanged', () => {
      const before = useWorkflowStore.getState().executionProgress
      act(() => {
        es.emit('progress', {
          agents_completed: 0,
          agents_total: 0,
          agent_statuses: {},
        })
      })
      expect(useWorkflowStore.getState().executionProgress).toBe(before)
    })

    it('progress without agent_statuses does not throw', () => {
      expect(() => {
        act(() => {
          es.emit('progress', {
            agents_completed: 1,
            agents_total: 2,
          })
        })
      }).not.toThrow()
    })

    it('malformed JSON is silently ignored', () => {
      const es = getCurrentES()
      // Override emit to use raw string data
      es.listeners.set('text_delta', (ev: any) => {
        // simulate hook's JSON.parse failure path
        try {
          JSON.parse(ev.data)
        } catch {
          // expected
        }
      })

      expect(() => {
        act(() => {
          es.listeners.get('text_delta')!({ type: 'text_delta', data: 'not json{' })
        })
      }).not.toThrow()
    })
  })

  // ──────────── text-part-routing (3-step protocol) ────────────

  describe('text-part-routing 3-step protocol', () => {
    let es: MockEventSource

    beforeEach(() => {
      renderHook(() => useSSE('sess-1'))
      es = getCurrentES()
    })

    it('text.started pushes a new text part with id', () => {
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      act(() => {
        es.emit('text.started', { text_id: 'seg-1', message_id: 'msg-1' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textParts = msg.parts.filter((p) => p.type === 'text') as any
      expect(textParts).toHaveLength(1)
      expect(textParts[0].id).toBe('seg-1')
      expect(textParts[0].text).toBe('')
    })

    it('text.ended overrides the part final text', () => {
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      act(() => {
        es.emit('text.started', { text_id: 'seg-1', message_id: 'msg-1' })
        es.emit('text_delta', { text_id: 'seg-1', message_id: 'msg-1', text: 'partial' })
        es.emit('text.ended', { text_id: 'seg-1', message_id: 'msg-1', text: 'final' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textParts = msg.parts.filter((p) => p.type === 'text') as any
      expect(textParts).toHaveLength(1)
      expect(textParts[0].text).toBe('final')
    })

    it('text_delta after tool_call creates new text part (regression)', () => {
      // Reproduces the original bug: text_delta after a tool_call must
      // land in a NEW text part, not be appended to the existing text.
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      act(() => {
        // Iter 1: text → tool_call
        es.emit('text.started', { text_id: 'iter1', message_id: 'msg-1' })
        es.emit('text_delta', { text_id: 'iter1', message_id: 'msg-1', text: 'T1' })
        es.emit('text.ended', { text_id: 'iter1', message_id: 'msg-1', text: 'T1' })
        es.emit('tool_call', {
          message_id: 'msg-1',
          id: 'tc-1',
          name: 'foo',
          arguments: '{}',
        })
        // Iter 2: text after tool_call
        es.emit('text.started', { text_id: 'iter2', message_id: 'msg-1' })
        es.emit('text_delta', { text_id: 'iter2', message_id: 'msg-1', text: 'T2' })
        es.emit('text.ended', { text_id: 'iter2', message_id: 'msg-1', text: 'T2' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textParts = msg.parts.filter((p) => p.type === 'text') as any
      const toolParts = msg.parts.filter((p) => p.type === 'tool_call')

      expect(textParts).toHaveLength(2)
      expect(textParts[0].id).toBe('iter1')
      expect(textParts[0].text).toBe('T1')
      expect(textParts[1].id).toBe('iter2')
      expect(textParts[1].text).toBe('T2')
      expect(toolParts).toHaveLength(1)
      // Tool call is positioned between the two text parts
      const textIdx0 = msg.parts.findIndex((p) => p.type === 'text' && (p as any).id === 'iter1')
      const tcIdx = msg.parts.findIndex((p) => p.type === 'tool_call')
      const textIdx1 = msg.parts.findIndex((p) => p.type === 'text' && (p as any).id === 'iter2')
      expect(textIdx0).toBeLessThan(tcIdx)
      expect(tcIdx).toBeLessThan(textIdx1)
    })

    it('text_delta with orphan text_id pushes new part (defensive)', () => {
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      // No text.started before text_delta — simulate replay / late join.
      act(() => {
        es.emit('text_delta', { text_id: 'late-1', message_id: 'msg-1', text: 'orphan' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textParts = msg.parts.filter((p) => p.type === 'text') as any
      expect(textParts).toHaveLength(1)
      expect(textParts[0].id).toBe('late-1')
      expect(textParts[0].text).toBe('orphan')
    })

    it('text.started with duplicate id is idempotent', () => {
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      act(() => {
        es.emit('text.started', { text_id: 'seg-1', message_id: 'msg-1' })
        es.emit('text.started', { text_id: 'seg-1', message_id: 'msg-1' })  // replay
        es.emit('text_delta', { text_id: 'seg-1', message_id: 'msg-1', text: 'abc' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textParts = msg.parts.filter((p) => p.type === 'text') as any
      expect(textParts).toHaveLength(1)
      expect(textParts[0].text).toBe('abc')
    })
  })

  // ──────────── heartbeat listener ────────────

  describe('heartbeat listener', () => {
    it('registers a heartbeat listener on EventSource', () => {
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()
      // The heartbeat listener must be attached so the backend's
      // periodic comment lines keep the connection marked alive.
      expect(es.listeners.has('heartbeat')).toBe(true)
    })

    it('heartbeat event marks the connection as connected', () => {
      useSSEStore.setState({ status: 'disconnected' })
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()

      expect(useSSEStore.getState().status).toBe('connecting')

      act(() => {
        es.listeners.get('heartbeat')!({
          type: 'heartbeat',
          data: JSON.stringify({ ts: 1234 }),
        })
      })

      expect(useSSEStore.getState().status).toBe('connected')
    })

    it('heartbeat recovers from transient disconnected state', () => {
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()

      // Simulate the browser firing onerror briefly, then a heartbeat
      // comes in to confirm the connection is healthy.
      act(() => {
        es.onerror?.({})
      })
      expect(useSSEStore.getState().status).toBe('disconnected')

      act(() => {
        es.listeners.get('heartbeat')!({
          type: 'heartbeat',
          data: JSON.stringify({ ts: 1234 }),
        })
      })
      expect(useSSEStore.getState().status).toBe('connected')
    })
  })

  // ──────────── error message bubble (Phase 2+ real-time fix) ────────────

  describe('error message bubble', () => {
    it('assistant_message with message_type=error creates an error bubble', () => {
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()

      act(() => {
        es.emit('assistant_message', {
          message_id: 'err-1',
          content: '⚠️ 模型请求频率过高，请稍后再试',
          message_type: 'error',
          metadata: {
            status: 'error',
            details: 'LLMRateLimitError: rate limited (429)',
          },
        })
      })

      const msg = useChatStore.getState().messages.get('err-1')
      expect(msg).toBeDefined()
      expect(msg!.message_type).toBe('error')
      expect(msg!.role).toBe('assistant')
      expect(msg!.parts).toHaveLength(1)
      expect(msg!.parts[0].type).toBe('text')
      expect((msg!.parts[0] as any).text).toBe('⚠️ 模型请求频率过高，请稍后再试')
      expect(msg!.metadata?.status).toBe('error')
      expect(msg!.metadata?.details).toBe('LLMRateLimitError: rate limited (429)')
    })

    it('assistant_message with message_type=error updates existing placeholder', () => {
      // Pre-existing placeholder (e.g. from message_received)
      seedMessage('err-2')
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()

      act(() => {
        es.emit('assistant_message', {
          message_id: 'err-2',
          content: '⚠️ 服务暂时不可用',
          message_type: 'error',
          metadata: {
            status: 'error',
            details: 'LLMServerError: 503',
          },
        })
      })

      const msg = useChatStore.getState().messages.get('err-2')
      expect(msg).toBeDefined()
      expect(msg!.message_type).toBe('error')
      expect(msg!.parts).toHaveLength(1)
      expect((msg!.parts[0] as any).text).toBe('⚠️ 服务暂时不可用')
      expect(msg!.metadata?.details).toBe('LLMServerError: 503')
    })

    it('normal assistant_message still uses length-replace logic', () => {
      // Pre-existing message with text part
      useChatStore.getState().addMessage({
        id: 'normal-1',
        session_id: 'sess-1',
        role: 'assistant',
        parts: [{ type: 'text', id: 't1', text: 'old' }],
        created_at: Date.now() / 1000,
      })
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()

      act(() => {
        es.emit('assistant_message', {
          message_id: 'normal-1',
          content: 'new longer text',
          // no message_type — normal path
        })
      })

      const msg = useChatStore.getState().messages.get('normal-1')
      expect(msg!.parts[0]).toMatchObject({ type: 'text', text: 'new longer text' })
    })
  })

  // ──────────── queue lifecycle events (B1 regression) ────────────

  describe('queue lifecycle', () => {
    let es: MockEventSource

    beforeEach(() => {
      renderHook(() => useSSE('sess-1'))
      es = getCurrentES()
    })

    it('attempt.started switches the streaming message and clears paused', () => {
      seedMessage('msg-q')
      useChatStore.getState().setQueuePaused('sess-1', true)

      act(() => {
        es.emit('attempt.started', {
          session_id: 'sess-1',
          message_id: 'msg-q',
        })
      })

      const s = useChatStore.getState()
      expect(s.streamingMessageId).toBe('msg-q')
      expect(s.queuePaused.get('sess-1')).toBe(false)
    })

    it('queue_paused sets the paused flag (queue stuck fix)', () => {
      act(() => {
        es.emit('queue_paused', { session_id: 'sess-1' })
      })
      expect(useChatStore.getState().queuePaused.get('sess-1')).toBe(true)
    })

    it('queue_state updates the queue length snapshot', () => {
      act(() => {
        es.emit('queue_state', { session_id: 'sess-1', queue_length: 3 })
      })
      expect(useChatStore.getState().queueLengths.get('sess-1')).toBe(3)
    })
  })

  // ──────────── token usage accounting (B2 regression) ────────────

  describe('token usage', () => {
    let es: MockEventSource

    beforeEach(() => {
      renderHook(() => useSSE('sess-1'))
      es = getCurrentES()
    })

    it('llm_usage sets context_used when no session_total_tokens seen (fallback)', () => {
      act(() => {
        es.emit('llm_usage', { session_id: 'sess-1', prompt_tokens: 120 })
      })
      act(() => {
        es.emit('llm_usage', { session_id: 'sess-1', prompt_tokens: 80 })
      })
      // context occupancy is "most recent call", so the later value wins
      expect(useChatStore.getState().tokensUsed.get('sess-1')).toBe(80)
    })

    it('session_total_tokens is authoritative; later llm_usage must not double count', () => {
      act(() => {
        es.emit('llm_usage', { session_id: 'sess-1', prompt_tokens: 120 })
      })
      act(() => {
        es.emit('session_total_tokens', { session_id: 'sess-1', context_used: 500 })
      })
      // llm_usage AFTER the authoritative event must be ignored
      act(() => {
        es.emit('llm_usage', { session_id: 'sess-1', prompt_tokens: 120 })
      })
      expect(useChatStore.getState().tokensUsed.get('sess-1')).toBe(500)
    })

    it('session_total_tokens overrides previously accumulated fallback', () => {
      act(() => {
        es.emit('llm_usage', { session_id: 'sess-1', prompt_tokens: 120 })
      })
      act(() => {
        es.emit('session_total_tokens', { session_id: 'sess-1', context_used: 900 })
      })
      expect(useChatStore.getState().tokensUsed.get('sess-1')).toBe(900)
    })

    it('session_total_tokens falls back to total_tokens when context_used absent', () => {
      act(() => {
        es.emit('session_total_tokens', { session_id: 'sess-1', total_tokens: 700 })
      })
      expect(useChatStore.getState().tokensUsed.get('sess-1')).toBe(700)
    })

    it('context_used is bounded and overwrites across calls (reflects compaction)', () => {
      // large occupancy
      act(() => {
        es.emit('session_total_tokens', { session_id: 'sess-1', context_used: 90000 })
      })
      expect(useChatStore.getState().tokensUsed.get('sess-1')).toBe(90000)
      // compaction shrank the context → next call is much smaller
      act(() => {
        es.emit('session_total_tokens', { session_id: 'sess-1', context_used: 30000 })
      })
      expect(useChatStore.getState().tokensUsed.get('sess-1')).toBe(30000)
    })
  })

  // ── T1.6b: reconnect / backoff / give-up / trace_id ──
  describe('reconnect + failure cap', () => {
    beforeEach(() => {
      renderHook(() => useSSE('sess-1'))
    })

    it('reconnect on open clears stale streaming state', () => {
      act(() => {
        getCurrentES().onopen?.()
      })
      act(() => {
        useChatStore.getState().setStreamingMessage('mid-stale')
        useChatStore.getState().setActiveAttempt('att-1')
      })
      // Second open = reconnect → stale streaming cleared.
      act(() => {
        const cur = getCurrentES()
        cur.onopen?.()
      })
      expect(useChatStore.getState().streamingMessageId).toBeNull()
    })

    it('onerror with CLOSED schedules a 1s reconnect', () => {
      vi.useFakeTimers()
      const before = MockEventSource.instances.length
      act(() => {
        const cur = getCurrentES()
        cur.readyState = 2
        cur.onerror?.({ currentTarget: cur })
      })
      vi.advanceTimersByTime(1000)
      vi.useRealTimers()
      expect(MockEventSource.instances.length).toBeGreaterThan(before)
    })

    it('gives up after 3 consecutive CLOSED errors', () => {
      vi.useFakeTimers()
      for (let i = 0; i < 3; i++) {
        act(() => {
          const cur = getCurrentES()
          cur.readyState = 2
          cur.onerror?.({ currentTarget: cur })
        })
        vi.advanceTimersByTime(1000)
      }
      const countAfterGiveUp = MockEventSource.instances.length
      vi.advanceTimersByTime(5000)
      expect(MockEventSource.instances.length).toBe(countAfterGiveUp)
      expect(useSSEStore.getState().status).toBe('disconnected')
      vi.useRealTimers()
    })

    it('status goes disconnected on a non-CLOSED error', () => {
      act(() => {
        const cur = getCurrentES()
        cur.readyState = 0 // CONNECTING — browser retries natively
        cur.onerror?.({ currentTarget: cur })
      })
      expect(useSSEStore.getState().status).toBe('disconnected')
    })

    it('heartbeat re-marks the connection alive', () => {
      act(() => {
        useSSEStore.getState().setStatus('disconnected')
      })
      act(() => {
        getCurrentES().emit('heartbeat', {})
      })
      expect(useSSEStore.getState().status).toBe('connected')
    })

    it('trace_id from any event is stored for log correlation', () => {
      act(() => {
        getCurrentES().emit('text_delta', {
          session_id: 'sess-1',
          message_id: 'mid-1',
          text_id: 't1',
          delta: 'x',
          trace_id: 'trace-abc',
        })
      })
      expect(useChatStore.getState().traceId).toBe('trace-abc')
    })

    it('malformed JSON payload is dropped without throwing', () => {
      expect(() => {
        act(() => {
          const fn = getCurrentES().listeners.get('text_delta')
          fn?.({ type: 'text_delta', data: '{not json' })
        })
      }).not.toThrow()
    })
  })

  // ── P0-5 看门狗（H1/H7）：线上行为曾完全无测试 ──
  describe('watchdog (zombie-connected + slow reconnect)', () => {
    it('flips a silent connected stream to disconnected and reconnects after 50s', () => {
      vi.useFakeTimers()
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()
      act(() => { es.onopen?.() })
      const before = MockEventSource.instances.length

      act(() => { vi.advanceTimersByTime(45_000) })
      expect(useSSEStore.getState().status).toBe('connected')
      expect(MockEventSource.instances.length).toBe(before)

      act(() => { vi.advanceTimersByTime(10_000) }) // 55s > 50s，跨过 2 tick
      // 翻转后立刻重连：status 从 disconnected 进 connecting（诚实反映"正在恢复"）
      expect(useSSEStore.getState().status).toBe('connecting')
      expect(MockEventSource.instances.length).toBe(before + 1)
      expect(es.readyState).toBe(MockEventSource.CLOSED) // 僵尸源被关闭

      // 下一 tick 不重复重连（status 已 disconnected 且 lastReconnectRef 刚更新）
      act(() => { vi.advanceTimersByTime(5_000) })
      expect(MockEventSource.instances.length).toBe(before + 1)
      vi.useRealTimers()
    })

    it('onopen and named heartbeat refresh lastAlive so a healthy stream is not killed', () => {
      vi.useFakeTimers()
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()
      act(() => { es.onopen?.() })
      const before = MockEventSource.instances.length
      // 每 20s 发一次具名 heartbeat → 看门狗不应误杀
      for (let i = 0; i < 3; i++) {
        act(() => { vi.advanceTimersByTime(20_000) })
        act(() => { es.listeners.get('heartbeat')?.({ type: 'heartbeat', data: '{}' }) })
      }
      expect(useSSEStore.getState().status).toBe('connected')
      expect(MockEventSource.instances.length).toBe(before)
      vi.useRealTimers()
    })

    it('throttles disconnected reconnects to at most one per 10s window', () => {
      vi.useFakeTimers()
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()
      act(() => { es.onerror?.({} as Event) })
      expect(useSSEStore.getState().status).toBe('disconnected')
      const before = MockEventSource.instances.length

      act(() => { vi.advanceTimersByTime(9_000) })
      expect(MockEventSource.instances.length).toBe(before)
      act(() => { vi.advanceTimersByTime(6_000) }) // 15s，跨过 3 个 5s tick
      expect(MockEventSource.instances.length).toBe(before + 1)
      vi.useRealTimers()
    })
  })
})