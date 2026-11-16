// T4.2a — CommandPalette. 13 commands across 4 categories, a fuzzy
// filter, keyboard navigation and per-command actions. The suite pins
// the filter scoring, the wrap-around navigation, and that every command
// actually invokes its target (nav routes, store toggles, session ops).

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const navigateMock = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('react-router-dom')
  return { ...actual, useNavigate: () => navigateMock }
})

import { CommandPalette } from '../components/common/CommandPalette'
import { useCommandPaletteStore } from '../stores/commandPalette'
import { useLayoutStore } from '../stores/layout'
import { useSessionStore } from '../stores/session'
import { useChatStore } from '../stores/chat'

const openPalette = () => useCommandPaletteStore.setState({ open: true })

const searchBox = () => screen.getByPlaceholderText('搜索命令、视图、操作...')

beforeEach(() => {
  navigateMock.mockReset()
  useCommandPaletteStore.setState({ open: false })
  useSessionStore.setState({
    currentSessionId: 'sess-1',
    searchOpen: false,
    sessions: [],
    openSessionIds: [],
  } as never)
  useChatStore.setState({ messages: new Map() })
})

describe('CommandPalette', () => {
  it('renders nothing when closed', () => {
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    expect(screen.queryByPlaceholderText('搜索命令、视图、操作...')).not.toBeInTheDocument()
  })

  it('lists every command grouped by category when open', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)

    // '导航' also appears in the footer hint, so allow duplicates.
    for (const label of ['视图', '操作', '导航']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
    for (const label of [
      '打开编排页', '切换右主区可见性', '切换到纯聊天模式', '切换到监控模式',
      '切换到专注模式', '新建会话', '刷新当前会话', '搜索消息',
      '打开 Chat', '监控首页', '设置',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    expect(screen.getByText('11 个命令')).toBeInTheDocument()
  })

  it('filters by label substring', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.change(searchBox(), { target: { value: '编排' } })

    expect(screen.getByText('打开编排页')).toBeInTheDocument()
    expect(screen.queryByText('新建会话')).not.toBeInTheDocument()
    expect(screen.getByText('1 个命令')).toBeInTheDocument()
  })

  it('matches on keywords as well as labels', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    // 'dashboard' only appears in the nav-home keywords.
    fireEvent.change(searchBox(), { target: { value: 'dashboard' } })
    expect(screen.getByText('监控首页')).toBeInTheDocument()
  })

  it('shows the empty state for a nonsense query', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.change(searchBox(), { target: { value: 'zzzz-nope' } })
    expect(screen.getByText('未找到匹配命令')).toBeInTheDocument()
    expect(screen.getByText('0 个命令')).toBeInTheDocument()
  })

  it('ArrowDown / ArrowUp move the selection with wrap-around', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    const box = searchBox()

    // Selected row is the only one carrying the arrow affordance.
    const selectedCount = () => document.querySelectorAll('.lucide-arrow-right').length
    expect(selectedCount()).toBe(1)

    fireEvent.keyDown(box, { key: 'ArrowDown' })
    expect(selectedCount()).toBe(1)

    // Wrap up from index 0 → last command.
    fireEvent.keyDown(box, { key: 'ArrowUp' })
    expect(selectedCount()).toBe(1)
  })

  it('Enter runs the selected command', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    // First command is 打开编排页 → navigate('/dag').
    fireEvent.keyDown(searchBox(), { key: 'Enter' })
    expect(navigateMock).toHaveBeenCalledWith('/dag')
    expect(useCommandPaletteStore.getState().open).toBe(false)
  })

  it('Enter after filtering runs the filtered first match', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.change(searchBox(), { target: { value: '设置' } })
    fireEvent.keyDown(searchBox(), { key: 'Enter' })
    expect(useLayoutStore.getState().settingsOpen).toBe(true)
  })

  it('clicking a command runs it and closes the palette', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.click(screen.getByText('监控首页'))
    expect(navigateMock).toHaveBeenCalledWith('/')
    expect(useCommandPaletteStore.getState().open).toBe(false)
  })

  it('the refresh command loads messages for the current session', async () => {
    const loadSpy = vi.fn().mockResolvedValue(undefined)
    useChatStore.setState({ loadMessages: loadSpy } as never)
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.click(screen.getByText('刷新当前会话'))
    await waitFor(() => expect(loadSpy).toHaveBeenCalledWith('sess-1'))
  })

  it('the refresh command is a no-op without a current session', async () => {
    const loadSpy = vi.fn().mockResolvedValue(undefined)
    useChatStore.setState({ loadMessages: loadSpy } as never)
    useSessionStore.setState({ currentSessionId: null } as never)
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.click(screen.getByText('刷新当前会话'))
    expect(loadSpy).not.toHaveBeenCalled()
  })

  it('the search command opens message search', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.click(screen.getByText('搜索消息'))
    expect(useSessionStore.getState().searchOpen).toBe(true)
  })

  it('the three work-mode commands set the layout mode', () => {
    // Each command closes the palette, so re-open and re-render between
    // assertions (Radix unmounts the content on close).
    const { unmount } = (() => {
      openPalette()
      return render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    })()
    fireEvent.click(screen.getByText('切换到纯聊天模式'))
    expect(useLayoutStore.getState().workMode).toBe('chat')
    unmount()

    openPalette()
    const second = render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.click(screen.getByText('切换到监控模式'))
    expect(useLayoutStore.getState().workMode).toBe('monitor')
    second.unmount()

    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.click(screen.getByText('切换到专注模式'))
    expect(useLayoutStore.getState().workMode).toBe('focus')
  })

  it('the new-session command creates a session', async () => {
    const createSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ createNewSession: createSpy } as never)
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    fireEvent.click(screen.getByText('新建会话'))
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith('新会话'))
  })

  it('resets the selection to the top whenever the query changes', () => {
    openPalette()
    render(<MemoryRouter><CommandPalette /></MemoryRouter>)
    const box = searchBox()
    fireEvent.keyDown(box, { key: 'ArrowDown' })
    fireEvent.keyDown(box, { key: 'ArrowDown' })
    fireEvent.change(box, { target: { value: '编排' } })
    // After the reset, Enter must hit the first (only) match.
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(navigateMock).toHaveBeenCalledWith('/dag')
  })
})
