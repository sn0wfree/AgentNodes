// system store unit tests — workspacePath / llm / modelInfo,
// plus fetchSystemInfo success + failure (silent fallback).

import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('../api/client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../api/client'
import { useSystemStore } from '../stores/system'

const mockGet = vi.mocked(api.get)
const mockPost = vi.mocked(api.post)

describe('useSystemStore', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSystemStore.setState({
      workspacePath: '',
      llm: { provider: '', model: '', configured: false, max_retries: 3 },
      modelInfo: null,
    })
  })

  it('starts with empty defaults', () => {
    const s = useSystemStore.getState()
    expect(s.workspacePath).toBe('')
    expect(s.llm.configured).toBe(false)
    expect(s.modelInfo).toBeNull()
  })

  it('populates workspace, llm, and model info on fetchSystemInfo', async () => {
    mockGet.mockResolvedValueOnce({
      workspace_path: '/tmp/ws',
      llm: { provider: 'openai', model: 'gpt-4', configured: true, max_retries: 3 },
      model_info: { provider: 'openai', model: 'gpt-4', models_dev_id: 'gpt-4' },
    } as never)

    await useSystemStore.getState().fetchSystemInfo()
    const s = useSystemStore.getState()
    expect(s.workspacePath).toBe('/tmp/ws')
    expect(s.llm.configured).toBe(true)
    expect(s.llm.model).toBe('gpt-4')
    expect(s.modelInfo?.model).toBe('gpt-4')
  })

  it('keeps defaults on fetchSystemInfo failure', async () => {
    mockGet.mockRejectedValueOnce(new Error('network down') as never)
    await useSystemStore.getState().fetchSystemInfo()
    const s = useSystemStore.getState()
    expect(s.workspacePath).toBe('')
    expect(s.llm.configured).toBe(false)
  })

  it('refreshModelInfo sends provider + model and stores the result', async () => {
    mockPost.mockResolvedValueOnce({ provider: 'openai', model: 'gpt-4o' } as never)
    const result = await useSystemStore
      .getState()
      .refreshModelInfo('openai', 'gpt-4o')
    expect(mockPost).toHaveBeenCalledWith('/system/model-info/refresh', {
      provider: 'openai', model: 'gpt-4o',
    })
    expect(result?.model).toBe('gpt-4o')
    expect(useSystemStore.getState().modelInfo?.model).toBe('gpt-4o')
  })

  it('refreshModelInfo omits empty provider/model from the body', async () => {
    mockPost.mockResolvedValueOnce({ provider: 'x', model: 'y' } as never)
    await useSystemStore.getState().refreshModelInfo()
    expect(mockPost).toHaveBeenCalledWith('/system/model-info/refresh', {})
  })

  it('refreshModelInfo returns null on API failure', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockPost.mockRejectedValueOnce(new Error('boom') as never)
    const result = await useSystemStore.getState().refreshModelInfo('a', 'b')
    expect(result).toBeNull()
    expect(consoleSpy).toHaveBeenCalled()
    consoleSpy.mockRestore()
  })
})