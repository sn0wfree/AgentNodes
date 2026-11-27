import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useChatStore, type Message } from '../stores/chat'
import { enableMapSet } from 'immer'

enableMapSet()

vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}))

import { api } from '../api/client'
const mockedApi = api as unknown as { get: ReturnType<typeof vi.fn> }

function makeMessage(id: string, role: 'user' | 'assistant', createdAt: number): Message {
  return {
    id,
    session_id: 'sess-1',
    role,
    parts: [{ type: 'text' as const, id: `seed-${id}`, text: 'x' }],
    created_at: createdAt,
  }
}

describe('fetchSessionAttempts (reload recovery)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChatStore.setState({
      messages: new Map(),
      streamingMessageId: null,
      streamingText: '',
      activeAttemptId: null,
      partTextAccumDelta: {},
    })
  })

  it('restores streaming state for a running attempt whose message is materialized', async () => {
    useChatStore.getState().setMessages([makeMessage('asm-1', 'assistant', 100)])
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-1',
          message_id: 'asm-1',
          status: 'running',
          prompt: 'hi',
          created_at: '2026-01-01T00:00:00',
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    expect(s.streamingMessageId).toBe('asm-1')
    expect(s.activeAttemptId).toBe('att-1')
    expect(s.messages.has('asm-1')).toBe(true)
    // The materialized message keeps its persisted parts
    expect(s.messages.get('asm-1')?.parts).toHaveLength(1)
  })

  it('creates a placeholder when the running message is not yet materialized', async () => {
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-1',
          message_id: 'asm-fresh',
          status: 'running',
          prompt: 'hi',
          created_at: '2026-01-01T00:00:00',
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    expect(s.streamingMessageId).toBe('asm-fresh')
    const msg = s.messages.get('asm-fresh')
    expect(msg).toBeDefined()
    expect(msg?.role).toBe('assistant')
    // No queue metadata on a running placeholder
    expect(msg?.metadata?.queue_status).toBeUndefined()
  })

  it('rebuilds queued placeholders with position/length', async () => {
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-1',
          message_id: 'asm-running',
          status: 'running',
          prompt: 'first',
          created_at: '2026-01-01T00:00:00',
        },
        {
          attempt_id: 'att-2',
          message_id: 'asm-queued',
          status: 'queued',
          prompt: 'second',
          created_at: '2026-01-01T00:00:01',
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    expect(s.streamingMessageId).toBe('asm-running')
    const queued = s.messages.get('asm-queued')
    expect(queued?.metadata).toMatchObject({
      queue_status: 'queued',
      queue_position: 2,
      queue_length: 2,
    })
  })

  it('does nothing when there are no active attempts', async () => {
    mockedApi.get.mockResolvedValueOnce({ attempts: [] })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    expect(s.streamingMessageId).toBeNull()
    expect(s.activeAttemptId).toBeNull()
    expect(s.messages.size).toBe(0)
  })

  it('does not clobber existing queued message metadata on reload', async () => {
    useChatStore
      .getState()
      .setMessages([
        { ...makeMessage('asm-queued', 'assistant', 100), metadata: { queue_status: 'queued', queue_position: 2, queue_length: 2 } },
      ])
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-2',
          message_id: 'asm-queued',
          status: 'queued',
          prompt: 'second',
          created_at: '2026-01-01T00:00:01',
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    const queued = s.messages.get('asm-queued')
    expect(queued?.metadata).toMatchObject({
      queue_status: 'queued',
      queue_position: 1,
      queue_length: 1,
    })
    // single queued attempt → position updates to 1
    expect(useChatStore.getState().messages.size).toBe(1)
  })

  it('loadMessages re-attaches streaming state after a reload', async () => {
    mockedApi.get.mockResolvedValueOnce({
      messages: [makeMessage('asm-1', 'assistant', 100)],
      has_more: false,
    })
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-1',
          message_id: 'asm-1',
          status: 'running',
          prompt: 'hi',
          created_at: '2026-01-01T00:00:00',
        },
      ],
    })

    await useChatStore.getState().loadMessages('sess-1')

    expect(mockedApi.get).toHaveBeenCalledTimes(2)
    expect(useChatStore.getState().streamingMessageId).toBe('asm-1')
    expect(useChatStore.getState().activeAttemptId).toBe('att-1')
  })
})

// ── §9 复核修复：过时错误反复重现 ──────────────────────────────
//
// 现场：session「你好，你能做什么」里有一轮 429 配额失败（11-24），
// 之后用户又成功对话 3 轮。但每次切回该会话，那条错误气泡都会重新弹出。
//
// 根因：fetchSessionAttempts 把**历史** failed attempt 也转成 error 消息气泡，
// 而该失败在 DB 里本来就没有 error 消息（errCount=0）—— 纯前端每次加载
// 现场合成的产物。docstring 声明的意图只有「重建 streaming/queued 状态」。
//
// 修复：failed attempt 只在「它是本 session 最新一轮」时显示错误气泡。

describe('fetchSessionAttempts — 过时失败不重复显示', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChatStore.setState({
      messages: new Map(),
      streamingMessageId: null,
      streamingText: '',
      activeAttemptId: null,
      partTextAccumDelta: {},
    })
  })

  it('shows the error bubble for the latest failed attempt', async () => {
    // 该失败是最后一条消息 → 应显示
    useChatStore.getState().setMessages([makeMessage('asm-x', 'assistant', 1000)])
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-fail',
          message_id: 'asm-fail',
          status: 'failed',
          prompt: 'hi',
          error: 'LLMRateLimitError: rate limited (429)',
          created_at: new Date(1000 * 1000).toISOString(),
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const msg = useChatStore.getState().messages.get('asm-fail')
    expect(msg).toBeDefined()
    expect(msg?.metadata?.status).toBe('error')
  })

  it('suppresses a stale failed attempt when later messages exist', async () => {
    // 失败之后又有更晚的消息 → 已过时 → 不再显示
    useChatStore.getState().setMessages([
      makeMessage('asm-fail', 'assistant', 1000),
      makeMessage('asm-user2', 'user', 2000),
      makeMessage('asm-ok', 'assistant', 3000),
    ])
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-fail',
          message_id: 'asm-fail',
          status: 'failed',
          prompt: 'hi',
          error: 'LLMRateLimitError: rate limited (429)',
          created_at: new Date(1000 * 1000).toISOString(),
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const msg = useChatStore.getState().messages.get('asm-fail')
    // 消息本身仍在（DB 里的空 assistant 消息），但**不应**被改写成错误消息
    expect(msg?.metadata?.status).not.toBe('error')
  })

  it('does not re-add the error bubble on repeated loads (idempotent)', async () => {
    useChatStore.getState().setMessages([makeMessage('asm-fail', 'assistant', 1000)])
    const payload = {
      attempts: [
        {
          attempt_id: 'att-fail',
          message_id: 'asm-fail',
          status: 'failed',
          prompt: 'hi',
          error: 'boom',
          created_at: new Date(1000 * 1000).toISOString(),
        },
      ],
    }
    mockedApi.get.mockResolvedValueOnce(payload)
    await useChatStore.getState().fetchSessionAttempts('sess-1')
    mockedApi.get.mockResolvedValueOnce(payload)
    await useChatStore.getState().fetchSessionAttempts('sess-1')

    // 两次加载后状态一致：不会叠加消息，也不会重复改写已存在的消息
    const s = useChatStore.getState()
    expect(s.messages.size).toBe(1)
    // 已存在的消息保持原样（不覆盖其 parts/metadata）
    const parts = s.messages.get('asm-fail')?.parts ?? []
    expect(parts[0]?.type).toBe('text')
    expect(parts[0]?.type === 'text' ? parts[0].text : '').toBe('x')
  })
})
