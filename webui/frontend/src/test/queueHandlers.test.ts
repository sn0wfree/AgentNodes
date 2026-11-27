// hooks/sse/queueHandlers — message_received / attempt.started /
// queue_paused / queue_state covering the FIFO message queue and
// multicast-ready assistant placeholder creation.

import { describe, it, expect, beforeEach } from 'vitest'
import {
  messageReceived,
  attemptStarted,
  queuePaused,
  queueState,
} from '../hooks/sse/queueHandlers'
import { useChatStore } from '../stores/chat'
import type { SSEContext } from '../hooks/sse/types'

function ctx(overrides: Partial<SSEContext> = {}): SSEContext {
  const store = useChatStore.getState()
  return {
    sessionId: 'sess-1',
    state: {
      hasSeenTotalTokens: () => false,
      getMessages: () => Array.from(store.messages.values()),
      getMessage: (id) => store.messages.get(id),
      isQueuePaused: () => false,
      getTokensUsed: () => 0,
    },
    addMessage: store.addMessage,
    updateMessage: store.updateMessage,
    setStreamingMessage: store.setStreamingMessage,
    setStreamingText: store.setStreamingText,
    appendStreamingText: () => {},
    setActiveAttempt: () => {},
    setQueuePaused: store.setQueuePaused,
    setQueueLength: store.setQueueLength,
    setTokensUsed: () => {},
    markTotalTokensSeen: () => {},
    setLastCompaction: () => {},
    accumulatePartText: () => {},
    clearPartAccum: () => {},
    updateAgent: () => {},
    updateNodeStatus: () => {},
    setExecutionProgress: () => {},
    setGoal: () => {},
    updateGoal: () => {},
    addToast: () => {},
    addErrorToastOnce: (_key: string, _msg: string) => true,
    patchSessionMeta: () => {},
    ...overrides,
  } as SSEContext
}

beforeEach(() => {
  useChatStore.setState({
    messages: new Map(),
    streamingMessageId: null,
    streamingText: '',
    queueLengths: new Map(),
    queuePaused: new Map(),
    partTextAccumDelta: {},
  })
})

describe('messageReceived', () => {
  it('seeds the user message and the assistant placeholder when processing', () => {
    messageReceived(
      {
        user_message_id: 'u-1',
        assistant_message_id: 'a-1',
        content: 'hi',
        created_at: 100,
        status: 'processing',
      },
      ctx()
    )
    const messages = Array.from(useChatStore.getState().messages.values())
    const ids = messages.map((m) => m.id)
    void ids
    const user = useChatStore.getState().messages.get('u-1')!
    const assistant = useChatStore.getState().messages.get('a-1')!
    expect(user.role).toBe('user')
    expect(user.parts[0]).toMatchObject({ type: 'text', text: 'hi' })
    expect(user.created_at).toBe(100)
    expect(assistant.role).toBe('assistant')
    expect(assistant.metadata?.queue_status).toBe('processing')
    expect(useChatStore.getState().streamingMessageId).toBe('a-1')
  })

  it('updates an existing user message created_at (backend-authoritative)', () => {
    useChatStore.setState((s) => {
      const m = new Map(s.messages)
      m.set('u-1', {
        id: 'u-1',
        session_id: 'sess-1',
        role: 'user',
        parts: [],
        created_at: 1,
      })
      return { messages: m }
    })
    messageReceived(
      {
        user_message_id: 'u-1',
        assistant_message_id: 'a-1',
        content: 'hi',
        created_at: 200,
      },
      ctx()
    )
    expect(useChatStore.getState().messages.get('u-1')!.created_at).toBe(200)
  })

  it('does not switch streaming for queued messages (FIFO)', () => {
    messageReceived(
      {
        user_message_id: 'u-2',
        assistant_message_id: 'a-2',
        content: 'hi',
        status: 'queued',
        queue_position: 1,
        queue_length: 3,
      },
      ctx()
    )
    expect(useChatStore.getState().streamingMessageId).toBeNull()
    // Queue length is recorded for the banner.
    expect(useChatStore.getState().queueLengths.get('sess-1')).toBe(3)
  })

  it('no-ops on queue bookkeeping when there is no sessionId', () => {
    // No sessionId → no queue_length entry. Streaming flag is still
    // set because the placeholder message is created regardless of
    // sessionId (intentional: keeps the message list stable while
    // a session is being established).
    messageReceived(
      { user_message_id: 'u', assistant_message_id: 'a', content: 'x' },
      ctx({ sessionId: '' as never })
    )
    expect(useChatStore.getState().queueLengths.size).toBe(0)
  })
})

describe('attemptStarted', () => {
  it('switches streaming to the given message and clears the text buffer', () => {
    useChatStore.getState().setStreamingMessage('old-id')
    useChatStore.getState().setStreamingText('draft text')
    attemptStarted({ message_id: 'a-1' }, ctx())
    expect(useChatStore.getState().streamingMessageId).toBe('a-1')
    expect(useChatStore.getState().streamingText).toBe('')
  })

  it('clears queue_paused when the attempt kicks off', () => {
    // Wire isQueuePaused to read the live store flag.
    useChatStore.getState().setQueuePaused('sess-1', true)
    const live = ctx({
      state: {
        ...useChatStore.getState(),
        hasSeenTotalTokens: () => false,
        getMessages: () => [],
        getMessage: () => undefined,
        getTokensUsed: () => 0,
        isQueuePaused: (sid: string) =>
          useChatStore.getState().queuePaused.get(sid) === true,
      } as never,
    })
    attemptStarted({ message_id: 'a-1' }, live)
    expect(useChatStore.getState().queuePaused.get('sess-1')).toBe(false)
  })

  it('no-ops without message_id', () => {
    useChatStore.getState().setStreamingMessage('keep-this')
    attemptStarted({}, ctx())
    expect(useChatStore.getState().streamingMessageId).toBe('keep-this')
  })
})

describe('queuePaused / queueState', () => {
  it('queuePaused flips the session flag', () => {
    queuePaused({}, ctx())
    expect(useChatStore.getState().queuePaused.get('sess-1')).toBe(true)
  })

  it('queueState records the length when numeric', () => {
    queueState({ queue_length: 5 }, ctx())
    expect(useChatStore.getState().queueLengths.get('sess-1')).toBe(5)
  })

  it('queueState ignores non-numeric lengths', () => {
    queueState({ queue_length: 'lots' } as never, ctx())
    expect(useChatStore.getState().queueLengths.get('sess-1')).toBeUndefined()
  })
})
