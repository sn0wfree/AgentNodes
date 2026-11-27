// 定位：错误气泡究竟由哪条路径产生（§9 复核 · 真根因取证）
//
// 真实场景（session 748e50ba「你好，你能做什么」）：
//   DB 消息 85dfb024：assistant / 空 parts / message_type='assistant'
//   attempts 85dfb024 → failed + 429 错误
//   用户其后又成功对话 3 轮
//
// 本文件用这个真实数据复现，确定错误气泡的来源路径。
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useChatStore, type Message } from '../stores/chat'
import { enableMapSet } from 'immer'

enableMapSet()

vi.mock('../api/client', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
import { api } from '../api/client'
const mockedApi = api as unknown as { get: ReturnType<typeof vi.fn> }

const FAILED_MID = '85dfb024-8830-4d6c-bb71-f4f545cfd0d8'
const ERR = "LLMRateLimitError: rate limited (429): {'type': 'error', 'error': {'type': 'rate_limit_error', 'message': '已达到 Token Plan 用量上限 (2056)', 'http_code': '429'}}"

/** 真实 DB 消息：失败轮留下的一条空 assistant 消息 */
const dbFailedMsg = (): Message => ({
  id: FAILED_MID,
  session_id: 'sess-1',
  role: 'assistant',
  parts: [
    { type: 'thinking', text: '', collapsed: true } as never,
    { type: 'text', id: 'p1', text: '' },
  ],
  created_at: 1795497553.749,
})

describe('错误气泡来源定位', () => {
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

  it('attempts 路径：DB 里已存在该消息时，不会合成错误气泡（!has(mid) 守卫）', async () => {
    useChatStore.getState().setMessages([dbFailedMsg()])
    mockedApi.get.mockResolvedValueOnce({
      attempts: [{
        attempt_id: 'att-fail', message_id: FAILED_MID, status: 'failed',
        prompt: 'x', error: ERR, created_at: '2026-11-24T05:19:13.611638+00:00',
      }],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const m = useChatStore.getState().messages.get(FAILED_MID)
    // 关键断言：attempts 路径**不**产生 error 气泡（消息已存在 → 守卫跳过）
    expect(m?.metadata?.status).not.toBe('error')
    expect(m?.message_type).not.toBe('error')
  })

  it('attempts 路径：DB 里**没有**该消息时，才会合成错误气泡', async () => {
    mockedApi.get.mockResolvedValueOnce({
      attempts: [{
        attempt_id: 'att-fail', message_id: FAILED_MID, status: 'failed',
        prompt: 'x', error: ERR, created_at: '2026-11-24T05:19:13.611638+00:00',
      }],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const m = useChatStore.getState().messages.get(FAILED_MID)
    expect(m?.metadata?.status).toBe('error')
  })
})
