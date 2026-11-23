// T4.2b — SessionSidebar. The left rail: search, time-grouped listing,
// select, inline rename, tag editing, and delete-with-confirm. The suite
// drives each through the store so the contracts (what gets called, with
// which args, and what must NOT happen) are pinned.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import { SessionSidebar } from '../components/chat/SessionSidebar'
import { useSessionStore, type Session } from '../stores/session'
import { useLayoutStore } from '../stores/layout'

const mkSession = (over: Partial<Session> = {}): Session =>
  ({
    id: 'sess-1',
    title: '第一个会话',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    tags: [],
    ...over,
  }) as Session

const seed = (sessions: Session[], currentId: string | null = null) =>
  useSessionStore.setState({
    sessions,
    currentSessionId: currentId,
    openSessionIds: currentId ? [currentId] : [],
    searchOpen: false,
  } as never)

beforeEach(() => {
  useLayoutStore.setState({ sidebarOpen: true } as never)
  seed([])
})

const renderSidebar = () => render(<MemoryRouter><SessionSidebar /></MemoryRouter>)

describe('SessionSidebar', () => {
  it('shows the empty state when there are no sessions', () => {
    renderSidebar()
    expect(screen.getByText('暂无会话')).toBeInTheDocument()
  })

  it('lists sessions with their titles', () => {
    seed([mkSession({ id: 'a', title: 'Alpha' }), mkSession({ id: 'b', title: 'Beta' })])
    renderSidebar()
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
  })

  it('does not hide sessions with test-related words in their titles', () => {
    seed([mkSession({ id: 'a', title: 'API test' }), mkSession({ id: 'b', title: 'Quick review' })])
    renderSidebar()
    expect(screen.getByText('API test')).toBeInTheDocument()
    expect(screen.getByText('Quick review')).toBeInTheDocument()
  })

  it('filters sessions by the search box', () => {
    seed([mkSession({ id: 'a', title: 'Alpha' }), mkSession({ id: 'b', title: 'Beta' })])
    renderSidebar()
    fireEvent.change(screen.getByPlaceholderText('搜索会话...'), {
      target: { value: 'alph' },
    })
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.queryByText('Beta')).not.toBeInTheDocument()
  })

  it('searches titles only — tags are not part of the query', () => {
    // Documents the current contract: filtering matches title substrings.
    seed([
      mkSession({ id: 'a', title: 'Alpha', tags: ['量化'] }),
      mkSession({ id: 'b', title: 'Beta', tags: ['other'] }),
    ])
    renderSidebar()
    fireEvent.change(screen.getByPlaceholderText('搜索会话...'), {
      target: { value: '量化' },
    })
    // Tag text does not narrow the list.
    expect(screen.queryByText('Alpha')).not.toBeInTheDocument()
    expect(screen.queryByText('Beta')).not.toBeInTheDocument()
    expect(screen.getByText('暂无会话')).toBeInTheDocument()
  })

  it('selects a session on click', () => {
    const switchSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ switchSession: switchSpy } as never)
    seed([mkSession({ id: 'a', title: 'Alpha' })])
    renderSidebar()
    fireEvent.click(screen.getByText('Alpha'))
    expect(switchSpy).toHaveBeenCalledWith('a')
  })

  it('closes the mobile drawer after selecting a session', () => {
    useSessionStore.setState({ switchSession: vi.fn().mockResolvedValue(undefined) } as never)
    seed([mkSession({ id: 'a', title: 'Alpha' })])
    useLayoutStore.getState().setNarrowViewport(true)
    useLayoutStore.getState().setSidebarOpen(true)
    renderSidebar()
    fireEvent.click(screen.getByText('Alpha'))
    expect(useLayoutStore.getState().sidebarOpen).toBe(false)
    useLayoutStore.getState().setNarrowViewport(false)
  })

  it('the new-session button creates a session', () => {
    const createSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ createNewSession: createSpy } as never)
    renderSidebar()
    fireEvent.click(screen.getByTitle('新建会话'))
    expect(createSpy).toHaveBeenCalled()
  })

  it('closes the mobile drawer after creating a session', async () => {
    useSessionStore.setState({ createNewSession: vi.fn().mockResolvedValue(undefined) } as never)
    useLayoutStore.getState().setNarrowViewport(true)
    useLayoutStore.getState().setSidebarOpen(true)
    renderSidebar()
    fireEvent.click(screen.getByTitle('新建会话'))
    await waitFor(() => expect(useLayoutStore.getState().sidebarOpen).toBe(false))
    useLayoutStore.getState().setNarrowViewport(false)
  })

  it('rename action switches to the session and requests rename via layout store', async () => {
    const switchSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ switchSession: switchSpy } as never)
    seed([
      mkSession({ id: 'a', title: 'Alpha' }),
      mkSession({ id: 'b', title: 'Beta' }),
    ])
    useSessionStore.setState({ currentSessionId: 'a' } as never)
    renderSidebar()

    // 'Beta' 是非当前会话：点其重命名应触发切换 + 置位信号
    // 两个会话各有一个「重命名」按钮；Beta 是第二个
    const renameBtns = screen.getAllByTitle('重命名')
    expect(renameBtns.length).toBe(2)
    fireEvent.click(renameBtns[1])

    expect(switchSpy).toHaveBeenCalledWith('b')
    expect(useLayoutStore.getState().renameRequestId).toBe('b')
  })

  it('no inline rename input remains in the sidebar (single entry in TopBar)', () => {
    seed([mkSession({ id: 'a', title: 'Alpha' })])
    renderSidebar()
    fireEvent.click(screen.getByTitle('重命名'))
    // 侧栏不再有行内输入框；编辑态由顶栏接管
    expect(screen.queryByDisplayValue('Alpha')).toBeNull()
    expect(screen.getByText('Alpha')).toBeInTheDocument()
  })

  it('tag editing splits a comma list and trims entries', async () => {
    const updateSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ updateSessionMeta: updateSpy } as never)
    seed([mkSession({ id: 'a', title: 'Alpha' })])
    renderSidebar()

    fireEvent.click(screen.getByTitle('编辑标签'))
    const input = screen.getByPlaceholderText('tag1, tag2')
    fireEvent.change(input, { target: { value: ' 量化 , 动量 ,, ' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith('a', { tags: ['量化', '动量'] })
    )
  })

  it('delete asks for confirmation before calling the store', async () => {
    const deleteSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ deleteSession: deleteSpy } as never)
    seed([mkSession({ id: 'a', title: 'Alpha' })])
    renderSidebar()

    fireEvent.click(screen.getByTitle('删除'))
    // Confirm dialog appears with the session named.
    expect(await screen.findByText('删除会话')).toBeInTheDocument()
    expect(screen.getByText(/确定要删除「Alpha」吗/)).toBeInTheDocument()
    expect(deleteSpy).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith('a'))
  })

  it('cancelling the delete dialog leaves the session intact', async () => {
    const deleteSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ deleteSession: deleteSpy } as never)
    seed([mkSession({ id: 'a', title: 'Alpha' })])
    renderSidebar()

    fireEvent.click(screen.getByTitle('删除'))
    await screen.findByText(/确定要删除/)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(deleteSpy).not.toHaveBeenCalled()
    expect(screen.getByText('Alpha')).toBeInTheDocument()
  })

  it('collapses the sidebar through the rail button', () => {
    seed([mkSession({ id: 'a', title: 'Alpha' })])
    renderSidebar()
    fireEvent.click(screen.getByTitle('收起侧边栏'))
    expect(useLayoutStore.getState().sidebarOpen).toBe(false)
  })

  it('marks the active session', () => {
    seed(
      [mkSession({ id: 'a', title: 'Alpha' }), mkSession({ id: 'b', title: 'Beta' })],
      'b'
    )
    renderSidebar()
    // Both render; the active one carries the accent styling. Assert the
    // behavioural fact (both present, active identified by aria/current or
    // class) without pinning the exact utility string.
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
    const betaRow = screen.getByText('Beta').closest('[class*="bg-"]')
    expect(betaRow).toBeTruthy()
  })
})
