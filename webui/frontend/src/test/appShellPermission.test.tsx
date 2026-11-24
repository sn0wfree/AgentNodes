import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const mocks = vi.hoisted(() => ({
  respond: vi.fn(),
  loadSessions: vi.fn().mockResolvedValue(undefined),
  createNewSession: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../api/client', () => ({ api: { permission: { respond: mocks.respond } } }))
vi.mock('../api/agentSchemas', () => ({ loadAgentSchemas: vi.fn().mockResolvedValue(undefined) }))
vi.mock('../hooks/useSSE', () => ({ useSSE: vi.fn() }))
vi.mock('../hooks/useKeyboardShortcuts', () => ({ useKeyboardShortcuts: vi.fn() }))
vi.mock('../stores/session', () => {
  const state = {
    currentSessionId: null,
    loadSessions: mocks.loadSessions,
    createNewSession: mocks.createNewSession,
    openSessionIds: [],
    sessions: [],
  }
  return { useSessionStore: Object.assign((selector: (value: typeof state) => unknown) => selector(state), { getState: () => state }) }
})
vi.mock('../components/layout/IconNav', () => ({ IconNav: () => null }))
vi.mock('../components/layout/TopBar', () => ({ TopBar: () => null }))
vi.mock('../components/layout/MainSplit', () => ({ MainSplit: () => null }))
vi.mock('../components/layout/RightPanel', () => ({ RightPanel: () => null }))
vi.mock('../components/layout/SplitDivider', () => ({ SplitDivider: () => null }))
vi.mock('../components/chat/SessionSidebar', () => ({ SessionSidebar: () => null }))
vi.mock('../components/common/CommandPalette', () => ({ CommandPalette: () => null }))
vi.mock('../components/common/SearchModal', () => ({ SearchModal: () => null }))

import { AppShell } from '../components/layout/AppShell'
import { useChatStore } from '../stores/chat'
import { useToastStore } from '../stores/toast'

describe('AppShell permission response', () => {
  beforeEach(() => {
    mocks.respond.mockReset()
    mocks.loadSessions.mockReset().mockResolvedValue(undefined)
    mocks.createNewSession.mockClear()
    useToastStore.setState({ toasts: [] })
    useChatStore.setState({
      pendingPermission: {
        tool_call_id: 'tc-1',
        tool_name: 'write_file',
        args: {},
        pattern: '*.py',
        target: '/tmp/x.py',
      },
    })
  })

  it('shows a session loading failure without creating a new session', async () => {
    mocks.loadSessions.mockRejectedValueOnce(new Error('网络中断'))
    render(<MemoryRouter><AppShell /></MemoryRouter>)
    expect(await screen.findByText(/会话初始化失败：网络中断/)).toBeInTheDocument()
    expect(mocks.createNewSession).not.toHaveBeenCalled()
  })

  it('runs only one init when two shells mount concurrently (P0-5 重复会话守卫)', async () => {
    // 慢 loadSessions 让两个 mount 的 init 在时间上重叠；
    // module 级 shellInitInFlight 守卫必须让第二个直接退出。
    mocks.loadSessions.mockImplementation(
      () => new Promise<void>((resolve) => setTimeout(resolve, 20)),
    )
    render(
      <MemoryRouter>
        <AppShell />
        <AppShell />
      </MemoryRouter>,
    )
    await waitFor(() => expect(mocks.createNewSession).toHaveBeenCalledTimes(1))
    // 留出第二个 mount 若未守卫本应完成的延迟窗口
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 80))
    })
    expect(mocks.createNewSession).toHaveBeenCalledTimes(1)
  })

  it('keeps the request and shows an error when the gateway is unavailable', async () => {
    mocks.respond.mockResolvedValue({ status: 'no_gateway' })
    render(<MemoryRouter><AppShell /></MemoryRouter>)
    fireEvent.change(screen.getByTestId('permission-reason-input'), {
      target: { value: '会覆盖现有策略' },
    })
    fireEvent.click(screen.getByTestId('permission-deny-once'))

    expect(await screen.findByText(/权限请求失败/)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByTestId('permission-reason-input')).toHaveValue('会覆盖现有策略')
    expect(mocks.respond).toHaveBeenCalledWith({
      tool_call_id: 'tc-1',
      action: 'deny',
      permanent: false,
      reason: '会覆盖现有策略',
    })

    mocks.respond.mockResolvedValueOnce({ status: 'ok' })
    fireEvent.click(screen.getByTestId('permission-deny-once'))
    expect(await screen.findByText('已拒绝本次')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(mocks.respond).toHaveBeenLastCalledWith({
      tool_call_id: 'tc-1',
      action: 'deny',
      permanent: false,
      reason: '会覆盖现有策略',
    })
  })

  it('shows network failures in the dialog and permits retry', async () => {
    mocks.respond.mockRejectedValueOnce(new Error('网络中断'))
    render(<MemoryRouter><AppShell /></MemoryRouter>)
    fireEvent.click(screen.getByTestId('permission-allow-once'))
    expect(await screen.findByRole('alert')).toHaveTextContent('网络中断')
    expect(screen.getByTestId('permission-allow-once')).toBeEnabled()
  })

  it('reports an expired request and closes the dialog', async () => {
    mocks.respond.mockResolvedValueOnce({ status: 'expired' })
    render(<MemoryRouter><AppShell /></MemoryRouter>)
    fireEvent.click(screen.getByTestId('permission-allow-once'))
    expect(await screen.findByText(/权限请求已过期/)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('prevents duplicate submissions and preserves a newer request', async () => {
    let resolveResponse: (value: { status: string }) => void = () => {}
    mocks.respond.mockReturnValue(new Promise<{ status: string }>((resolve) => {
      resolveResponse = resolve
    }))
    render(<MemoryRouter><AppShell /></MemoryRouter>)
    fireEvent.change(screen.getByTestId('permission-reason-input'), {
      target: { value: '旧请求原因' },
    })
    fireEvent.click(screen.getByTestId('permission-deny-once'))
    expect(screen.getByTestId('permission-deny-once')).toBeDisabled()
    fireEvent.click(screen.getByTestId('permission-deny-once'))
    expect(mocks.respond).toHaveBeenCalledTimes(1)

    act(() => {
      useChatStore.setState({
        pendingPermission: {
          tool_call_id: 'tc-2',
          tool_name: 'read_file',
          args: {},
          pattern: '*',
          target: '/tmp/y.py',
        },
      })
    })
    await act(async () => { resolveResponse({ status: 'ok' }) })
    await waitFor(() => expect(screen.getByText('read_file')).toBeInTheDocument())
    expect(screen.getByTestId('permission-reason-input')).toHaveValue('')
    expect(screen.getByTestId('permission-allow-once')).toBeEnabled()
  })
})

// R-014：会话初始化失败自动重试上限 10 次（此前无限循环）。
describe('AppShell init retry cap', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.loadSessions.mockReset()
    mocks.createNewSession.mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('stops auto-retrying after 10 attempts and shows a manual reload', async () => {
    mocks.loadSessions.mockRejectedValue(new Error('服务不可达'))
    render(<MemoryRouter><AppShell /></MemoryRouter>)

    // 首次失败后进入「正在重试」；每次推进 1s 触发下一次 init。
    // 共 10 次尝试（首次 + 9 次定时重试），之后不再调度。
    for (let i = 0; i < 10; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    }
    // 再推进 10s，调用次数不应继续增加
    const callsAtCap = mocks.loadSessions.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(mocks.loadSessions.mock.calls.length).toBe(callsAtCap)
    expect(callsAtCap).toBe(10)

    // 超限后显示明确失败态（不再「正在重试」）
    expect(screen.getByText(/已重试 10 次仍未成功/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument()
  })
})
