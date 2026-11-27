// hooks/sse/textHandlers — covers the text and thinking streaming
// protocol end-to-end against a minimal in-memory chat store.

import { describe, it, expect, beforeEach } from 'vitest'
import {
  textStarted,
  textDelta,
  textEnded,
  thinkingStart,
  thinkingDelta,
  thinkingDone,
  thinkingEnd,
} from '../hooks/sse/textHandlers'
import { useChatStore } from '../stores/chat'
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
    setMessages: store.setMessages,
    addMessage: store.addMessage,
    updateMessage: store.updateMessage,
    applyTextDelta: store.applyTextDelta,
    setStreamingMessage: store.setStreamingMessage,
    setStreamingText: store.setStreamingText,
    appendStreamingText: store.appendStreamingText,
    setActiveAttempt: store.setActiveAttempt,
    cancelAttempt: async () => {},
    resumeQueue: async () => {},
    setQueuePaused: () => {},
    setQueueLength: () => {},
    setTokensUsed: () => {},
    markTotalTokensSeen: () => {},
    setHasMore: () => {},
    loadMoreMessages: async () => {},
    loadMessages: async () => {},
    fetchSessionAttempts: async () => {},
    clearMessages: store.clearMessages,
    accumulatePartText: store.accumulatePartText,
    clearPartAccum: store.clearPartAccum,
    clearAllPartAccum: store.clearAllPartAccum,
    updateAgent: () => {},
    setLastCompaction: () => {},
    addToast: () => {},
    addErrorToastOnce: (_key: string, _msg: string) => true,
    ...overrides,
  } as SSEContext
}

function seedAssistant(msgId: string): void {
  useChatStore.setState((s) => {
    const m = new Map(s.messages)
    m.set(msgId, {
      id: msgId,
      session_id: 'sess-1',
      role: 'assistant',
      parts: [],
      created_at: 0,
    } as never)
    return { messages: m }
  })
}

function getMessage(id: string) {
  return useChatStore.getState().messages.get(id)!
}

beforeEach(() => {
  useChatStore.setState({
    messages: new Map(),
    streamingMessageId: null,
    streamingText: '',
    partTextAccumDelta: {},
  })
})

describe('textHandlers — text protocol', () => {
  it('text.started seeds a streaming text part', () => {
    seedAssistant('m1')
    textStarted({ text_id: 't-1', message_id: 'm1' }, ctx())
    const msg = getMessage('m1')
    expect(msg.parts).toEqual([
      { type: 'text', id: 't-1', text: '', isStreaming: true },
    ])
  })

  it('text.started re-marks an existing part as streaming (replay)', () => {
    seedAssistant('m1')
    textStarted({ text_id: 't-1', message_id: 'm1' }, ctx())
    textStarted({ text_id: 't-1', message_id: 'm1' }, ctx())
    const msg = getMessage('m1')
    expect(msg.parts).toHaveLength(1)
    expect((msg.parts[0] as { isStreaming?: boolean }).isStreaming).toBe(true)
  })

  it('text.delta appends to the matching text part and the preview buffer', () => {
    seedAssistant('m1')
    const c = ctx()
    textStarted({ text_id: 't-1', message_id: 'm1' }, c)
    textDelta({ text_id: 't-1', message_id: 'm1', delta: 'hi' }, c)
    textDelta({ text_id: 't-1', message_id: 'm1', delta: ' there' }, c)
    const msg = getMessage('m1')
    expect((msg.parts[0] as { text: string }).text).toBe('hi there')
    // Preview buffer holds the latest delta.
    expect(useChatStore.getState().partTextAccumDelta['t-1']).toBe('hi there')
  })

  it('P0-1: text.delta performs exactly one store notification per chunk', () => {
    seedAssistant('m1')
    const c = ctx()
    textStarted({ text_id: 't-1', message_id: 'm1' }, c)
    let notifications = 0
    const unsub = useChatStore.subscribe(() => { notifications += 1 })
    try {
      textDelta({ text_id: 't-1', message_id: 'm1', delta: 'x' }, c)
      expect(notifications).toBe(1)
      expect(getMessage('m1').parts[0]).toMatchObject({ text: 'x' })
      expect(useChatStore.getState().partTextAccumDelta['t-1']).toBe('x')
      expect(useChatStore.getState().streamingText).toBe('x')
    } finally {
      unsub()
    }
  })

  it('text.delta without text_id is dropped silently (protocol error guard)', () => {
    seedAssistant('m1')
    const c = ctx()
    textDelta({ message_id: 'm1', delta: 'orphan' }, c)
    expect(getMessage('m1').parts).toEqual([])
  })

  it('text.ended overrides with the authoritative final text and clears streaming', () => {
    seedAssistant('m1')
    const c = ctx()
    textStarted({ text_id: 't-1', message_id: 'm1' }, c)
    textDelta({ text_id: 't-1', message_id: 'm1', delta: 'draft' }, c)
    textEnded({ text_id: 't-1', message_id: 'm1', text: 'FINAL' }, c)
    const msg = getMessage('m1')
    expect((msg.parts[0] as { text: string }).text).toBe('FINAL')
    expect((msg.parts[0] as { isStreaming?: boolean }).isStreaming).toBe(false)
    expect(useChatStore.getState().partTextAccumDelta['t-1']).toBeUndefined()
  })

  it('text.ended with empty finalText preserves the streamed text (regression B4)', () => {
    seedAssistant('m1')
    const c = ctx()
    textStarted({ text_id: 't-1', message_id: 'm1' }, c)
    textDelta({ text_id: 't-1', message_id: 'm1', delta: 'streamed' }, c)
    // No final text — only the end signal.
    textEnded({ text_id: 't-1', message_id: 'm1' }, c)
    const msg = getMessage('m1')
    expect((msg.parts[0] as { text: string }).text).toBe('streamed')
  })
})

describe('applyTextDelta (P0-1 atomic store action)', () => {
  it('creates an orphan part when text.started has not arrived (replay / late join)', () => {
    seedAssistant('m1')
    useChatStore.getState().applyTextDelta('m1', 't-late', 'late chunk')
    const msg = getMessage('m1')
    expect(msg.parts).toEqual([
      { type: 'text', id: 't-late', text: 'late chunk', isStreaming: true },
    ])
    expect(useChatStore.getState().partTextAccumDelta['t-late']).toBe('late chunk')
    expect(useChatStore.getState().streamingText).toBe('late chunk')
  })

  it('keeps the persisted part text raw while the preview buffer recovers spaces', () => {
    // Contract: chunk-boundary space recovery (shouldInsertSpaceBetween)
    // applies ONLY to partTextAccumDelta; the persisted part.text stays
    // exactly as the backend emitted it.
    seedAssistant('m1')
    const store = useChatStore.getState()
    store.applyTextDelta('m1', 't-1', 'Hi')
    store.applyTextDelta('m1', 't-1', 'there')
    expect((getMessage('m1').parts[0] as { text: string }).text).toBe('Hithere')
    expect(useChatStore.getState().partTextAccumDelta['t-1']).toBe('Hi there')
    // CJK / punctuation boundaries never trigger the insert.
    store.applyTextDelta('m1', 't-1', '。')
    expect(useChatStore.getState().partTextAccumDelta['t-1']).toBe('Hi there。')
  })

  it('updates preview buffer + legacy stream even when the message is unknown', () => {
    useChatStore.getState().applyTextDelta('missing-msg', 't-x', 'ghost')
    expect(useChatStore.getState().messages.has('missing-msg')).toBe(false)
    expect(useChatStore.getState().partTextAccumDelta['t-x']).toBe('ghost')
    expect(useChatStore.getState().streamingText).toBe('ghost')
  })

  it('routes by text_id: interleaved ids do not cross-contaminate parts', () => {
    seedAssistant('m1')
    const store = useChatStore.getState()
    store.applyTextDelta('m1', 't-a', 'A1')
    store.applyTextDelta('m1', 't-b', 'B1')
    store.applyTextDelta('m1', 't-a', 'A2')
    const parts = getMessage('m1').parts as Array<{ id: string; text: string }>
    expect(parts).toHaveLength(2)
    expect(parts.find((p) => p.id === 't-a')!.text).toBe('A1A2')
    expect(parts.find((p) => p.id === 't-b')!.text).toBe('B1')
  })

  it('text.ended clears the preview buffer entry created by applyTextDelta', () => {
    seedAssistant('m1')
    const c = ctx()
    useChatStore.getState().applyTextDelta('m1', 't-1', 'draft')
    expect(useChatStore.getState().partTextAccumDelta['t-1']).toBe('draft')
    textEnded({ text_id: 't-1', message_id: 'm1', text: 'FINAL' }, c)
    expect(useChatStore.getState().partTextAccumDelta['t-1']).toBeUndefined()
    expect((getMessage('m1').parts[0] as { text: string }).text).toBe('FINAL')
  })
})

describe('textHandlers — thinking protocol', () => {
  it('thinking_start pushes a fresh collapsed streaming thinking part', () => {
    seedAssistant('m1')
    thinkingStart({ message_id: 'm1' }, ctx())
    const msg = getMessage('m1')
    expect(msg.parts).toHaveLength(1)
    const p = msg.parts[0] as { type: string; text: string; collapsed: boolean; isStreaming: boolean }
    expect(p.type).toBe('thinking')
    expect(p.collapsed).toBe(true)
    expect(p.isStreaming).toBe(true)
  })

  it('thinking_delta appends to the last thinking part', () => {
    seedAssistant('m1')
    const c = ctx()
    thinkingStart({ message_id: 'm1' }, c)
    thinkingDelta({ message_id: 'm1', delta: 'reasoning ' }, c)
    thinkingDelta({ message_id: 'm1', delta: 'step' }, c)
    const msg = getMessage('m1')
    expect((msg.parts[0] as { text: string }).text).toBe('reasoning step')
  })

  it('thinking_done clears streaming, collapses the part, and clears preview buffer', () => {
    seedAssistant('m1')
    const c = ctx()
    thinkingStart({ message_id: 'm1' }, c)
    thinkingDelta({ message_id: 'm1', delta: 'thought' }, c)
    thinkingDone({ message_id: 'm1' }, c)
    const msg = getMessage('m1')
    const p = msg.parts[0] as { isStreaming: boolean; collapsed: boolean }
    expect(p.isStreaming).toBe(false)
    expect(p.collapsed).toBe(true)
    // The preview buffer for this part was cleared.
    const partId = 'think-m1-0'
    expect(useChatStore.getState().partTextAccumDelta[partId]).toBeUndefined()
  })

  it('thinking_end is equivalent to thinking_done', () => {
    seedAssistant('m1')
    const c = ctx()
    thinkingStart({ message_id: 'm1' }, c)
    thinkingDelta({ message_id: 'm1', delta: 'reasoning' }, c)
    thinkingEnd({ message_id: 'm1' }, c)
    const msg = getMessage('m1')
    const p = msg.parts[0] as { isStreaming: boolean; collapsed: boolean }
    expect(p.isStreaming).toBe(false)
    expect(p.collapsed).toBe(true)
  })

  it('thinking_start without message_id is a no-op', () => {
    useChatStore.setState((s) => {
      const m = new Map(s.messages)
      m.set('m1', {
        id: 'm1', session_id: 'sess-1', role: 'assistant',
        parts: [], created_at: 0,
      })
      return { messages: m }
    })
    thinkingStart({}, ctx())
    expect(getMessage('m1').parts).toEqual([])
  })

  // ── F5: think_id dedup + targeted routing ──

  it('thinking_start with a known think_id is deduped (SSE replay)', () => {
    seedAssistant('m1')
    const c = ctx()
    thinkingStart({ message_id: 'm1', think_id: 't1' }, c)
    thinkingDelta({ message_id: 'm1', delta: 'abc', think_id: 't1' }, c)
    // Replay of the same thinking_start — must NOT push a second block.
    thinkingStart({ message_id: 'm1', think_id: 't1' }, c)
    const msg = getMessage('m1')
    expect(msg.parts).toHaveLength(1)
    expect((msg.parts[0] as { text: string }).text).toBe('abc')
  })

  it('interleaved thinking blocks route deltas by think_id', () => {
    seedAssistant('m1')
    const c = ctx()
    thinkingStart({ message_id: 'm1', think_id: 'tA' }, c)
    thinkingDelta({ message_id: 'm1', delta: 'A1', think_id: 'tA' }, c)
    thinkingStart({ message_id: 'm1', think_id: 'tB' }, c)
    thinkingDelta({ message_id: 'm1', delta: 'B1', think_id: 'tB' }, c)
    thinkingDelta({ message_id: 'm1', delta: 'A2', think_id: 'tA' }, c)
    const msg = getMessage('m1')
    expect(msg.parts).toHaveLength(2)
    const pa = msg.parts[0] as { id?: string; text: string }
    const pb = msg.parts[1] as { id?: string; text: string }
    expect(pa.id).toBe('tA')
    expect(pa.text).toBe('A1A2')
    expect(pb.id).toBe('tB')
    expect(pb.text).toBe('B1')
  })
})
