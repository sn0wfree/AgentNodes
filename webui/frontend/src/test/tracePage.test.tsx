// TracePage：历史运行回放入口（N2）
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { TracePage } from '../pages/TracePage'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => vi.fn() }
})
vi.mock('../components/layout/PageShell', () => ({
  PageShell: ({ title, children }: any) => <div><h1>{title}</h1>{children}</div>,
}))
vi.mock('../components/chat/TraceViewer', () => ({
  TraceViewer: ({ sessionId }: any) => <div data-testid="trace-viewer">{sessionId}</div>,
}))

import { useSessionStore } from '../stores/session'

describe('TracePage', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [
        { id: 'a', title: 'Alpha', created_at: 1, updated_at: 2, starred: false, tags: [], message_count: 1, archived: false },
        { id: 'b', title: 'Beta', created_at: 1, updated_at: 1, starred: false, tags: [], message_count: 0, archived: false },
      ],
      currentSessionId: 'a',
    } as never)
  })

  it('defaults to the current session and renders the trace viewer', () => {
    render(<MemoryRouter><TracePage /></MemoryRouter>)
    expect(screen.getByText('Trace')).toBeTruthy()
    expect(screen.getByTestId('trace-viewer').textContent).toBe('a')
  })

  it('offers every owned session in the selector', () => {
    render(<MemoryRouter><TracePage /></MemoryRouter>)
    const options = Array.from(document.querySelectorAll('#trace-session option'))
    expect(options.map((o) => o.textContent)).toEqual(['Alpha', 'Beta'])
  })

  it('shows an empty hint when there are no sessions', () => {
    useSessionStore.setState({ sessions: [], currentSessionId: null } as never)
    render(<MemoryRouter><TracePage /></MemoryRouter>)
    expect(screen.getByText('选择一条会话以查看其 Trace')).toBeTruthy()
  })
})
