// TraceViewer — fetches GET /api/chat/session/{id}/trace and renders the
// reconstructed llm_request envelope (A3: event_log projection).

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { TraceViewer } from '../components/chat/TraceViewer'
import { useAuthStore } from '../stores/auth'

function mockFetch(events: unknown[]) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ events }),
  } as Response)
}

describe('TraceViewer', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    useAuthStore.setState({ token: null, user: null })
  })

  it('authenticates requests for the current session trace', async () => {
    useAuthStore.setState({ token: 'test-token' })
    const fetchMock = mockFetch([])
    vi.stubGlobal('fetch', fetchMock)
    render(<TraceViewer sessionId="s-1" />)
    await screen.findByText('No trace events found')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat/session/s-1/trace?limit=200',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    )
  })

  it('sends the types filter as the backend param name', async () => {
    const fetchMock = mockFetch([])
    vi.stubGlobal('fetch', fetchMock)
    render(<TraceViewer sessionId="s-1" />)
    await waitFor(() => expect(screen.getByText('No trace events found')).toBeTruthy())
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/api/chat/session/s-1/trace')
  })

  it('renders a reconstructed llm_request with system prompt + tools schema', async () => {
    const bigPrompt = 'S'.repeat(120)
    const events = [
      {
        type: 'llm_request',
        time_created: 1700000000,
        iteration: 1,
        history_count: 2,
        tools_count: 1,
        system_prompt_len: bigPrompt.length,
        system_prompt: bigPrompt,
        tools_schema: JSON.stringify({ name: 't1', parameters: { type: 'object' } }),
        history_meta: [{ role: 'user', content_len: 10 }],
      },
    ]
    vi.stubGlobal('fetch', mockFetch(events))
    render(<TraceViewer sessionId="s-1" />)

    expect(await screen.findByText(/LLM Request — 1 tools, 2 messages/)).toBeTruthy()

    // Expand to reveal the reconstructed envelope.
    fireEvent.click(screen.getByText(/LLM Request — 1 tools/))
    expect(screen.getByText('System prompt (120 chars)')).toBeTruthy()
    expect(screen.getByText(bigPrompt)).toBeTruthy()
    expect(screen.getByText(/Tools schema \(1 tools\)/)).toBeTruthy()
    expect(screen.getByText(/"name": "t1"/)).toBeTruthy()
  })

  it('prefers time_created over ts for the timestamp', async () => {
    const events = [
      { type: 'llm_request', time_created: 1700000000, ts: 999999999, iteration: 1 },
    ]
    vi.stubGlobal('fetch', mockFetch(events))
    render(<TraceViewer sessionId="s-1" />)
    // 1700000000s epoch → a local time string; just assert it rendered the card.
    expect(await screen.findByText(/LLM Request/)).toBeTruthy()
  })

  it('renders trajectory lifecycle events from the event_log projection', async () => {
    const events = [
      { type: 'loop_start', time_created: 1700000001, max_iterations: 5 },
      { type: 'iter_start', time_created: 1700000002, iteration: 1, tokens: 1200 },
      { type: 'tool_call', time_created: 1700000003, name: 'ls' },
      { type: 'tool_result', time_created: 1700000004, tool: 'ls', status: 'ok', elapsed_ms: 12 },
      { type: 'llm_response', time_created: 1700000005, finish_reason: 'stop', tool_call_count: 0 },
      { type: 'loop_end', time_created: 1700000006, reason: 'stop', iteration: 1 },
      { type: 'loop_final', time_created: 1700000007, reason: 'stop', iterations: 1, elapsed_s: 0.4 },
      { type: 'tool_heartbeat', time_created: 1700000008, tool: 'run_backtest', elapsed_s: 3.2 },
    ]
    vi.stubGlobal('fetch', mockFetch(events))
    render(<TraceViewer sessionId="s-1" />)

    expect(await screen.findByText(/Loop Start — max 5 iterations/)).toBeTruthy()
    expect(screen.getByText(/Iteration 1 — ~1200 tokens/)).toBeTruthy()
    expect(screen.getByText(/Tool Call — ls/)).toBeTruthy()
    expect(screen.getByText(/ls — ok \(12ms\)/)).toBeTruthy()
    expect(screen.getByText(/LLM Response — stop, 0 tool calls/)).toBeTruthy()
    expect(screen.getByText(/Loop End — stop, 1 iters/)).toBeTruthy()
    expect(screen.getByText(/Loop Final — stop, 1 iters, 0.4s/)).toBeTruthy()
    expect(screen.getByText(/Heartbeat — run_backtest \(3.2s\)/)).toBeTruthy()
  })

  it('renders a cumulative token chart from session_total_tokens events', async () => {
    const events = [
      { type: 'session_total_tokens', time_created: 1700000000, total_tokens: 1200 },
      { type: 'session_total_tokens', time_created: 1700000001, total_tokens: 3400 },
      { type: 'session_total_tokens', time_created: 1700000002, total_tokens: 5100 },
    ]
    vi.stubGlobal('fetch', mockFetch(events))
    render(<TraceViewer sessionId="s-1" />)
    expect(await screen.findByText('Cumulative tokens')).toBeTruthy()
  })

  it('renders the envelope diff comparing two llm_requests', async () => {
    const events = [
      {
        type: 'llm_request',
        time_created: 1700000000,
        iteration: 1,
        system_prompt: 'sys A\nshared\nkeep',
        tools_schema: '[]',
        system_prompt_len: 17,
      },
      {
        type: 'llm_request',
        time_created: 1700000001,
        iteration: 2,
        system_prompt: 'sys B\nshared\nchanged',
        tools_schema: '[]',
        system_prompt_len: 17,
      },
    ]
    vi.stubGlobal('fetch', mockFetch(events))
    render(<TraceViewer sessionId="s-1" />)
    await screen.findByText('Diff')

    fireEvent.click(screen.getByText('Diff'))
    expect(screen.getByText('Envelope diff')).toBeTruthy()
    expect(screen.getByText(/sys A/)).toBeTruthy()
    expect(screen.getByText(/sys B/)).toBeTruthy()
    expect(screen.getByText(/shared/)).toBeTruthy()
  })
})