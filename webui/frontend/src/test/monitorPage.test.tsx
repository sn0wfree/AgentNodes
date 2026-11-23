// T3.3 — MonitorPage integration. The dashboard aggregates agents,
// studies and runs, so the highest-value assertions are the KPI
// derivations (only ACTIVE studies count, only running agents count),
// the allSettled degradation, the 15s poll lifecycle, and navigation.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, render as rtlRender, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const navigateMock = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('react-router-dom')
  return { ...actual, useNavigate: () => navigateMock }
})

// H7：注入带 testid 的 stub，让「监控页无假状态 pill」断言真正可检测回归
vi.mock('../components/common/SSEStatus', () => ({
  SSEStatus: () => <div data-testid="sse-status" />,
}))
vi.mock('../hooks/useSSE', () => ({ useSSE: vi.fn() }))

vi.mock('../api/client', () => ({
  api: {
    study: { list: vi.fn() },
    run: { list: vi.fn() },
  },
}))

import { api } from '../api/client'
import { useSSE } from '../hooks/useSSE'
import { useSystemStore } from '../stores/system'
import { useSessionStore } from '../stores/session'
import { useAgentStore } from '../stores/agents'
import { useLayoutStore } from '../stores/layout'
import { MonitorPage } from '../pages/MonitorPage'

const render = (ui: import('react').ReactElement) =>
  rtlRender(<MemoryRouter>{ui}</MemoryRouter>)

const agent = (over: Record<string, unknown>) => ({
  id: 'a1', session_id: 'sess-1', status: 'running', name: 'A',
  created_at: 1, updated_at: 1, tool_calls_count: 0, compaction_count: 0,
  context_tokens: 0, context_tokens_limit: 1000, iterations_detail: [],
  ...over,
})

beforeEach(() => {
  navigateMock.mockReset()
  useSystemStore.setState({
    workspacePath: '/ws/demo',
    llm: { provider: 'glm', model: 'glm-5' },
  } as never)
  useSessionStore.setState({ currentSessionId: 'sess-1' } as never)
  useAgentStore.setState({ agents: new Map() })
  vi.mocked(api.study.list).mockReset()
  vi.mocked(api.run.list).mockReset()
  vi.mocked(api.study.list).mockResolvedValue({ studies: [] } as never)
  vi.mocked(api.run.list).mockResolvedValue({ runs: [] } as never)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('MonitorPage', () => {
  it('renders the LLM + workspace status strip', async () => {
    render(<MonitorPage />)
    await waitFor(() => expect(screen.getByText(/glm \/ glm-5/)).toBeInTheDocument())
    // The workspace path can appear in more than one place (status strip
    // plus an agent/run row), so assert presence rather than uniqueness.
    expect(screen.getAllByText('/ws/demo').length).toBeGreaterThan(0)
    // Q10: 监控页无 useSSE 订阅，不得渲染假连接状态 pill。
    expect(screen.queryByTestId('sse-status')).toBeNull()
expect(vi.mocked(useSSE)).not.toHaveBeenCalled()
  })

  it('counts only ACTIVE studies in the KPI (running/queued/monitoring)', async () => {
    vi.mocked(api.study.list).mockResolvedValue({
      studies: [
        { study_id: 's1', execution_status: 'running' },
        { study_id: 's2', execution_status: 'queued' },
        { study_id: 's3', execution_status: 'monitoring' },
        { study_id: 's4', execution_status: 'completed' },
        { study_id: 's5', execution_status: 'failed' },
      ],
    } as never)
    render(<MonitorPage />)
    await waitFor(() => expect(screen.getByText('活跃研究')).toBeInTheDocument())
    // The KPI value sits next to the label.
    const card = screen.getByText('活跃研究').parentElement!
    expect(card.querySelector('.font-mono')?.textContent).toBe('3')
  })

  it('counts only running agents in the KPI', async () => {
    useAgentStore.setState({
      agents: new Map([
        ['a1', agent({ id: 'a1', status: 'running' }) as never],
        ['a2', agent({ id: 'a2', status: 'completed' }) as never],
        ['a3', agent({ id: 'a3', status: 'failed' }) as never],
      ]),
    })
    render(<MonitorPage />)
    await waitFor(() => expect(screen.getByText('运行中 Agent')).toBeInTheDocument())
    const card = screen.getByText('运行中 Agent').parentElement!
    expect(card.querySelector('.font-mono')?.textContent).toBe('1')
  })

  it('reports the recent-run count', async () => {
    vi.mocked(api.run.list).mockResolvedValue({
      runs: [{ name: 'r1' }, { name: 'r2' }],
    } as never)
    render(<MonitorPage />)
    // Wait for the post-load value (loading shows '…').
    await waitFor(() => {
      const card = screen.getByText('最近 Run').parentElement!
      expect(card.querySelector('.font-mono')?.textContent).toBe('2')
    })
  })

  it('shows a failed run request instead of reporting an empty run list', async () => {
    vi.mocked(api.run.list).mockRejectedValueOnce(new Error('boom'))
    render(<MonitorPage />)
    expect(await screen.findByText('运行数据加载失败：boom')).toBeInTheDocument()
    expect(screen.queryByText('暂无运行记录')).not.toBeInTheDocument()
    const card = screen.getByText('最近 Run').parentElement!
    expect(card.querySelector('.font-mono')?.textContent).toBe('—')
  })

  it('shows a failed study request without hiding successful runs', async () => {
    vi.mocked(api.study.list).mockRejectedValueOnce(new Error('study unavailable'))
    vi.mocked(api.run.list).mockResolvedValueOnce({ runs: [{ name: 'r1' }] } as never)
    render(<MonitorPage />)
    expect(await screen.findByText('研究数据加载失败：study unavailable')).toBeInTheDocument()
    expect(screen.getByText('r1')).toBeInTheDocument()
    expect(screen.queryByText(/暂无进行中的研究/)).not.toBeInTheDocument()
  })

  it('shows that the workspace is not ready rather than loading indefinitely', async () => {
    useSystemStore.setState({ workspacePath: '' })
    render(<MonitorPage />)
    expect(await screen.findByText('运行数据加载失败：工作区尚未就绪')).toBeInTheDocument()
    expect(api.run.list).not.toHaveBeenCalled()
  })

  it('shows the empty state with a link into Chat', async () => {
    render(<MonitorPage />)
    await waitFor(() =>
      expect(screen.getByText(/暂无进行中的研究/)).toBeInTheDocument()
    )
    fireEvent.click(screen.getByText('Chat', { selector: 'span' }))
    expect(navigateMock).toHaveBeenCalledWith('/chat')
  })

  it('navigates to a study from the active-studies list', async () => {
    vi.mocked(api.study.list).mockResolvedValue({
      studies: [{
        study_id: 'st-7', execution_status: 'running',
        objective: '研究动量', strategy_name: 'momentum', current_round: 3,
      }],
    } as never)
    render(<MonitorPage />)
    await waitFor(() => expect(screen.getByText('研究动量')).toBeInTheDocument())
    fireEvent.click(screen.getByText('研究动量'))
    expect(navigateMock).toHaveBeenCalledWith('/study/st-7')
  })

  it('wires all four quick entries to their routes', async () => {
    render(<MonitorPage />)
    await waitFor(() => expect(screen.getByText('Chat')).toBeInTheDocument())
    for (const [label, route] of [
      ['Chat', '/chat'], ['Trace', '/trace'], ['因子库', '/factors'], ['策略库', '/strategies'],
    ] as const) {
      navigateMock.mockReset()
      // The quick-entry button wraps a title div with the same text.
      const btn = screen
        .getAllByText(label)
        .map((el) => el.closest('button'))
        .find((b) => b !== null)!
      fireEvent.click(btn)
      expect(navigateMock).toHaveBeenCalledWith(route)
    }
  })

  it('keeps the chat sidebar preference when navigating from the dashboard', async () => {
    useLayoutStore.setState({ sidebarOpen: true })
    render(<MonitorPage />)
    await screen.findByText(/暂无进行中的研究/)
    fireEvent.click(screen.getByTitle('Chat'))
    expect(useLayoutStore.getState().sidebarOpen).toBe(true)
  })

  it('still toggles the sidebar when Chat is already active', async () => {
    useLayoutStore.setState({ sidebarOpen: true })
    rtlRender(<MemoryRouter initialEntries={['/chat']}><MonitorPage /></MemoryRouter>)
    await screen.findByText(/暂无进行中的研究/)
    fireEvent.click(screen.getByTitle('Chat'))
    expect(useLayoutStore.getState().sidebarOpen).toBe(false)
    useLayoutStore.getState().setSidebarOpen(true)
  })

  it('polls every 15s and clears the interval on unmount', async () => {
    // Drive the interval with fake timers from the start, but let the
    // initial load resolve through microtasks so waitFor is not needed.
    vi.useFakeTimers()
    const { unmount } = render(<MonitorPage />)
    await act(() => vi.advanceTimersByTimeAsync(0))
    const callsAfterMount = vi.mocked(api.study.list).mock.calls.length
    expect(callsAfterMount).toBeGreaterThan(0)

    await act(() => vi.advanceTimersByTimeAsync(15000))
    expect(vi.mocked(api.study.list).mock.calls.length).toBeGreaterThan(callsAfterMount)

    const beforeUnmount = vi.mocked(api.study.list).mock.calls.length
    unmount()
    await vi.advanceTimersByTimeAsync(30000)
    expect(vi.mocked(api.study.list).mock.calls.length).toBe(beforeUnmount)
  })

  it('skips the study request when there is no current session', async () => {
    useSessionStore.setState({ currentSessionId: null } as never)
    render(<MonitorPage />)
    await waitFor(() => expect(api.run.list).toHaveBeenCalled())
    expect(api.study.list).not.toHaveBeenCalled()
    expect(await screen.findByText('请选择会话以查看研究')).toBeInTheDocument()
  })
})
