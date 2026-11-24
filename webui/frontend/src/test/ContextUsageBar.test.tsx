import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { ContextUsageBar } from '../components/chat/ContextUsageBar'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { useSystemStore } from '../stores/system'

const mockPost = vi.fn().mockResolvedValue({ status: 'done' })
vi.mock('../api/client', () => ({
  api: {
    post: (...args: unknown[]) => mockPost(...args),
  },
}))

const setup = (opts: {
  sessionId?: string | null
  tokensUsed?: number
  context?: number
  source?: 'bundled' | 'cached' | 'fetched' | 'fallback'
}) => {
  useSessionStore.setState({
    currentSessionId: opts.sessionId ?? 's1',
  } as any)
  useChatStore.setState({
    tokensUsed: new Map(
      opts.sessionId ? [[opts.sessionId, opts.tokensUsed ?? 0]] : []
    ),
  })
  useSystemStore.setState({
    modelInfo: opts.context
      ? {
          provider: 'minimax',
          model: 'minimax-M3',
          models_dev_id: 'minimax-cn-coding-plan',
          context_tokens: opts.context,
          max_output_tokens: 128000,
          supports_vision: true,
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
          source: opts.source ?? 'fetched',
          fetched_at: null,
        }
      : null,
  })
}

describe('ContextUsageBar', () => {
  beforeEach(() => {
    useChatStore.setState({
      tokensUsed: new Map(),
      streamingMessageId: null,
    })
    useSessionStore.setState({ currentSessionId: null } as any)
    useSystemStore.setState({ modelInfo: null })
    mockPost.mockClear()
  })

  it('renders nothing when modelInfo is missing', () => {
    setup({ sessionId: 's1', tokensUsed: 100, context: 0 })
    const { container } = render(<ContextUsageBar />)
    expect(container.firstChild).toBeNull()
  })

  it('renders token usage text with correct format', () => {
    setup({ sessionId: 's1', tokensUsed: 8200, context: 128000, source: 'fetched' })
    render(<ContextUsageBar />)
    const text = screen.getByTestId('context-usage-text')
    expect(text.textContent).toMatch(/8\.2K \/ 128\.0K/)
    expect(text.textContent).toMatch(/6\.4%/)
  })

  it('formats large numbers with M suffix', () => {
    setup({ sessionId: 's1', tokensUsed: 1_200_000, context: 2_000_000, source: 'fetched' })
    render(<ContextUsageBar />)
    const text = screen.getByTestId('context-usage-text')
    expect(text.textContent).toMatch(/1\.2M \/ 2\.0M/)
  })

  it('reflects compaction: usage drops after context is compressed', () => {
    const { rerender } = render(<ContextUsageBar />)
    // large occupancy → 90% red
    setup({ sessionId: 's1', tokensUsed: 90000, context: 100000, source: 'fetched' })
    rerender(<ContextUsageBar />)
    expect(screen.getByTestId('context-usage-text').textContent).toMatch(/90\.0%/)
    expect(screen.getByTestId('context-progress-bar').className).toContain('bg-danger')
    // compaction shrank context → 30% green
    setup({ sessionId: 's1', tokensUsed: 30000, context: 100000, source: 'fetched' })
    rerender(<ContextUsageBar />)
    expect(screen.getByTestId('context-usage-text').textContent).toMatch(/30\.0%/)
    expect(screen.getByTestId('context-progress-bar').className).toContain('bg-success')
  })

  it('applies green color tier for low usage', () => {
    setup({ sessionId: 's1', tokensUsed: 1000, context: 100000, source: 'fetched' })
    render(<ContextUsageBar />)
    const bar = screen.getByTestId('context-progress-bar')
    expect(bar.className).toContain('bg-success')
  })

  it('applies amber color tier for 50-80% usage', () => {
    setup({ sessionId: 's1', tokensUsed: 60000, context: 100000, source: 'fetched' })
    render(<ContextUsageBar />)
    const bar = screen.getByTestId('context-progress-bar')
    expect(bar.className).toContain('bg-warning')
  })

  it('applies red color tier for >=80% usage', () => {
    setup({ sessionId: 's1', tokensUsed: 85000, context: 100000, source: 'fetched' })
    render(<ContextUsageBar />)
    const bar = screen.getByTestId('context-progress-bar')
    expect(bar.className).toContain('bg-danger')
  })

  it('shows stale hint when source is fallback', () => {
    setup({ sessionId: 's1', tokensUsed: 1000, context: 100000, source: 'fallback' })
    render(<ContextUsageBar />)
    expect(screen.getByTestId('context-stale-hint')).toBeTruthy()
  })

  it('shows stale hint when source is bundled', () => {
    setup({ sessionId: 's1', tokensUsed: 1000, context: 100000, source: 'bundled' })
    render(<ContextUsageBar />)
    expect(screen.getByTestId('context-stale-hint')).toBeTruthy()
  })

  it('no stale hint when source is fetched', () => {
    setup({ sessionId: 's1', tokensUsed: 1000, context: 100000, source: 'fetched' })
    render(<ContextUsageBar />)
    expect(screen.queryByTestId('context-stale-hint')).toBeNull()
  })

  it('reads tokensUsed from the current sessionId', () => {
    useChatStore.setState({
      tokensUsed: new Map([
        ['s1', 500],
        ['s2', 99999],
      ]),
    })
    useSessionStore.getState().setCurrentSession('s2')
    useSystemStore.setState({
      modelInfo: {
        provider: 'minimax',
        model: 'minimax-M3',
        models_dev_id: 'minimax-cn-coding-plan',
        context_tokens: 100000,
        max_output_tokens: 128000,
        supports_vision: true,
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
      },
    })
    render(<ContextUsageBar />)
    // 99999 rounds to 100.0K via 99999/1000 = 99.999 → 100.0
    expect(screen.getByTestId('context-usage-text').textContent).toMatch(/100\.0K/)
  })

  // ── P41: auto-trigger /compact at 80% threshold ──

  it('does NOT call /chat/send_async below 80%', () => {
    setup({ sessionId: 'p41-low', tokensUsed: 60000, context: 100000 })
    render(<ContextUsageBar />)
    // Amber tier (50–80%) — no auto-trigger.
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('auto-calls /chat/send_async with /compact at >=80%', async () => {
    setup({ sessionId: 'p41-high', tokensUsed: 85000, context: 100000 })
    render(<ContextUsageBar />)
    // useEffect runs after render — flush.
    await act(async () => {})
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(mockPost).toHaveBeenCalledWith('/chat/send_async', {
      session_id: 'p41-high',
      content: '/compact',
    })
  })

  it('only fires auto-compact once per session', async () => {
    const sid = 'p41-once'
    setup({ sessionId: sid, tokensUsed: 85000, context: 100000 })
    const { rerender } = render(<ContextUsageBar />)
    await act(async () => {})
    expect(mockPost).toHaveBeenCalledTimes(1)

    // Re-render and bump usage — should NOT trigger again (per-
    // session throttle via the module-level autoCompactedSessions).
    mockPost.mockClear()
    useChatStore.setState({
      tokensUsed: new Map([[sid, 90000]]),
    })
    rerender(<ContextUsageBar />)
    await act(async () => {})
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('does NOT trigger auto-compact while a message is streaming', async () => {
    const sid = 'p41-streaming'
    setup({ sessionId: sid, tokensUsed: 85000, context: 100000 })
    useChatStore.setState({ streamingMessageId: 'm-streaming' })
    render(<ContextUsageBar />)
    await act(async () => {})
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('does NOT trigger when no session is selected', async () => {
    setup({ sessionId: null, tokensUsed: 85000, context: 100000 })
    render(<ContextUsageBar />)
    await act(async () => {})
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('triggers again when switching to a fresh session in red tier', async () => {
    // First session triggers once.
    setup({ sessionId: 'p41-a', tokensUsed: 85000, context: 100000 })
    const { rerender } = render(<ContextUsageBar />)
    await act(async () => {})
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(mockPost).toHaveBeenLastCalledWith('/chat/send_async', {
      session_id: 'p41-a',
      content: '/compact',
    })

    // Switch to a different session that's also red — should fire
    // (the throttle is per-session).
    mockPost.mockClear()
    setup({ sessionId: 'p41-b', tokensUsed: 90000, context: 100000 })
    rerender(<ContextUsageBar />)
    await act(async () => {})
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(mockPost).toHaveBeenLastCalledWith('/chat/send_async', {
      session_id: 'p41-b',
      content: '/compact',
    })
  })

  it('rolls back the throttle on POST failure', async () => {
    const sid = 'p41-rollback'
    mockPost.mockRejectedValueOnce(new Error('boom'))
    setup({ sessionId: sid, tokensUsed: 85000, context: 100000 })
    render(<ContextUsageBar />)
    await act(async () => {})
    expect(mockPost).toHaveBeenCalledTimes(1)

    // A second mount on the same session (e.g. context bump from
    // backend SSE) should retry. The catch handler deletes the
    // session id from the throttle set on failure.
    mockPost.mockResolvedValueOnce({ status: 'done' })
    act(() => {
      useChatStore.setState({ tokensUsed: new Map([[sid, 86000]]) })
    })
    render(<ContextUsageBar />)
    await act(async () => {})
    expect(mockPost).toHaveBeenCalledTimes(2)
  })
})
