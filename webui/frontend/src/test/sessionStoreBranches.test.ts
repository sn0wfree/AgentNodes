// useSessionStore · 未覆盖分支单元测试（§9 批 9）
//
// 补齐 session.ts 的低覆盖分支：
// - deleteSession：删除当前会话后的邻居切换 / 无会话时新建 / 删除失败抛错
// - runSearch：请求失败清空结果 / 过期响应丢弃（竞态）/ isSearching 复位
// - closeSession / switchSession 的边界

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useSessionStore } from '../stores/session'
import type { Session } from '../stores/session'

const makeSession = (id: string): Session => ({
  id,
  user_id: 'u1',
  title: `Session ${id}`,
  created_at: 0,
  updated_at: 0,
  starred: false,
  tags: [],
  message_count: 0,
  archived: false,
})

vi.mock('../api/client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    sse: vi.fn(),
  },
}))

import { api } from '../api/client'
const mockedApi = api as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
}

describe('useSessionStore — deleteSession 分支', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
      searchOpen: false,
      searchQuery: '',
    })
    vi.clearAllMocks()
    // 删除当前会话会触发 switchSession → loadMessages + loadSessionState。
    // 两者都走 api.get 但端点不同，需按 URL 返回各自形状的合法响应，
    // 否则 undefined.agents / undefined.messages 抛错被 catch 吞掉（控制台噪音）。
    mockedApi.get.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/messages')) {
        return Promise.resolve({ messages: [], has_more: false })
      }
      return Promise.resolve({ agents: [], workflow: null, goal: null })
    })
    mockedApi.post.mockResolvedValue({ messages: [], hits: [] })
  })

  it('removes the deleted session locally', async () => {
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      openSessionIds: ['a', 'b'],
      currentSessionId: 'a',
    })
    mockedApi.delete.mockResolvedValueOnce({})

    await useSessionStore.getState().deleteSession('a')

    expect(mockedApi.delete).toHaveBeenCalledWith('/chat/session/a')
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual(['b'])
    expect(useSessionStore.getState().openSessionIds).not.toContain('a')
  })

  it('rethrows when the delete request fails', async () => {
    useSessionStore.setState({
      sessions: [makeSession('a')],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    mockedApi.delete.mockRejectedValueOnce(new Error('boom'))
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    await expect(useSessionStore.getState().deleteSession('a')).rejects.toThrow('boom')
    // 失败时本地状态不变
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual(['a'])
    spy.mockRestore()
  })

  it('creates a fresh session when no tabs remain after deleting the last one', async () => {
    useSessionStore.setState({
      sessions: [makeSession('only')],
      openSessionIds: ['only'],
      currentSessionId: 'only',
    })
    mockedApi.delete.mockResolvedValueOnce({})
    const createSpy = vi.spyOn(useSessionStore.getState(), 'createNewSession')
      .mockResolvedValue(undefined)

    await useSessionStore.getState().deleteSession('only')

    expect(createSpy).toHaveBeenCalledWith('新会话')
    createSpy.mockRestore()
  })
})

describe('useSessionStore — runSearch 边界', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
      searchOpen: false,
      searchQuery: '',
      isSearching: false,
    })
    vi.clearAllMocks()
  })

  it('clears results and resets isSearching when the request fails', async () => {
    useSessionStore.setState({
      searchResults: [{
        session_id: 'x', session_title: '', message_id: '',
        role: 'user', snippet: '', score: 0, created_at: 0,
      }],
    })
    mockedApi.post.mockRejectedValueOnce(new Error('network'))
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    await useSessionStore.getState().runSearch('foo')

    expect(useSessionStore.getState().searchResults).toEqual([])
    expect(useSessionStore.getState().isSearching).toBe(false)
    spy.mockRestore()
  })

  it('discards a stale response when a newer query is in flight', async () => {
    let resolveFirst: (v: unknown) => void = () => {}
    const first = new Promise((res) => { resolveFirst = res })
    mockedApi.post.mockReturnValueOnce(first)
    mockedApi.post.mockResolvedValueOnce({ hits: [] })

    const p1 = useSessionStore.getState().runSearch('old')
    const p2 = useSessionStore.getState().runSearch('new')
    // 先完成旧的请求（应被丢弃），再完成新的
    resolveFirst({ hits: [{ session_id: 'stale', session_title: '', message_id: '', role: 'user', snippet: '', score: 0, created_at: 0 }] })
    await p1
    await p2

    expect(useSessionStore.getState().searchResults).toEqual([])
  })

  it('sets isSearching during the request and clears it afterwards', async () => {
    let resolveReq: (v: unknown) => void = () => {}
    mockedApi.post.mockReturnValueOnce(new Promise((res) => { resolveReq = res }))

    const pending = useSessionStore.getState().runSearch('foo')
    expect(useSessionStore.getState().isSearching).toBe(true)

    resolveReq({ hits: [] })
    await pending
    expect(useSessionStore.getState().isSearching).toBe(false)
  })
})
